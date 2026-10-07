"""Model access for the compiler. Exactly ONE role: reasoning.

DYNAMIC_DB IS NOT A NARRATION SERVICE. It has one and only one reason to call
a model at all: resolving a business concept against a schema nobody has
described, and judging whether a query means what the rules say. Whatever
application embeds this engine is free to have its own separate "fast" /
narration model for its own purposes -- that model, and any routing between
the two, belongs entirely to that application, never to this one.

THERE IS NO SILENT FALLBACK. When the reasoning system fails, the compile
fails and the caller decides what to do (serve the last verified artifact,
report the capability unavailable). ``ALLOW_REASONING_FALLBACK`` plus
``REASONING_FALLBACK_MODEL`` exist for an operator who has explicitly decided
a degraded reasoning model is better than none; it is off by default, and
falling back is never silent -- see :func:`complete`.

PROVIDER- AND MODEL-AWARE, NOT TEMPERATURE-ONLY. See
:mod:`dynamic_db.llm_config` for the full parameter table and the family
(chat vs. reasoning-style) detection this module uses to decide which fields
a request may legally carry.

NOTHING SECRET IS EVER LOGGED. The key is read once per call, put in a header,
and never printed -- not in a log line, not in an error message, and never in a
prompt.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

import httpx

from dynamic_db import llm_config
from dynamic_db.config import get_settings

logger = logging.getLogger(__name__)

#: DYNAMIC_DB's one and only model role. Kept as a named constant (rather than
#: inlining the string "reasoning" everywhere) so a downstream application's
#: own usage tracker can key on it explicitly, the way this project's own
#: tests already do.
SYSTEM_REASONING = "reasoning"

#: Endpoint used when no base URL is configured. Both providers speak the
#: OpenAI-compatible chat-completions protocol.
_PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}

_RETRYABLE_STATUS_CODES = {408, 409, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 1.0
_MAX_RETRY_DELAY_SECONDS = 8.0


class ModelUnavailable(RuntimeError):
    """The model could not be reached, or refused. Never a reason to approve."""


@dataclass(frozen=True)
class ModelRoute:
    """Everything one call needs, resolved from configuration."""

    system: str
    model: str
    provider: str
    api_key: str
    base_url: str
    timeout_seconds: int
    max_tokens: int

    @property
    def configured(self) -> bool:
        return bool(self.api_key and self.model)


def route(system: str) -> ModelRoute:
    """Resolve one system to a concrete model. Only ``SYSTEM_REASONING`` exists."""
    if system != SYSTEM_REASONING:
        raise ValueError(
            f"Unknown model system {system!r}. DYNAMIC_DB has only {SYSTEM_REASONING!r}."
        )
    settings = get_settings()
    return ModelRoute(
        system=SYSTEM_REASONING,
        model=settings.reasoning_model or "",
        provider=settings.reasoning_provider or "",
        api_key=settings.reasoning_api_key or "",
        base_url=settings.reasoning_base_url or "",
        timeout_seconds=settings.reasoning_timeout_seconds,
        max_tokens=settings.reasoning_max_tokens,
    )


def _fallback_route() -> Optional[ModelRoute]:
    """The explicitly-configured degraded route, or None.

    Never used unless ``ALLOW_REASONING_FALLBACK`` is on -- see the module
    docstring. Only ``model`` changes; provider, key and timeout stay the
    primary route's own configuration.
    """
    settings = get_settings()
    if not settings.allow_reasoning_fallback or not settings.reasoning_fallback_model:
        return None
    primary = route(SYSTEM_REASONING)
    return ModelRoute(
        system=SYSTEM_REASONING,
        model=settings.reasoning_fallback_model,
        provider=primary.provider,
        api_key=primary.api_key,
        base_url=primary.base_url,
        timeout_seconds=primary.timeout_seconds,
        max_tokens=primary.max_tokens,
    )


def _endpoint(model_route: ModelRoute) -> str:
    base = model_route.base_url or _PROVIDER_BASE_URLS.get(
        (model_route.provider or "").lower()
    )
    if not base:
        raise ModelUnavailable(
            f"No endpoint configured for the {model_route.system} model system."
        )
    return base.rstrip("/") + "/chat/completions"


# --------------------------------------------------------------------------
# Usage accounting
# --------------------------------------------------------------------------
#
# DYNAMIC_DB NEVER REACHES OUTSIDE ITS OWN PACKAGE FOR THIS. Every real call's
# token cost is always recorded as a structured log event (see
# dynamic_db.logging_setup), which is enough on its own to answer "what did
# this cost" from the logs. A DOWNSTREAM APPLICATION that wants those numbers
# folded into its own expense tracker (an existing spreadsheet, a billing
# dashboard, anything) registers a callback via :func:`set_usage_sink` --
# dependency injection, not a hardcoded relative path into a sibling project.

UsageSink = Any  # Callable[..., None], kept loose to avoid a hard typing import cycle

_usage_sink: Optional[UsageSink] = None


def set_usage_sink(sink: Optional[UsageSink]) -> None:
    """Register (or clear, with ``None``) a callback for every real model call.

    Called with the same keyword arguments :func:`_record_usage` always
    logs: ``input_tokens``, ``output_tokens``, ``input_cache_tokens``,
    ``duration_seconds``, ``system``, ``node``, ``capability``, ``provider``,
    ``model``. A sink that raises never breaks a compile -- see
    :func:`_record_usage`.
    """
    global _usage_sink
    _usage_sink = sink


def _record_usage(
    *,
    model_route: ModelRoute,
    node: str,
    capability: str,
    usage: Optional[Dict[str, Any]],
    duration_seconds: float,
) -> None:
    """Log one structured usage event, and forward it to the injected sink, if any."""
    details = (usage or {}).get("prompt_tokens_details") or {}
    fields = {
        "input_tokens": (usage or {}).get("prompt_tokens"),
        "output_tokens": (usage or {}).get("completion_tokens"),
        "input_cache_tokens": details.get("cached_tokens"),
        "duration_seconds": round(duration_seconds, 3),
        "system": model_route.system,
        "node": node,
        "capability": capability,
        "provider": model_route.provider,
        "model": model_route.model,
    }
    logger.info(
        "model call: %s/%s spent %s input + %s output tokens in %.2fs",
        node,
        capability or "-",
        fields["input_tokens"],
        fields["output_tokens"],
        duration_seconds,
        extra={"extra_fields": {"event": "model_usage", **fields}},
    )
    if _usage_sink is None:
        return
    try:
        _usage_sink(**fields)
    except Exception:  # noqa: BLE001 - an injected sink must never break a compile
        logger.warning("dynamic: the registered usage sink raised", exc_info=True)


# --------------------------------------------------------------------------
# The call
# --------------------------------------------------------------------------


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return min(float(retry_after), _MAX_RETRY_DELAY_SECONDS)
        except ValueError:
            pass
    return min(_RETRY_BACKOFF_SECONDS * attempt, _MAX_RETRY_DELAY_SECONDS)


def _post(model_route: ModelRoute, payload: Dict[str, Any]) -> httpx.Response:
    last: Optional[httpx.Response] = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(
                _endpoint(model_route),
                headers={
                    "Authorization": f"Bearer {model_route.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=model_route.timeout_seconds,
            )
        except httpx.HTTPError as exc:
            logger.warning(
                "dynamic: %s model request failed (%s)", model_route.system, type(exc).__name__
            )
            raise ModelUnavailable(
                f"The {model_route.system} model could not be reached."
            ) from None
        if response.status_code < 400:
            return response
        last = response
        if response.status_code not in _RETRYABLE_STATUS_CODES or attempt == _MAX_ATTEMPTS:
            break
        delay = _retry_delay(response, attempt)
        logger.info(
            "dynamic: %s model returned HTTP %s on attempt %d/%d; retrying in %.1fs",
            model_route.system,
            response.status_code,
            attempt,
            _MAX_ATTEMPTS,
            delay,
        )
        time.sleep(delay)
    # Status only. A response body can echo the prompt back.
    status = last.status_code if last is not None else "unknown"
    logger.warning("dynamic: %s model returned HTTP %s", model_route.system, status)
    raise ModelUnavailable(f"The {model_route.system} model returned HTTP {status}.")


def _extract_text(body: Dict[str, Any]) -> str:
    message = body["choices"][0]["message"]
    text = (message.get("content") or "").strip()
    if not text:
        # Some reasoning models put the answer in a separate field.
        text = (message.get("reasoning_content") or "").strip()
    return text


def _build_payload(
    model_route: ModelRoute,
    system_prompt: str,
    user_prompt: str,
    temperature: float,
    json_object: bool,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "model": model_route.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    if llm_config.wants_temperature(model_route.model):
        payload["temperature"] = temperature
    payload.update(
        llm_config.payload_extras(
            model_route.model, max_tokens=model_route.max_tokens, json_object=json_object
        )
    )
    return payload


def complete(
    system_prompt: str,
    user_prompt: str,
    *,
    system: str,
    node: str,
    capability: str = "",
    temperature: float = 0.0,
    json_object: bool = False,
) -> str:
    """One completion. Returns the text, or raises :class:`ModelUnavailable`.

    ``json_object`` asks the provider to constrain its own decoding to JSON,
    which removes the whole class of failures where a verdict arrives wrapped
    in a code fence or prefaced by a sentence of commentary. A provider that
    does not support it is retried once without, so an older or local model
    still works exactly as it did.

    THE REQUEST BODY IS BUILT FOR THE CONFIGURED MODEL'S OWN FAMILY, not
    assumed to be temperature-only chat shape -- see
    :mod:`dynamic_db.llm_config`. A reasoning-family model never receives
    ``temperature`` or the penalty terms (the provider rejects them outright
    for that family); it receives ``max_completion_tokens`` in place of
    ``max_tokens``.
    """
    model_route = route(system)
    if not model_route.configured:
        raise ModelUnavailable(
            f"The {system} model system is not configured. Set its model and API key."
        )

    payload = _build_payload(model_route, system_prompt, user_prompt, temperature, json_object)

    started = time.perf_counter()
    try:
        response = _post(model_route, payload)
    except ModelUnavailable:
        if json_object:
            payload.pop("response_format", None)
            response = _post(model_route, payload)
        elif get_settings().allow_reasoning_fallback:
            fallback = _fallback_route()
            if fallback is not None and fallback.configured:
                logger.warning(
                    "dynamic: %s model unavailable - falling back to %s "
                    "(ALLOW_REASONING_FALLBACK is on). This is a configured, "
                    "logged degradation, never a silent one.",
                    system,
                    fallback.model,
                )
                model_route = fallback
                payload = _build_payload(
                    model_route, system_prompt, user_prompt, temperature, json_object
                )
                response = _post(model_route, payload)
            else:
                raise
        else:
            raise
    elapsed = time.perf_counter() - started

    try:
        body = response.json()
        text = _extract_text(body)
    except (ValueError, KeyError, IndexError, TypeError):
        logger.warning("dynamic: %s model returned an unreadable body", system)
        raise ModelUnavailable(
            f"The {system} model returned an unreadable response."
        ) from None

    _record_usage(
        model_route=model_route,
        node=node,
        capability=capability,
        usage=body.get("usage") if isinstance(body, dict) else None,
        duration_seconds=elapsed,
    )

    if not text:
        raise ModelUnavailable(f"The {system} model returned no text.")
    return text


_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> Optional[Dict[str, Any]]:
    """The first JSON object in ``text``, or ``None``.

    ``None`` is a real answer here, distinguishable from ``{}``. Every caller
    treats it as a failure and fails closed; defaulting to an empty dict would
    let a trailing comma silently turn a rejection into an approval, with
    nothing in the log to say it had happened.
    """
    if not text:
        return None
    candidates = [text.strip()]
    fenced = _FENCE.search(text)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


def status() -> Dict[str, Any]:
    """What the reasoning system would use, with nothing secret in it."""
    settings = get_settings()
    model_route = route(SYSTEM_REASONING)
    fallback = _fallback_route()
    out: Dict[str, Any] = {
        SYSTEM_REASONING: {
            "model": model_route.model,
            "provider": model_route.provider,
            "configured": model_route.configured,
            "model_family": llm_config.classify_model(model_route.model),
        },
        "allow_reasoning_fallback": settings.allow_reasoning_fallback,
        "reasoning_fallback_model": settings.reasoning_fallback_model,
        "fallback_configured": bool(fallback and fallback.configured),
        "usage_sink_registered": _usage_sink is not None,
    }
    return out

"""One model call for one agent role.

Reuses the classic client's transport (endpoint, retries, reasoning-model
request shape) rather than a second HTTP client, and logs every real call to
the same usage workbook -- with the agent role as its node, so the cost of
planning, writing and verifying can each be read on its own.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Dict, Optional, Tuple

from app.config.settings import get_settings
from app.services import llm_service as classic
from app.services.llm_service import LLMService, LLMUnavailable

logger = logging.getLogger(__name__)

_transport = LLMService.__new__(LLMService)  # transport only; no explanation cache


def chat(
    *,
    role: str,
    model: str,
    system: str,
    user: str,
    max_tokens: int,
    json_mode: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    """``(text, usage)`` for one call. Raises :class:`LLMUnavailable`."""
    settings = get_settings()
    if not settings.llm_api_key or not model:
        raise LLMUnavailable("The AI service is not configured (LLM_API_KEY / agent model).")

    payload: Dict[str, Any] = {
        "model": model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if classic._is_reasoning_model(model):
        payload["max_completion_tokens"] = max_tokens
    else:
        payload["temperature"] = settings.llm_temperature
        payload["max_tokens"] = max_tokens
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    started = time.perf_counter()
    response = _transport._post_with_retry(payload, settings)
    elapsed = time.perf_counter() - started
    try:
        body = response.json()
        message = body["choices"][0]["message"]
        text = (message.get("content") or message.get("reasoning_content") or "").strip()
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMUnavailable("The AI service returned an unreadable response.") from None
    if not text:
        raise LLMUnavailable("The AI service returned no text.")

    usage = body.get("usage") if isinstance(body, dict) else None
    usage = usage or {}
    if classic._usage_tracker is not None:
        try:
            details = usage.get("prompt_tokens_details") or {}
            classic._usage_tracker.log_usage(
                input_tokens=usage.get("prompt_tokens"),
                output_tokens=usage.get("completion_tokens"),
                input_cache_tokens=details.get("cached_tokens"),
                duration_seconds=elapsed,
                system="agent",
                node=f"agent_{role}",
                provider=settings.llm_provider,
                model=model,
            )
        except Exception:  # noqa: BLE001 - accounting must never break an answer
            logger.warning("Could not log agent LLM usage", exc_info=True)

    logger.info("agent %s answered with %s in %.1fs", role, model, elapsed)
    return text, {
        "model": model,
        "duration_s": round(elapsed, 2),
        "input_tokens": usage.get("prompt_tokens"),
        "output_tokens": usage.get("completion_tokens"),
    }


def parse_json(text: str) -> Optional[Dict[str, Any]]:
    """The first JSON object in ``text``, or None. Tolerates a fenced block."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except ValueError:
        return None
    return data if isinstance(data, dict) else None

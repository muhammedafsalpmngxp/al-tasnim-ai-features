"""What generation controls the configured reasoning model actually supports.

WHY THIS FILE EXISTS AT ALL. The rest of this project's model client
(:mod:`dynamic_db.llm`) used to be built around ``temperature`` as if it were
the only control an OpenAI-compatible Chat Completions endpoint offered. That
was already wrong for ordinary chat models -- ``top_p``, the two penalty
terms, ``seed``, ``response_format`` and more are all real, documented
controls -- and it becomes actively unsafe for the newer REASONING-style
model family (the ``o*`` and ``gpt-5*`` lines), which the OpenAI API rejects
``temperature``/``top_p``/the penalty terms FOR AT ALL, and which use
``max_completion_tokens`` in place of ``max_tokens`` and a ``reasoning_effort``
enum this project had no concept of.

HOW CAPABILITIES ARE DETERMINED. There is no live "list my capabilities"
endpoint on an OpenAI-compatible Chat Completions API -- providers do not
expose one, and this project's client is a plain ``httpx`` caller, not the
vendor SDK, so there is nothing to introspect at runtime. What CAN be done
honestly is: classify the configured MODEL NAME into the family the provider
documents (a "chat" model or a "reasoning" model), and apply that family's
documented parameter table. That is what this module does, and the
distinction is not decorative: sending ``temperature`` to a reasoning model is
a 400 from the provider, not a difference in output.

NOTHING HERE IS INVENTED. Every parameter below is one the OpenAI Chat
Completions API documents; nothing about a model's own token vocabulary,
weights or internal behaviour is fabricated. Where support genuinely cannot be
determined for an unrecognised model name, this module says so explicitly
(``supported = "unknown"``) rather than guessing either way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

FAMILY_CHAT = "chat"
FAMILY_REASONING = "reasoning"
FAMILY_UNKNOWN = "unknown"

#: Name patterns for the two OpenAI-documented model families that take
#: genuinely different parameter sets. A model name matching neither is
#: treated as unknown, never silently assumed to be "chat".
#:
#: DELIBERATELY STRICT. A model name that merely LOOKS like it might be a
#: reasoning model (a custom or internal alias such as "gpt-5.6-luna" -- a
#: decimal-versioned, word-suffixed name no real OpenAI release uses) is
#: matched as UNKNOWN, not guessed as reasoning-family. Guessing wrong in
#: that direction would silently start OMITTING temperature and the penalty
#: terms from a real request that a differently-shaped endpoint might still
#: expect -- changing what is actually sent on nothing but a name
#: resemblance. Only exact, documented OpenAI reasoning-family names match.
_REASONING_FAMILY_PATTERNS = (
    re.compile(r"^o[1-9](-|$)", re.IGNORECASE),  # o1, o3, o4-mini, ...
    re.compile(r"^gpt-5(-|$)", re.IGNORECASE),  # gpt-5, gpt-5-mini, gpt-5-nano
)
_CHAT_FAMILY_PATTERNS = (
    re.compile(r"^gpt-4", re.IGNORECASE),
    re.compile(r"^gpt-3\.5", re.IGNORECASE),
    re.compile(r"^chatgpt-", re.IGNORECASE),
)


def classify_model(model: str) -> str:
    """FAMILY_CHAT, FAMILY_REASONING, or FAMILY_UNKNOWN for an unrecognised name."""
    name = (model or "").strip()
    if not name:
        return FAMILY_UNKNOWN
    if any(p.match(name) for p in _REASONING_FAMILY_PATTERNS):
        return FAMILY_REASONING
    if any(p.match(name) for p in _CHAT_FAMILY_PATTERNS):
        return FAMILY_CHAT
    return FAMILY_UNKNOWN


@dataclass(frozen=True)
class ParamSpec:
    """One generation control, and everything an operator needs to know
    about it -- this is the exact shape the ``llm-config --explain`` CLI
    command prints."""

    name: str
    purpose: str
    kind: str  # "numeric" | "boolean" | "enum" | "string" | "object"
    supported_families: tuple  # families that accept this parameter at all
    legal_zero: bool  # may this be set to 0 / false and mean something real?
    zero_meaning: str
    valid_range: str
    default_when_omitted: str
    #: What this project's CLIENT actually does with it today -- distinct from
    #: whether the PROVIDER supports it. A provider can support a parameter
    #: this client has no use for (e.g. `tools`, since this client calls no
    #: tools), and reporting "supported" without this would overclaim.
    sent_by_this_client: bool
    effect: str


#: Every parameter this module knows about. Sourced from the OpenAI Chat
#: Completions API reference; nothing here is this project's invention.
PARAM_CATALOG: List[ParamSpec] = [
    ParamSpec(
        name="temperature",
        purpose="Sampling randomness. Lower is more deterministic.",
        kind="numeric",
        supported_families=(FAMILY_CHAT,),
        legal_zero=True,
        zero_meaning="Fully deterministic (greedy) sampling.",
        valid_range="0.0 - 2.0",
        default_when_omitted="1.0 (provider default)",
        sent_by_this_client=True,
        effect="This project always sends 0.0: every compile call must be as "
        "repeatable as possible, and creative variation is never wanted from "
        "a SQL author or a verifier.",
    ),
    ParamSpec(
        name="top_p",
        purpose="Nucleus sampling threshold, an alternative to temperature.",
        kind="numeric",
        supported_families=(FAMILY_CHAT,),
        legal_zero=False,
        zero_meaning="Not sent as 0: the provider documents this as a (0, 1] "
        "value, and 0 is degenerate (no tokens would be eligible).",
        valid_range="0.0 (exclusive) - 1.0",
        default_when_omitted="1.0 (provider default)",
        sent_by_this_client=False,
        effect="Not sent. The provider's own guidance is to alter temperature "
        "or top_p, not both; this client already fixes temperature, so top_p "
        "is left at the provider default rather than sent as an invalid 0.",
    ),
    ParamSpec(
        name="presence_penalty",
        purpose="Penalises tokens that have appeared at all so far, "
        "encouraging new topics.",
        kind="numeric",
        supported_families=(FAMILY_CHAT,),
        legal_zero=True,
        zero_meaning="No penalty applied -- the provider's own neutral default.",
        valid_range="-2.0 - 2.0",
        default_when_omitted="0.0 (provider default)",
        sent_by_this_client=True,
        effect="Sent as 0.0. This client wants no topic-diversity pressure at "
        "all on a SQL compile.",
    ),
    ParamSpec(
        name="frequency_penalty",
        purpose="Penalises tokens by how often they have already appeared, "
        "discouraging repetition.",
        kind="numeric",
        supported_families=(FAMILY_CHAT,),
        legal_zero=True,
        zero_meaning="No penalty applied -- the provider's own neutral default.",
        valid_range="-2.0 - 2.0",
        default_when_omitted="0.0 (provider default)",
        sent_by_this_client=True,
        effect="Sent as 0.0, for the same reason as presence_penalty.",
    ),
    ParamSpec(
        name="max_tokens",
        purpose="Ceiling on completion length, for chat-family models.",
        kind="numeric",
        supported_families=(FAMILY_CHAT,),
        legal_zero=False,
        zero_meaning="Not sent as 0: a 0-token ceiling produces no completion "
        "at all, which is not a legal request for this client to make.",
        valid_range="a positive integer, bounded by the model's context window",
        default_when_omitted="the model's own maximum (provider default)",
        sent_by_this_client=True,
        effect="Sent as REASONING_MAX_TOKENS from configuration -- an "
        "operational ceiling, not a generic zero-default control.",
    ),
    ParamSpec(
        name="max_completion_tokens",
        purpose="Ceiling on completion length, for reasoning-family models "
        "(replaces max_tokens for this family).",
        kind="numeric",
        supported_families=(FAMILY_REASONING,),
        legal_zero=False,
        zero_meaning="Not sent as 0, for the same reason as max_tokens.",
        valid_range="a positive integer, bounded by the model's context window",
        default_when_omitted="the model's own maximum (provider default)",
        sent_by_this_client=True,
        effect="Sent as REASONING_MAX_TOKENS when the configured model is a "
        "reasoning-family model.",
    ),
    ParamSpec(
        name="reasoning_effort",
        purpose="How much internal reasoning a reasoning-family model spends "
        "before answering.",
        kind="enum",
        supported_families=(FAMILY_REASONING,),
        legal_zero=False,
        zero_meaning="Not applicable -- this is an enum (low/medium/high), "
        "not a numeric control, so no zero value exists to set.",
        valid_range="low | medium | high (provider-defined)",
        default_when_omitted="medium (provider default)",
        sent_by_this_client=False,
        effect="Not sent. Left at the provider default for this first "
        "implementation; a future version may expose REASONING_EFFORT once "
        "there is evidence a non-default value improves compile quality.",
    ),
    ParamSpec(
        name="seed",
        purpose="Best-effort deterministic sampling across repeated calls "
        "with identical parameters.",
        kind="numeric",
        supported_families=(FAMILY_CHAT, FAMILY_REASONING),
        legal_zero=True,
        zero_meaning="0 is a legal seed value, but it is still an ARBITRARY "
        "one -- unlike temperature=0, there is no neutral/disabled meaning "
        "for a seed, so this project does not default it to 0.",
        valid_range="any integer",
        default_when_omitted="unset -- no determinism guarantee (provider "
        "default)",
        sent_by_this_client=False,
        effect="Not sent. The provider only guarantees 'best effort' "
        "determinism even with a seed, and this project's real repeatability "
        "guarantee already comes from temperature=0 plus this project's own "
        "compiled-artifact caching, not from the provider's seed feature.",
    ),
    ParamSpec(
        name="response_format",
        purpose="Constrain the provider's own decoding to a JSON object.",
        kind="object",
        supported_families=(FAMILY_CHAT, FAMILY_REASONING),
        legal_zero=False,
        zero_meaning="Not applicable -- this is a structured object, not a "
        "numeric or boolean control.",
        valid_range='{"type": "json_object"} or {"type": "text"}',
        default_when_omitted='{"type": "text"} (provider default)',
        sent_by_this_client=True,
        effect="Sent as json_object for the verifier's structured verdict. "
        "Retried once without it if the provider rejects the parameter "
        "outright, so an older or non-conforming endpoint still works.",
    ),
    ParamSpec(
        name="parallel_tool_calls",
        purpose="Whether the model may request more than one tool call in a "
        "single turn.",
        kind="boolean",
        supported_families=(FAMILY_CHAT, FAMILY_REASONING),
        legal_zero=True,
        zero_meaning="Disabled -- at most one tool call per turn.",
        valid_range="true | false",
        default_when_omitted="true (provider default)",
        sent_by_this_client=False,
        effect="Not applicable. This client calls no tools at all, so this "
        "control has nothing to act on -- listed for completeness, not sent.",
    ),
    ParamSpec(
        name="logprobs",
        purpose="Return the log probability of each output token.",
        kind="boolean",
        supported_families=(FAMILY_CHAT,),
        legal_zero=True,
        zero_meaning="Disabled -- no token probabilities returned.",
        valid_range="true | false",
        default_when_omitted="false (provider default)",
        sent_by_this_client=False,
        effect="Not sent. Nothing in this project reads token-level "
        "probabilities; sending this would cost payload size for no use.",
    ),
    ParamSpec(
        name="n",
        purpose="How many completions to generate for one request.",
        kind="numeric",
        supported_families=(FAMILY_CHAT, FAMILY_REASONING),
        legal_zero=False,
        zero_meaning="Not sent as 0: n must be at least 1, so the valid "
        "minimum (1) is reported instead of an invalid 0.",
        valid_range="a positive integer",
        default_when_omitted="1 (provider default)",
        sent_by_this_client=False,
        effect="Not sent. This client always wants exactly one completion, "
        "which is the provider's own default, so nothing needs to be sent.",
    ),
]


@dataclass(frozen=True)
class ResolvedParam:
    spec: ParamSpec
    supported: str  # "yes" | "no" | "unknown"
    current_value: Optional[Any]

    def as_dict(self) -> Dict[str, Any]:
        return {
            "parameter": self.spec.name,
            "purpose": self.spec.purpose,
            "supported": self.supported,
            "current_value": self.current_value,
            "default": self.spec.default_when_omitted,
            "valid_range": self.spec.valid_range,
            "zero_meaning": self.spec.zero_meaning,
            "effect": self.spec.effect,
            "sent_by_this_client": self.spec.sent_by_this_client,
        }


def resolve(model: str, *, max_tokens: Optional[int] = None) -> List[ResolvedParam]:
    """Every known parameter, resolved against ``model``'s family.

    ``supported`` is "unknown" for an unrecognised model name -- never
    silently "yes" or "no". A control this client does not currently send
    (``sent_by_this_client is False``) still gets a `current_value` for
    ``llm-config``'s benefit, describing what WOULD be sent if it were.
    """
    family = classify_model(model)
    out: List[ResolvedParam] = []
    for spec in PARAM_CATALOG:
        if family == FAMILY_UNKNOWN:
            supported = "unknown"
        else:
            supported = "yes" if family in spec.supported_families else "no"

        value: Optional[Any] = None
        if supported == "yes":
            if spec.name in ("temperature", "presence_penalty", "frequency_penalty"):
                value = 0.0 if spec.legal_zero else None
            elif spec.name in ("max_tokens", "max_completion_tokens"):
                value = max_tokens
            elif spec.name == "response_format":
                value = '{"type": "json_object"} (verifier only)'
            elif not spec.sent_by_this_client:
                value = None if not spec.legal_zero else f"(not sent; would default to {spec.default_when_omitted})"
        out.append(ResolvedParam(spec=spec, supported=supported, current_value=value))
    return out


def payload_extras(model: str, *, max_tokens: int, json_object: bool) -> Dict[str, Any]:
    """The provider-aware fields :func:`dynamic_db.llm.complete` should add to
    its request body, beyond ``model``/``messages``.

    THIS IS WHERE THE PROVIDER-AWARENESS ACTUALLY TAKES EFFECT -- but only for
    a model this module can classify WITH CONFIDENCE:

      * FAMILY_REASONING (a real, documented OpenAI reasoning-family name)
        gets ``max_completion_tokens`` and none of the chat-only sampling
        controls, because the provider rejects them outright for this family.
      * FAMILY_CHAT (a real, documented OpenAI chat-family name) gets
        ``max_tokens`` plus the two penalty terms at their neutral zero --
        the spec-compliant "expose the zero default" behaviour.
      * FAMILY_UNKNOWN -- a custom or internal model alias this module
        cannot confidently place in either family -- gets EXACTLY what this
        client always sent before this module existed: ``max_tokens`` and
        nothing else. Guessing a shape for an unrecognised name and being
        wrong would silently change what is actually sent to a real,
        currently-working configuration on nothing but a name resemblance;
        an unrecognised model is exactly the case where that guess is least
        safe to make.
    """
    family = classify_model(model)
    extras: Dict[str, Any] = {}
    if family == FAMILY_REASONING:
        extras["max_completion_tokens"] = max_tokens
    else:
        extras["max_tokens"] = max_tokens
        if family == FAMILY_CHAT:
            extras["presence_penalty"] = 0.0
            extras["frequency_penalty"] = 0.0
    if json_object:
        extras["response_format"] = {"type": "json_object"}
    return extras


def wants_temperature(model: str) -> bool:
    """Whether ``temperature`` may legally be sent to this model."""
    family = classify_model(model)
    return family != FAMILY_REASONING


def explain(model: str, *, max_tokens: Optional[int] = None) -> List[Dict[str, Any]]:
    """The ``llm-config --explain`` payload: every parameter, human-readable."""
    return [p.as_dict() for p in resolve(model, max_tokens=max_tokens)]

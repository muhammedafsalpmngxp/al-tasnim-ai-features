"""Logging setup.

Operational information is logged: the request, the report date, query timings,
row counts, LLM outcome and Excel generation. Credentials, API keys and
connection strings are never logged anywhere in this application -- the filter
below is a second line of defence in case a value reaches a log record by
accident.
"""

from __future__ import annotations

import logging
import re
from typing import Iterable

_SECRET_PATTERNS: Iterable[re.Pattern] = (
    # DB connection-string style: PWD=..., UID=..., as connection_string()
    # joins them (';'-separated, never quoted).
    re.compile(r"(PWD|PASSWORD|UID|USERNAME)\s*=\s*[^;\s]+", re.IGNORECASE),
    # `Bearer <token>` wherever it appears -- a raw header value, a dict
    # repr (`'Authorization': 'Bearer xxx'`), an httpx debug line. Matched as
    # one unit so the token itself is redacted, not just the word "Bearer":
    # `key\s*[:=]\s*\S+` below would otherwise stop at "Bearer" and leave the
    # actual value in the clear.
    re.compile(r"\bBearer\s+[^\s'\"),;}]+", re.IGNORECASE),
    # key=value / key: "value", quoted or not: an API key, a password, a
    # bare token or secret assigned to a named field.
    re.compile(
        r"(api[_-]?key|authorization|password|secret|token)"
        r"\s*[:=]\s*['\"]?[^\s'\"),;}]+",
        re.IGNORECASE,
    ),
    # A bare provider key by shape, even with no keyword next to it --
    # OpenAI-style (`sk-...`) and Groq-style (`gsk_...`) keys, which use
    # both '-' and '_' as the prefix separator.
    re.compile(r"\b(gsk|sk)[_-][A-Za-z0-9_\-]{16,}", re.IGNORECASE),
)


class SecretRedactingFilter(logging.Filter):
    """Redact anything that looks like a credential before it is written."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # noqa: BLE001
            return True
        redacted = message
        for pattern in _SECRET_PATTERNS:
            redacted = pattern.sub("***redacted***", redacted)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    resolved = getattr(logging, str(level).upper(), logging.INFO)
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )
    handler.addFilter(SecretRedactingFilter())

    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(resolved)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

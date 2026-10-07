"""One logging pipeline, several handlers, every record traceable to a run.

ONE PIPELINE, NOT TWO LOGGERS. Every log call anywhere in this project goes
through the standard library's ``logging`` module exactly as it always did --
``logger.info(...)``, ``logger.warning(...)``, ``logger.exception(...)``.
What this module adds is what is ATTACHED to that one pipeline:

  * a console handler, so everything important is visible while the process
    runs, with no timer or fake progress standing in for it;
  * a rotating, human-readable file handler for the same stream;
  * a rotating, machine-readable JSONL file handler emitting one JSON object
    per record, for later analysis;
  * a filter that stamps every record with the CURRENT RUN ID, without any
    call site having to pass one -- see :func:`run_context`;
  * a filter that redacts anything secret-shaped from the rendered message,
    centrally, so no call site has to remember to.

A SEPARATE, PER-RUN SUMMARY FILE answers the question an incident
investigation actually asks: "given this run_id, what happened?". See
:func:`write_run_record`. The rotating logs answer "what happened around
this time"; the per-run file answers "what happened in THIS run", without
having to grep a rotating log that may have already rotated past it.

TIMING USES A MONOTONIC CLOCK. See :class:`Timer` -- wall-clock timestamps are
for the RECORD (when something happened); a monotonic clock is for the
DURATION (how long it took), and the two are never the same number.
"""

from __future__ import annotations

import contextvars
import json
import logging
import logging.handlers
import re
import sys
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from dynamic_db.config import get_settings

_run_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("dynamic_db_run_id", default="")
_component_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "dynamic_db_component", default="dynamic_db"
)

_CONFIGURED = False


# --------------------------------------------------------------------------
# Run identity
# --------------------------------------------------------------------------


def new_run_id() -> str:
    """A short, unique identity for one run. Time-ordered, so run ids sort
    the way the runs actually happened -- useful when scanning the runs
    directory by eye."""
    return f"{int(time.time())}-{uuid.uuid4().hex[:8]}"


def current_run_id() -> str:
    return _run_id_var.get()


@contextmanager
def run_context(run_id: Optional[str] = None, *, component: Optional[str] = None) -> Iterator[str]:
    """Every log record emitted inside this block is stamped with ``run_id``.

    No call site anywhere in the package needs to know about this: the stamp
    is applied by a logging Filter, reading this contextvar, at emit time.
    """
    run_id = run_id or new_run_id()
    run_token = _run_id_var.set(run_id)
    component_token = _component_var.set(component) if component else None
    try:
        yield run_id
    finally:
        _run_id_var.reset(run_token)
        if component_token is not None:
            _component_var.reset(component_token)


# --------------------------------------------------------------------------
# Secret redaction -- centralised, applied to every record's final text
# --------------------------------------------------------------------------

#: Patterns matched case-insensitively against the RENDERED log message.
#: Broad on purpose: false positives here cost nothing (a redacted word that
#: was not actually secret); a missed one costs a leaked credential.
_SECRET_PATTERNS = [
    re.compile(r"(?i)(PWD|PASSWORD)\s*[=:]\s*[^;\s]+"),
    re.compile(r"(?i)(UID|USER\s*ID)\s*[=:]\s*[^;\s]+"),
    re.compile(r"(?i)(api[_-]?key|apikey)\s*[=:]\s*\S+"),
    # "Bearer <token>" is TWO tokens after the colon, not one -- matched as a
    # whole so the token itself can never survive as trailing, unmatched text.
    re.compile(r"(?i)(authorization)\s*:\s*bearer\s+\S+"),
    re.compile(r"(?i)(authorization)\s*[=:]\s*\S+"),
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\bsk-[A-Za-z0-9-]{16,}"),
    re.compile(r"(?i)(secret|token|credential)\s*[=:]\s*\S+"),
]
_REDACTED = "[REDACTED]"


def redact(text: str) -> str:
    """The same text with anything secret-shaped replaced.

    Applied once, centrally, as a logging Filter -- see
    :class:`_RedactionFilter` -- so no call site anywhere in this project has
    to remember to scrub its own message.
    """
    if not text:
        return text
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub(lambda m: f"{m.group(1)}={_REDACTED}" if m.groups() else _REDACTED, out)
    return out


class _RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:  # noqa: BLE001 - logging must never itself raise
            pass
        return True


class _RunContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.run_id = _run_id_var.get() or "-"
        record.component = _component_var.get()
        return True


# --------------------------------------------------------------------------
# Formatters
# --------------------------------------------------------------------------


class _HumanFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        base = super().format(record)
        run_id = getattr(record, "run_id", "-")
        component = getattr(record, "component", "dynamic_db")
        return f"[{component.upper()}] [{run_id}] {base}"


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: Dict[str, Any] = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "run_id": getattr(record, "run_id", "-"),
            "component": getattr(record, "component", "dynamic_db"),
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["error_type"] = record.exc_info[0].__name__ if record.exc_info[0] else None
            payload["traceback"] = self.formatException(record.exc_info)
        for key, value in getattr(record, "extra_fields", {}).items():
            payload.setdefault(key, value)
        return json.dumps(payload, default=str)


# --------------------------------------------------------------------------
# Setup
# --------------------------------------------------------------------------


def configure(*, component: str = "dynamic_db", force: bool = False) -> None:
    """Attach console + rotating file handlers to the root logger, once.

    Idempotent: calling this more than once (the CLI, the library entry
    point, and a downstream application's own bootstrap might all call it)
    does not duplicate handlers, unless ``force`` asks for a clean rebuild.

    ATTACHES TO THIS PACKAGE'S OWN LOGGER, NEVER TO THE ROOT LOGGER. Every
    module here logs through ``logging.getLogger(__name__)``, which for a
    package always starts with ``dynamic_db.`` -- so handlers on the
    ``dynamic_db`` logger, with propagation switched off, see every one of
    them without needing the root logger at all. This is what makes it safe
    to call from inside a HOST application (Daily Report, or any other
    embedder) that configures its own root logger: this call can never
    duplicate that application's own log lines through a second, differently
    formatted pipeline, and that application's own logging is never disturbed
    by this one being configured.
    """
    global _CONFIGURED
    if _CONFIGURED and not force:
        return

    settings = get_settings()
    root = logging.getLogger("dynamic_db")
    root.propagate = False
    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)
    root.setLevel(settings.log_level.upper())

    run_filter = _RunContextFilter()
    redaction_filter = _RedactionFilter()

    if settings.log_console:
        console = logging.StreamHandler(stream=sys.stdout)
        console.addFilter(run_filter)
        console.addFilter(redaction_filter)
        if settings.log_console_json:
            console.setFormatter(_JsonFormatter())
        else:
            console.setFormatter(_HumanFormatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
        root.addHandler(console)

    log_dir = _resolve_log_dir(settings)
    app_dir = log_dir / "application"
    dyn_dir = log_dir / "dynamic_db"
    for directory in (app_dir, dyn_dir, log_dir / "runs"):
        directory.mkdir(parents=True, exist_ok=True)

    text_handler = logging.handlers.RotatingFileHandler(
        dyn_dir / f"{component}.log",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    text_handler.addFilter(run_filter)
    text_handler.addFilter(redaction_filter)
    text_handler.setFormatter(_HumanFormatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root.addHandler(text_handler)

    json_handler = logging.handlers.RotatingFileHandler(
        dyn_dir / f"{component}.jsonl",
        maxBytes=settings.log_max_bytes,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
    )
    json_handler.addFilter(run_filter)
    json_handler.addFilter(redaction_filter)
    json_handler.setFormatter(_JsonFormatter())
    root.addHandler(json_handler)

    _CONFIGURED = True


def _resolve_log_dir(settings) -> Path:
    from dynamic_db.config import PACKAGE_ROOT

    path = Path(settings.log_dir)
    if not path.is_absolute():
        path = PACKAGE_ROOT / path
    return path


def runs_dir() -> Path:
    return _resolve_log_dir(get_settings()) / "runs"


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------


class Timer:
    """A monotonic stopwatch for one named operation.

    Never derives a duration from two wall-clock timestamps -- a system clock
    adjustment (NTP sync, a laptop waking from sleep) can make that negative
    or absurd. ``time.perf_counter()`` cannot go backwards.
    """

    def __init__(self, operation: str) -> None:
        self.operation = operation
        self._started: Optional[float] = None
        self.duration_ms: Optional[float] = None

    def __enter__(self) -> "Timer":
        self._started = time.perf_counter()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._started is not None:
            self.duration_ms = (time.perf_counter() - self._started) * 1000


@contextmanager
def timed(logger: logging.Logger, operation: str, **extra: Any) -> Iterator[Timer]:
    """Log ``<operation> started`` / ``<operation> completed (N ms)`` around
    a block, and yield the :class:`Timer` so the caller can read the exact
    duration afterward (e.g. to put it in a run summary)."""
    timer = Timer(operation)
    logger.info("%s started", operation, extra={"extra_fields": {"event": f"{operation}_started", **extra}})
    with timer:
        try:
            yield timer
        except Exception as exc:  # noqa: BLE001 - re-raised after logging
            logger.error(
                "%s failed after %.1f ms - %s",
                operation,
                (time.perf_counter() - timer._started) * 1000 if timer._started else 0.0,
                exc,
                extra={"extra_fields": {"event": f"{operation}_failed", **extra}},
            )
            raise
    logger.info(
        "%s completed (%.1f ms)",
        operation,
        timer.duration_ms or 0.0,
        extra={"extra_fields": {"event": f"{operation}_completed", "duration_ms": timer.duration_ms, **extra}},
    )


# --------------------------------------------------------------------------
# Per-run record -- what an incident investigation actually reads
# --------------------------------------------------------------------------


@dataclass
class RunRecord:
    """Everything an operator needs to answer "what happened in run X".

    Written once, at the end of a run (success or failure), as one JSON file
    named by run_id -- so it is found by run_id directly, without grepping a
    rotating log that may have already rotated past it.
    """

    run_id: str
    component: str
    operation: str
    started_at: str
    finished_at: str = ""
    duration_ms: float = 0.0
    status: str = "unknown"
    steps: List[Dict[str, Any]] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None
    context: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "run_id": self.run_id,
            "component": self.component,
            "operation": self.operation,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_ms": round(self.duration_ms, 1),
            "status": self.status,
            "steps": self.steps,
            "error": self.error,
            "context": {k: redact(str(v)) for k, v in self.context.items()},
        }


def write_run_record(record: RunRecord) -> Path:
    """Persist one run's summary. Never raises -- a run record that could not
    be written must never be the reason a run itself is reported as failed."""
    path = runs_dir() / f"{record.run_id}.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(record.as_dict(), indent=2, default=str), encoding="utf-8")
    except OSError:
        logging.getLogger(__name__).warning("could not write run record %s", path)
    return path


def read_run_record(run_id: str) -> Optional[Dict[str, Any]]:
    path = runs_dir() / f"{run_id}.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def list_run_records(limit: int = 50) -> List[Dict[str, Any]]:
    directory = runs_dir()
    if not directory.exists():
        return []
    paths = sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    out = []
    for path in paths[:limit]:
        try:
            out.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out


def prune_run_records(retention_days: Optional[int] = None) -> int:
    """Remove run records older than the configured retention. Returns how
    many were removed. 0/None retention disables pruning entirely."""
    settings = get_settings()
    days = settings.log_retention_days if retention_days is None else retention_days
    if not days:
        return 0
    cutoff = time.time() - days * 86400
    removed = 0
    for path in runs_dir().glob("*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed

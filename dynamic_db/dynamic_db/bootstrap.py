"""THE authoritative startup gate. One orchestrator, one state, never duplicated.

A DOWNSTREAM APPLICATION HOLDS EXACTLY ONE :class:`BootstrapOrchestrator`,
built once at process startup, and calls :meth:`BootstrapOrchestrator.run`
once. Its ``.status`` property is a thread-safe snapshot a web layer can poll
from any request handler at any time, including WHILE a run is still in
progress -- that is what lets a frontend show live, real progress instead of
a fake timer.

    STARTING -> CONNECTING -> INTROSPECTING -> GENERATING_FINGERPRINT
             -> COMPARING_FINGERPRINT -> [SCHEMA_CHANGED ->] VALIDATING
             -> COMPILING (only if anything is actually affected)
             -> LOADING_ARTIFACTS -> READY
                                   -> FAILED (from any step)

ONLY A GENUINE STATE TRANSITION IS EVER REPORTED. Nothing here fakes
progress with a timer; every step is a real operation, timed with a monotonic
clock, and the state only advances once that operation has actually
completed.

WHY THIS IS NOT ITSELF A LANGGRAPH GRAPH. Every one of these steps runs in a
fixed, deterministic order every single time -- there is no discovered fact
that could make the bootstrap skip introspection, or run validation before
comparing fingerprints. A planner or a graph exists to choose among genuinely
different paths; this pipeline has exactly one path, so it is a plain
function that reports its own progress, not a graph asked to find one.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Sequence

from dynamic_db import capabilities, changes, db, introspect, logging_setup, service
from dynamic_db.config import get_settings
from dynamic_db.logging_setup import RunRecord, Timer, new_run_id, run_context, write_run_record

logger = logging.getLogger(__name__)

STATE_STARTING = "STARTING"
STATE_CONNECTING = "CONNECTING"
STATE_INTROSPECTING = "INTROSPECTING"
STATE_GENERATING_FINGERPRINT = "GENERATING_FINGERPRINT"
STATE_COMPARING_FINGERPRINT = "COMPARING_FINGERPRINT"
STATE_SCHEMA_CHANGED = "SCHEMA_CHANGED"
STATE_VALIDATING = "VALIDATING"
STATE_COMPILING = "COMPILING"
STATE_LOADING_ARTIFACTS = "LOADING_ARTIFACTS"
STATE_READY = "READY"
STATE_FAILED = "FAILED"

#: The order a frontend checklist renders in. STATE_SCHEMA_CHANGED is
#: deliberately absent: it is a transient annotation on COMPARING_FINGERPRINT,
#: not a stop the pipeline waits in.
STATE_ORDER = (
    STATE_STARTING,
    STATE_CONNECTING,
    STATE_INTROSPECTING,
    STATE_GENERATING_FINGERPRINT,
    STATE_COMPARING_FINGERPRINT,
    STATE_VALIDATING,
    STATE_COMPILING,
    STATE_LOADING_ARTIFACTS,
    STATE_READY,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass
class StepStatus:
    name: str
    status: str = "pending"  # pending | running | done | skipped | failed
    duration_ms: Optional[float] = None
    detail: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BootstrapStatus:
    run_id: str = ""
    state: str = STATE_STARTING
    ready: bool = False
    started_at: str = ""
    updated_at: str = ""
    duration_ms: float = 0.0
    database: str = ""
    structure_fingerprint: str = ""
    live_fingerprint: str = ""
    schema_changed: bool = False
    change_summary: str = ""
    affected_capabilities: List[str] = field(default_factory=list)
    unavailable_capabilities: List[str] = field(default_factory=list)
    steps: List[StepStatus] = field(default_factory=list)
    error: Optional[Dict[str, Any]] = None

    def as_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        out["steps"] = [s.as_dict() for s in self.steps]
        return out


class BootstrapOrchestrator:
    """The single source of truth for whether DYNAMIC_DB is ready to serve.

    Thread-safe: :meth:`run` mutates state under a lock; :attr:`status` reads
    a snapshot under the same lock, so a request handler polling status from
    another thread while a run is in progress never sees a half-updated
    object, and never blocks the run itself for longer than a dict copy.
    """

    def __init__(self, capability_ids: Optional[Sequence[str]] = None) -> None:
        self._lock = threading.Lock()
        self._capability_ids = tuple(capability_ids) if capability_ids else None
        self._status = BootstrapStatus(state=STATE_STARTING, started_at=_now(), updated_at=_now())

    @property
    def status(self) -> Dict[str, Any]:
        with self._lock:
            return self._status.as_dict()

    @property
    def is_ready(self) -> bool:
        with self._lock:
            return self._status.ready

    def _set(self, **fields: Any) -> None:
        with self._lock:
            for key, value in fields.items():
                setattr(self._status, key, value)
            self._status.updated_at = _now()

    def _step(self, name: str, status: str, *, duration_ms: Optional[float] = None, detail: str = "") -> None:
        with self._lock:
            self._status.state = name if status == "running" else self._status.state
            self._status.steps.append(
                StepStatus(name=name, status=status, duration_ms=duration_ms, detail=detail)
            )
            self._status.updated_at = _now()

    # ----------------------------------------------------------------
    def run(self, *, report_date: Optional[date] = None, run_id: Optional[str] = None) -> Dict[str, Any]:
        """Run the whole fingerprint-gated pipeline once, synchronously.

        Never raises. A failure is reported as ``state=FAILED`` with a full
        error record, exactly like a success is reported as ``READY`` --
        the caller (an application's own startup event) reads
        :attr:`status` either way and decides what to do; it must never
        crash the host process on a Dynamic DB failure, or nobody would ever
        see the failure state this method so carefully produced.
        """
        logging_setup.configure(component="dynamic_db")
        run_id = run_id or new_run_id()
        record = RunRecord(
            run_id=run_id, component="dynamic_db", operation="bootstrap", started_at=_now()
        )
        with run_context(run_id, component="dynamic_db"):
            self._set(run_id=run_id, state=STATE_STARTING, ready=False, error=None)
            logger.info("bootstrap: starting run %s", run_id)
            overall = Timer("bootstrap")
            with overall:
                try:
                    self._run_steps(report_date, record)
                except Exception as exc:  # noqa: BLE001 - this method must never raise
                    logger.exception("bootstrap: unhandled failure")
                    self._fail("unhandled_error", str(exc), record)

            record.duration_ms = overall.duration_ms or 0.0
            record.finished_at = _now()
            record.status = "READY" if self.is_ready else "FAILED"
            write_run_record(record)
            self._set(duration_ms=record.duration_ms)
            # Logged INSIDE the run_context block, so this line -- like every
            # other one in this run -- carries the real run_id rather than
            # the "-" placeholder a log emitted after the context exits would
            # get stamped with.
            logger.info(
                "bootstrap: run %s finished as %s in %.0f ms",
                run_id, record.status, record.duration_ms,
            )
        return self.status

    def _run_steps(self, report_date: Optional[date], record: RunRecord) -> None:
        settings = get_settings()

        # 1. CONNECT -----------------------------------------------------
        self._step(STATE_CONNECTING, "running")
        with Timer("connect") as t:
            try:
                info = db.check_connectivity()
            except Exception as exc:  # noqa: BLE001
                self._step(STATE_CONNECTING, "failed", detail=str(exc))
                self._fail("connection_failed", str(exc), record)
                return
        self._step(STATE_CONNECTING, "done", duration_ms=t.duration_ms, detail=str(info.get("database_name", "")))
        record.steps.append({"step": STATE_CONNECTING, "duration_ms": t.duration_ms, "status": "done"})

        # 2. INTROSPECT ----------------------------------------------------
        self._step(STATE_INTROSPECTING, "running")
        with Timer("introspect") as t:
            try:
                snapshot = introspect.introspect()
            except Exception as exc:  # noqa: BLE001
                self._step(STATE_INTROSPECTING, "failed", detail=str(exc))
                self._fail("introspection_failed", str(exc), record)
                return
        if not snapshot.approved_tables():
            self._step(
                STATE_INTROSPECTING, "failed",
                detail="INCLUDED_TABLES resolved to zero live tables - check configuration",
            )
            self._fail(
                "no_approved_tables",
                "No approved table was found in the live database. Check INCLUDED_TABLES.",
                record,
            )
            return
        self._step(
            STATE_INTROSPECTING, "done", duration_ms=t.duration_ms,
            detail=f"{len(snapshot.approved_tables())} approved table(s)",
        )
        record.steps.append({"step": STATE_INTROSPECTING, "duration_ms": t.duration_ms, "status": "done"})
        self._set(database=snapshot.database)

        # 3. FINGERPRINT -----------------------------------------------------
        self._step(STATE_GENERATING_FINGERPRINT, "running")
        with Timer("fingerprint") as t:
            structure_fp = snapshot.structure_fingerprint()
            live_fp = snapshot.live_fingerprint()
        self._step(STATE_GENERATING_FINGERPRINT, "done", duration_ms=t.duration_ms, detail=structure_fp[:16])
        self._set(structure_fingerprint=structure_fp, live_fingerprint=live_fp)
        record.steps.append({"step": STATE_GENERATING_FINGERPRINT, "duration_ms": t.duration_ms})

        # 4. COMPARE -----------------------------------------------------
        self._step(STATE_COMPARING_FINGERPRINT, "running")
        with Timer("compare_fingerprint") as t:
            previous = introspect.load_previous_snapshot()
            report = changes.compare(previous, snapshot)
        changed = bool(report.changed)
        self._set(schema_changed=changed, change_summary=report.summary())
        if changed:
            self._step(
                STATE_SCHEMA_CHANGED, "done", duration_ms=0.0,
                detail=report.summary(),
            )
            logger.warning("bootstrap: SCHEMA CHANGED - %s", report.summary())
        self._step(
            STATE_COMPARING_FINGERPRINT, "done", duration_ms=t.duration_ms,
            detail="changed" if changed else "unchanged",
        )
        record.steps.append({"step": STATE_COMPARING_FINGERPRINT, "duration_ms": t.duration_ms, "changed": changed})

        # 5. VALIDATE ------------------------------------------------------
        self._step(STATE_VALIDATING, "running")
        compiler = service.get_service()
        with Timer("validate") as t:
            compiler.ensure_seeded(snapshot)
            wanted = self._capability_ids or tuple(capabilities.all_ids())
            stale_ids: List[str] = []
            for capability_id in wanted:
                capability = capabilities.get(capability_id)
                from dynamic_db import artifacts as artifacts_module
                from dynamic_db import prompts as prompts_module

                current = artifacts_module.load(capability_id).current
                stale, _reason = artifacts_module.staleness(
                    current, capability, snapshot,
                    compiler_version=prompts_module.COMPILER_VERSION,
                    prompt_version=prompts_module.AUTHOR_PROMPT_VERSION,
                    rules_version=compiler._rules_version(capability),
                )
                if stale:
                    stale_ids.append(capability_id)
        self._step(
            STATE_VALIDATING, "done", duration_ms=t.duration_ms,
            detail=f"{len(stale_ids)} of {len(wanted)} capability(ies) stale",
        )
        self._set(affected_capabilities=stale_ids)
        record.steps.append({"step": STATE_VALIDATING, "duration_ms": t.duration_ms, "stale": stale_ids})

        # 6. COMPILE (only what is actually affected) -----------------------
        if stale_ids and settings.dynamic_sql_mode != service.MODE_OFF:
            self._step(STATE_COMPILING, "running")
            with Timer("compile") as t:
                results = []
                for capability_id in capabilities.compile_order(stale_ids):
                    results.append(
                        compiler.compile(capability_id, report_date=report_date, snapshot=snapshot)
                    )
            failed = [r["capability"] for r in results if r.get("status") == "failed"]
            self._step(
                STATE_COMPILING, "done", duration_ms=t.duration_ms,
                detail=f"{len(results) - len(failed)} of {len(results)} compiled",
            )
            record.steps.append(
                {"step": STATE_COMPILING, "duration_ms": t.duration_ms, "results": results}
            )
        else:
            self._step(STATE_COMPILING, "skipped", detail="nothing affected" if not stale_ids else "mode is off")

        # 7. LOAD ARTIFACTS --------------------------------------------------
        self._step(STATE_LOADING_ARTIFACTS, "running")
        with Timer("load_artifacts") as t:
            from dynamic_db import artifacts as artifacts_module

            unusable: List[str] = []
            for capability_id in (self._capability_ids or tuple(capabilities.all_ids())):
                current = artifacts_module.load(capability_id).current
                if current is None or not (current.usable or current.not_applicable):
                    unusable.append(capability_id)
        self._step(
            STATE_LOADING_ARTIFACTS, "done", duration_ms=t.duration_ms,
            detail=f"{len(unusable)} capability(ies) without a usable artifact",
        )
        self._set(unavailable_capabilities=unusable)
        record.steps.append({"step": STATE_LOADING_ARTIFACTS, "duration_ms": t.duration_ms, "unusable": unusable})

        # 8. READY -----------------------------------------------------------
        self._step(STATE_READY, "done")
        self._set(state=STATE_READY, ready=True)
        logger.info("bootstrap: READY (%d capability(ies) unavailable)", len(unusable))

    def _fail(self, error_type: str, message: str, record: RunRecord) -> None:
        self._set(
            state=STATE_FAILED,
            ready=False,
            error={"type": error_type, "message": message, "at": _now()},
        )
        record.error = {"type": error_type, "message": message}
        logger.error("bootstrap: FAILED - %s: %s", error_type, message)


_orchestrator: Optional[BootstrapOrchestrator] = None


def get_orchestrator(capability_ids: Optional[Sequence[str]] = None) -> BootstrapOrchestrator:
    """The process-wide bootstrap orchestrator. One instance, never duplicated."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = BootstrapOrchestrator(capability_ids)
    return _orchestrator


def reset_orchestrator() -> None:
    """For tests: drop the process-wide instance so the next call builds fresh."""
    global _orchestrator
    _orchestrator = None

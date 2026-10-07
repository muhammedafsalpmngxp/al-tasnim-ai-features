"""Where the compiler meets the application: which SQL actually serves a request.

THE NORMAL DAY COSTS NOTHING. A request asks for a capability's SQL; the
promoted artifact's recorded dependencies still match the live schema; the SQL
is returned. No model is called, no prompt is built, and the only database work
is a cached catalogue read that is itself re-checked at most once every few
minutes. The dynamic layer is what absorbs a CHANGE -- it is not a tax on every
request.

THE BASELINE .sql FILES ARE THE FIRST ARTIFACT, not a fallback that sits unused
until something breaks. They are registered as each capability's version 1
after passing the same deterministic validator every compiled artifact passes,
so this system starts fully working, against a schema it has never seen
compiled, for zero model calls. What the reasoning model is for is the day one
of those files stops fitting the database.

WHEN AN ARTIFACT NO LONGER FITS, THE DECISION IS DELIBERATE AND DETERMINISTIC.
A stale artifact is re-validated against the NEW schema: if every object it
names still exists and its contract still holds, the change was irrelevant to
it and it may keep serving while a recompile is attempted. If not, it is
withdrawn -- and the capability is reported unavailable rather than answering a
different question in the same shape. In ``strict`` mode even the first case is
refused.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from dynamic_db import artifacts, capabilities, graph, introspect, llm, prompts
from dynamic_db import dependencies as deps
from dynamic_db import rules as rules_module
from dynamic_db import validation
from dynamic_db.capabilities import PARAM_REPORT_DATE, Capability
from dynamic_db.config import get_settings
from dynamic_db.introspect import SchemaSnapshot
from dynamic_db.state import (
    STATUS_FAILED,
    STATUS_NOT_APPLICABLE,
    STATUS_PROMOTED,
    STATUS_REUSED,
    initial,
    outcome,
)

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_AUTO = "auto"
MODE_STRICT = "strict"

#: How long a resolved capability may be served without re-reading the
#: catalogue. The check itself is metadata-only and cheap, but paying it on
#: every drill-down click would be paying it hundreds of times to learn the
#: same thing. A schema change is noticed within this window, and immediately
#: on any process start.
_SCHEMA_CHECK_TTL_SECONDS = 300


class CapabilityUnavailable(RuntimeError):
    """This capability cannot be served against the database as it is now.

    Raised rather than returning something plausible. A report that is missing
    a section, and says why, is recoverable; one that silently answers a
    different question is not.
    """


class CompilerService:
    """Resolves each capability to the SQL that currently serves it."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._resolved: Dict[str, str] = {}
        self._checked_at: Dict[str, float] = {}
        self._seeded = False
        self._snapshot: Optional[SchemaSnapshot] = None
        self._snapshot_at: float = 0.0

    # -- the hot path ------------------------------------------------------
    def sql_for(self, capability_id: str, *, report_date: Optional[date] = None) -> str:
        """The SQL that serves this capability right now.

        Raises:
            CapabilityUnavailable: the current schema cannot support it.
        """
        capability = capabilities.get(capability_id)
        settings = get_settings()

        if settings.dynamic_sql_mode == MODE_OFF:
            if not capability.baseline_sql:
                raise CapabilityUnavailable(
                    f"{capability.id} has no baseline SQL and DYNAMIC_SQL_MODE is off."
                )
            return capability.baseline_sql

        with self._lock:
            cached = self._resolved.get(capability_id)
            checked = self._checked_at.get(capability_id, 0.0)
            if cached and (time.monotonic() - checked) < _SCHEMA_CHECK_TTL_SECONDS:
                return cached

        sql = self._resolve(capability, report_date=report_date)
        with self._lock:
            self._resolved[capability_id] = sql
            self._checked_at[capability_id] = time.monotonic()
        return sql

    def invalidate(self, capability_id: Optional[str] = None) -> None:
        """Force the next request to re-check the schema."""
        with self._lock:
            if capability_id is None:
                self._resolved.clear()
                self._checked_at.clear()
                self._snapshot = None
            else:
                self._resolved.pop(capability_id, None)
                self._checked_at.pop(capability_id, None)

    # -- resolution --------------------------------------------------------
    def _snapshot_now(self, *, force: bool = False) -> SchemaSnapshot:
        """The live schema, re-read at most once per TTL within this process."""
        with self._lock:
            fresh = (
                self._snapshot is not None
                and not force
                and (time.monotonic() - self._snapshot_at) < _SCHEMA_CHECK_TTL_SECONDS
            )
            if fresh:
                return self._snapshot  # type: ignore[return-value]
        snapshot = introspect.introspect()
        with self._lock:
            self._snapshot = snapshot
            self._snapshot_at = time.monotonic()
        return snapshot

    def _rules_version(self, capability: Capability) -> str:
        return rules_module.version(capability.rules_text())

    def _resolve(self, capability: Capability, *, report_date: Optional[date]) -> str:
        settings = get_settings()
        snapshot = self._snapshot_now()
        self.ensure_seeded(snapshot)

        record = artifacts.load(capability.id)
        stale, reason = artifacts.staleness(
            record.current,
            capability,
            snapshot,
            compiler_version=prompts.COMPILER_VERSION,
            prompt_version=prompts.AUTHOR_PROMPT_VERSION,
            rules_version=self._rules_version(capability),
        )

        if record.current is not None and record.current.not_applicable:
            raise CapabilityUnavailable(
                f"{capability.id} is recorded as not applicable to this database: "
                f"{record.current.not_applicable_reason}"
            )

        if not stale and record.current is not None:
            return record.current.sql

        logger.warning("dynamic[%s]: stale - %s", capability.id, reason)
        result = self.compile(capability.id, report_date=report_date, snapshot=snapshot)
        if result.get("status") in (STATUS_PROMOTED, STATUS_REUSED):
            refreshed = artifacts.load(capability.id)
            if refreshed.current is not None and refreshed.current.usable:
                return refreshed.current.sql
        if result.get("status") == STATUS_NOT_APPLICABLE:
            raise CapabilityUnavailable(
                f"{capability.id} cannot be represented by this database: "
                f"{result.get('not_applicable_reason', '')}"
            )

        # The recompile did not produce a verified artifact. Whether the
        # previous one may keep serving is decided here, deterministically, and
        # never by default.
        return self._fall_back(capability, record, snapshot, settings.dynamic_sql_mode, reason)

    def _fall_back(
        self,
        capability: Capability,
        record: artifacts.ArtifactRecord,
        snapshot: SchemaSnapshot,
        mode: str,
        reason: str,
    ) -> str:
        current = record.current
        if current is None or not current.usable:
            raise CapabilityUnavailable(
                f"{capability.id} has no verified SQL for the current schema ({reason})."
            )
        if mode == MODE_STRICT:
            raise CapabilityUnavailable(
                f"{capability.id} could not be re-verified against the current schema "
                f"({reason}), and DYNAMIC_SQL_MODE is strict."
            )
        # "Demonstrably still works" is a decidable question, so it is decided
        # rather than assumed: every object it names still exists inside the
        # approved boundary, and its contract still holds.
        problems = validation.validate(current.sql, capability, snapshot)
        if problems:
            logger.error(
                "dynamic[%s]: the previous artifact does not fit the current schema either "
                "(%s). The capability is unavailable.",
                capability.id,
                problems[0][:200],
            )
            raise CapabilityUnavailable(
                f"{capability.id} cannot be served: its stored SQL no longer fits the current "
                f"schema, and a new one could not be verified."
            )
        logger.warning(
            "dynamic[%s]: recompilation did not produce a verified artifact, but the stored "
            "one still validates against the current schema, so it keeps serving. This is a "
            "deliberate decision, not a default.",
            capability.id,
        )
        return current.sql

    # -- seeding -----------------------------------------------------------
    def ensure_seeded(self, snapshot: Optional[SchemaSnapshot] = None) -> Dict[str, str]:
        """Register the baseline .sql files as each capability's first artifact.

        Every one goes through the SAME deterministic validator a compiled
        artifact does. A baseline that does not fit the live schema is NOT
        registered -- it is exactly the case the compiler exists for, and
        pretending it was verified would defeat the whole gate.
        """
        if self._seeded:
            return {}
        snapshot = snapshot or self._snapshot_now()
        results: Dict[str, str] = {}
        for capability in capabilities.all_capabilities():
            record = artifacts.load(capability.id)
            if record.current is not None:
                continue
            sql = capability.baseline_sql
            if not sql.strip():
                results[capability.id] = "no baseline SQL was registered for this capability"
                continue
            problems = validation.validate(sql, capability, snapshot)
            if problems:
                results[capability.id] = f"baseline rejected: {problems[0][:160]}"
                logger.warning(
                    "dynamic[%s]: the baseline SQL does not fit the current schema - %s",
                    capability.id,
                    problems[0][:200],
                )
                continue
            footprint = deps.extract(sql, snapshot)
            artifacts.promote(
                capability.id,
                artifacts.Artifact(
                    capability_id=capability.id,
                    sql=sql,
                    origin=artifacts.ORIGIN_SEED,
                    schema_fingerprint=snapshot.structure_fingerprint(),
                    dependency_fingerprint=snapshot.dependency_fingerprint(
                        footprint.tables, footprint.columns
                    ),
                    dependencies=footprint.as_dict(),
                    validator_status="passed",
                    verifier_status=artifacts.VERIFIED_SEED,
                    verifier_note=(
                        "Human-authored baseline, registered after passing the same "
                        "deterministic validation a compiled artifact passes. Not model-"
                        "reviewed, because it was not model-written."
                    ),
                    model_used="",
                    prompt_version=prompts.AUTHOR_PROMPT_VERSION,
                    compiler_version=prompts.COMPILER_VERSION,
                    rules_version=self._rules_version(capability),
                ),
            )
            results[capability.id] = "seeded"
        introspect.store_previous_snapshot(snapshot)
        self._seeded = True
        return results

    # -- compilation -------------------------------------------------------
    def _probe_params(
        self, capability: Capability, report_date: Optional[date], snapshot: SchemaSnapshot
    ) -> Tuple[List[Any], str]:
        """(parameters, where they came from). Never a hardcoded identifier.

        A capability that needs more than a report date borrows a real pair
        from another capability's already-verified result. That keeps the probe
        honest -- it runs against a row that genuinely exists -- without any
        well, task or crew id appearing anywhere in this codebase.
        """
        params: List[Any] = []
        sources: List[str] = []
        for parameter in capability.parameters:
            if parameter.kind == PARAM_REPORT_DATE:
                if report_date is None:
                    return [], "no report date was supplied for the probe"
                params.append(report_date)
                sources.append("the report date being served")
            elif parameter.probe_default is not None:
                # A presentation bound -- a page size, a limit -- not a business
                # value. Binding one for the probe invents nothing about the data.
                params.append(parameter.probe_default)
                sources.append(f"the declared probe bound for {parameter.name}")
        if len(params) == capability.parameter_count:
            return params, "; ".join(dict.fromkeys(sources))

        if not capability.probe_params_from:
            return [], "this capability's probe parameters have no verified source"

        borrowed = self._borrow_probe_params(capability, report_date)
        if borrowed is None:
            return [], (
                f"no row was available from {capability.probe_params_from} to probe with"
            )
        sources.append(f"one row of the verified {capability.probe_params_from} result")
        return params + borrowed, "; ".join(dict.fromkeys(sources))

    def _borrow_probe_params(
        self, capability: Capability, report_date: Optional[date]
    ) -> Optional[List[Any]]:
        source = artifacts.load(capability.probe_params_from or "")
        if source.current is None or not source.current.usable:
            return None
        from dynamic_db.db import fetch_all

        try:
            rows = fetch_all(
                source.current.sql,
                (report_date,) if report_date is not None else (),
                label=f"probe_params:{capability.id}",
            )
        except Exception as exc:  # noqa: BLE001 - a probe helper must not break a compile
            logger.info(
                "dynamic[%s]: could not borrow probe parameters (%s)", capability.id, exc
            )
            return None
        for row in rows:
            values = [row.get(column) for column in capability.probe_param_columns]
            if all(value is not None for value in values):
                return values
        return None

    def compile(
        self,
        capability_id: str,
        *,
        report_date: Optional[date] = None,
        snapshot: Optional[SchemaSnapshot] = None,
        force: bool = False,
    ) -> Dict[str, Any]:
        """Run one capability through the graph. Returns its outcome summary."""
        capability = capabilities.get(capability_id)
        settings = get_settings()
        snapshot = snapshot or self._snapshot_now()

        if not settings.reasoning_configured:
            # No silent downgrade to the fast model. A semantic compile on a
            # narration model produces SQL that runs and measures the wrong
            # thing, which is worse than not compiling at all.
            message = (
                "the reasoning model system is not configured, so no capability can be "
                "recompiled"
            )
            logger.error("dynamic[%s]: %s", capability_id, message)
            return {"capability": capability_id, "status": STATUS_FAILED, "reason": message}

        params, source = self._probe_params(capability, report_date, snapshot)
        if len(params) != capability.parameter_count:
            message = f"cannot probe {capability.id}: {source}"
            logger.error("dynamic[%s]: %s", capability_id, message)
            return {"capability": capability_id, "status": STATUS_FAILED, "reason": message}

        state = initial(
            run_id=uuid.uuid4().hex[:8],
            capability=capability.id,
            probe_params=params,
            snapshot=snapshot,
            max_sql_retries=settings.dynamic_max_sql_retries,
            max_verify_retries=settings.dynamic_max_verify_retries,
        )
        state["probe_params_source"] = source
        # An operator asking for a recompile gets one, even when the
        # deterministic check says nothing moved. The check still runs and is
        # still logged, so a forced run is auditable as exactly that.
        state["force"] = bool(force)

        try:
            final = graph.run(state)
        except Exception as exc:  # noqa: BLE001 - a compile must never take the app down
            logger.exception("dynamic[%s]: the compile graph failed", capability_id)
            return {
                "capability": capability_id,
                "status": STATUS_FAILED,
                "reason": f"the compile graph failed: {exc}",
            }

        result = outcome(final)
        result["not_applicable_reason"] = final.get("not_applicable_reason", "")
        self.invalidate(capability_id)
        return result

    def compile_all(
        self, *, report_date: Optional[date] = None, force: bool = False
    ) -> List[Dict[str, Any]]:
        """Compile every capability that needs it, in dependency order.

        The order matters: a capability that borrows another's verified result
        for its probe parameters must never run first.
        """
        snapshot = self._snapshot_now(force=True)
        self.ensure_seeded(snapshot)
        results: List[Dict[str, Any]] = []
        for capability_id in capabilities.compile_order(capabilities.all_ids()):
            results.append(
                self.compile(
                    capability_id, report_date=report_date, snapshot=snapshot, force=force
                )
            )
        return results

    # -- reporting ---------------------------------------------------------
    def status(self, *, refresh: bool = False) -> Dict[str, Any]:
        """What is compiled, against what, and whether it still fits.

        Deliberately answerable without calling a model, so an operator can ask
        "is anything stale" as often as they like.
        """
        settings = get_settings()
        out: Dict[str, Any] = {
            "mode": settings.dynamic_sql_mode,
            "included_tables": list(settings.included_tables),
            "models": llm.status(),
            "compiler_version": prompts.COMPILER_VERSION,
            "author_prompt_version": prompts.AUTHOR_PROMPT_VERSION,
            "verifier_prompt_version": prompts.VERIFIER_PROMPT_VERSION,
            "capabilities": {},
        }
        if settings.dynamic_sql_mode == MODE_OFF:
            out["note"] = "dynamic compilation is off; the baseline SQL files are served"
            return out

        try:
            snapshot = self._snapshot_now(force=refresh)
        except Exception as exc:  # noqa: BLE001 - status must never fail loudly
            out["error"] = f"the schema could not be read: {exc}"
            return out

        out["database"] = snapshot.database
        out["structure_fingerprint"] = snapshot.structure_fingerprint()[:16]
        out["live_fingerprint"] = snapshot.live_fingerprint()[:16]
        out["approved_tables_present"] = sorted(t.key() for t in snapshot.approved_tables())
        out["approved_tables_missing"] = sorted(
            name for name in settings.included_tables if snapshot.get(name) is None
        )

        previous = introspect.load_previous_snapshot()
        from dynamic_db import changes as change_module

        report = change_module.compare(previous, snapshot)
        out["schema_change"] = report.as_dict()

        for capability in capabilities.all_capabilities():
            record = artifacts.load(capability.id)
            stale, reason = artifacts.staleness(
                record.current,
                capability,
                snapshot,
                compiler_version=prompts.COMPILER_VERSION,
                prompt_version=prompts.AUTHOR_PROMPT_VERSION,
                rules_version=self._rules_version(capability),
            )
            entry = record.current.summary() if record.current else {"capability": capability.id}
            entry["stale"] = stale
            entry["stale_reason"] = reason
            out["capabilities"][capability.id] = entry

        out["schema_change"]["affected_capabilities"] = sorted(
            name for name, entry in out["capabilities"].items() if entry.get("stale")
        )
        return out


_service: Optional[CompilerService] = None


def get_service() -> CompilerService:
    """The process-wide compiler service."""
    global _service
    if _service is None:
        _service = CompilerService()
    return _service


def sql_for(capability_id: str, *, report_date: Optional[date] = None) -> str:
    """Convenience wrapper used by the repositories."""
    return get_service().sql_for(capability_id, report_date=report_date)

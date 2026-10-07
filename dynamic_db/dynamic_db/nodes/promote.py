"""Node 8 - put the verified artifact into service. No model.

PROMOTION IS A SEPARATE STEP ON PURPOSE. Until this node runs, the previous
verified artifact is still the one serving requests, and the candidate is
sitting beside it where an operator can read both. Nothing that failed a gate
ever replaces something that passed one.

THE BASELINE SNAPSHOT IS ADVANCED HERE, not in the change detector. A run that
noticed a change and then crashed must still see that change next time; moving
the baseline forward before the work is done would mean the second run sees a
clean database and quietly leaves a stale query in service.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from dynamic_db import artifacts, capabilities, introspect, llm, prompts
from dynamic_db.state import STATUS_NOT_APPLICABLE, STATUS_PROMOTED, CompileState

logger = logging.getLogger(__name__)


def promote_node(state: CompileState) -> Dict[str, Any]:
    capability = capabilities.get(state["capability"])

    artifact = artifacts.Artifact(
        capability_id=capability.id,
        sql=state.get("generated_sql", ""),
        origin=artifacts.ORIGIN_COMPILED,
        schema_fingerprint=state.get("schema_fingerprint", ""),
        dependency_fingerprint=state.get("dependency_fingerprint", ""),
        dependencies=state.get("dependencies", {}),
        validator_status="passed",
        verifier_status=artifacts.VERIFIED_APPROVED,
        verifier_note=state.get("verifier_note", ""),
        model_used=state.get("model_used", "") or llm.route(llm.SYSTEM_REASONING).model,
        prompt_version=prompts.AUTHOR_PROMPT_VERSION,
        compiler_version=prompts.COMPILER_VERSION,
        rules_version=state.get("rules_version", ""),
        mapping_notes=state.get("mapping_notes", ""),
    )

    if state.get("not_applicable"):
        reason = state.get("not_applicable_reason", "") or "no reason given"
        artifacts.mark_not_applicable(capability.id, reason, artifact)
        _advance_baseline(state)
        return {"status": STATUS_NOT_APPLICABLE}

    artifacts.promote(capability.id, artifact)
    _advance_baseline(state)
    logger.info(
        "dynamic[%s]: promoted. %d model call(s) spent, %d rewrite(s), %d review(s).",
        capability.id,
        state.get("llm_calls", 0),
        state.get("sql_retry_count", 0),
        state.get("verify_retry_count", 0),
    )
    return {"status": STATUS_PROMOTED}


def _advance_baseline(state: CompileState) -> None:
    """Record the schema this compile was made against as the next comparison point."""
    snapshot = state.get("snapshot")
    if snapshot is not None:
        introspect.store_previous_snapshot(snapshot)

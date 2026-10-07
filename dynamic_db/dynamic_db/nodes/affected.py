"""Node 3 - is this capability actually affected? No model.

THIS IS THE NODE THAT MAKES THE SYSTEM AFFORDABLE. Everything after it costs
reasoning-model calls; everything up to it costs a catalogue read. So the whole
question is answered here, deterministically, from the artifact's own recorded
dependencies against the schema just read:

    unaffected  ->  the verified artifact keeps serving, the graph ends, and no
                    model is called at all. This is the normal case, every day.
    affected    ->  the SQL is recompiled against the CURRENT schema.

A capability is affected when its dependency fingerprint moved, when a table or
column it reads has gone, when the prompt or compiler version changed, or when
the text of a rule IT DEPENDS ON changed. It is NOT affected because rows were
loaded, because an unrelated table gained a column, or because a different
capability's dependencies moved.

WHERE A DEPENDENCY HAS VANISHED, discovery runs -- inside the configured
boundary only -- and offers the author structurally similar candidates. They
are offered as EVIDENCE, never adopted: two tables sharing column names prove
nothing about whether they mean the same thing, and the validator and the
verifier both still have to agree before anything is promoted.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from dynamic_db import artifacts, capabilities, changes, prompts
from dynamic_db.dependencies import Dependencies
from dynamic_db.state import STATUS_REUSED, CompileState

logger = logging.getLogger(__name__)


def affected_node(state: CompileState) -> Dict[str, Any]:
    capability = capabilities.get(state["capability"])
    snapshot = state["snapshot"]
    record = artifacts.load(capability.id)

    stale, reason = artifacts.staleness(
        record.current,
        capability,
        snapshot,
        compiler_version=prompts.COMPILER_VERSION,
        prompt_version=prompts.AUTHOR_PROMPT_VERSION,
        rules_version=state.get("rules_version", ""),
    )

    forced = bool(state.get("force"))
    if not stale:
        if not forced:
            logger.info(
                "dynamic[%s]: unaffected - %s. Reusing v%s with no model call.",
                capability.id,
                reason,
                record.current.version if record.current else "?",
            )
            return {
                "affected": False,
                "affected_reason": reason,
                "status": STATUS_REUSED,
                "generated_sql": record.current.sql if record.current else "",
            }
        # The deterministic check still ran and is still reported, so a forced
        # recompile is auditable as exactly that rather than looking like a
        # change nobody can find.
        logger.warning(
            "dynamic[%s]: not stale (%s), but a recompile was explicitly requested.",
            capability.id,
            reason,
        )

    candidates: List[str] = []
    if record.current is not None:
        stored = Dependencies.from_dict(record.current.dependencies or {})
        for table in stored.tables:
            if snapshot.get(table) is None:
                expected = [column for owner, column in stored.columns if owner == table]
                found = changes.replacement_candidates(table, snapshot, expected)
                if found:
                    candidates.append(
                        f"{table} is gone. Structurally similar tables inside the approved "
                        f"discovery boundary: " + "; ".join(found)
                    )
                else:
                    candidates.append(
                        f"{table} is gone and nothing inside the approved discovery boundary "
                        f"resembles it."
                    )

    logger.warning(
        "dynamic[%s]: AFFECTED - %s. Recompiling against the current schema.",
        capability.id,
        reason,
    )
    return {
        "affected": True,
        "affected_reason": reason,
        "replacement_candidates": candidates,
    }

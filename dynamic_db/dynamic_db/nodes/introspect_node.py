"""Node 1 - read the live database and build everything downstream needs. No model.

Three artefacts come out of this node and are then SHARED, not rebuilt:

  * the snapshot          structured facts, used by every deterministic gate;
  * the schema block      the rendered text, built ONCE and handed unchanged to
                          both the author and the verifier, so the largest and
                          most repetitive part of both prompts is byte-identical
                          and a provider can prefix-cache it;
  * the selected rules    only the sections this capability declared.

The snapshot may arrive already built. When several capabilities are compiled
in one run they share one read of the catalogue -- re-reading it per capability
would be six round trips to learn the same thing six times.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from dynamic_db import capabilities, introspect
from dynamic_db import rules as rules_module
from dynamic_db.state import CompileState

logger = logging.getLogger(__name__)


def introspect_node(state: CompileState) -> Dict[str, Any]:
    capability = capabilities.get(state["capability"])

    snapshot = state.get("snapshot")
    if snapshot is None or not snapshot.tables:
        snapshot = introspect.introspect()

    # Only the objects the capability's CURRENT artifact reads would be an
    # obvious narrowing here, and it is the wrong one: a capability whose table
    # has been replaced must be able to see what replaced it. The approved
    # allowlist is the boundary, and it is already small by construction.
    schema_block = introspect.render_schema(snapshot)

    # The capability itself owns how its rule text is assembled -- from
    # however many documents, sliced however the caller decided. This engine
    # only ever asks for the result.
    rules_text = capability.rules_text()

    logger.info(
        "dynamic[%s]: schema read - %d approved table(s), %d chars of schema, "
        "%d chars of rules",
        capability.id,
        len(snapshot.approved_tables()),
        len(schema_block),
        len(rules_text),
    )

    return {
        "snapshot": snapshot,
        "schema_fingerprint": snapshot.structure_fingerprint(),
        "live_fingerprint": snapshot.live_fingerprint(),
        "schema_block": schema_block,
        "rules_text": rules_text,
        "rules_version": rules_module.version(rules_text),
    }

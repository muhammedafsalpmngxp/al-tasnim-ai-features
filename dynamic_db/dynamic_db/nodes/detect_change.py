"""Node 2 - what moved in the database since last time. No model.

Compares the snapshot just read against the one recorded at the end of the last
run and produces the machine-readable change report an operator reads to
understand why a recompile happened.

IT DOES NOT DECIDE ANYTHING. Whether a change affects THIS capability is the
next node's job, from the capability's own recorded dependencies. Keeping the
two apart is what stops "something changed somewhere" turning into "recompile
everything" -- which is the expensive default this whole package exists to
avoid.

The recorded baseline is only advanced once a run has actually dealt with the
change (see the promote node). A run that crashed halfway must still see the
change next time.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from dynamic_db import changes, introspect
from dynamic_db.state import CompileState

logger = logging.getLogger(__name__)


def detect_change_node(state: CompileState) -> Dict[str, Any]:
    snapshot = state.get("snapshot")
    if snapshot is None:
        return {"schema_changes": {}, "change_lines": []}

    previous = introspect.load_previous_snapshot()
    report = changes.compare(previous, snapshot)

    if report.first_run:
        logger.info("dynamic: first run against this database - no baseline to compare")
    elif report.changed:
        logger.warning(
            "dynamic: the database CHANGED - %s (structure %s -> %s)",
            report.summary(),
            report.structure_fingerprint_old[:12],
            report.structure_fingerprint_new[:12],
        )
        for line in changes.describe(report):
            logger.warning("dynamic:   %s", line)
    else:
        logger.debug("dynamic: no structural change")

    return {
        "schema_changes": report.as_dict(),
        # Bounded and already formatted, so the author's prompt can carry the
        # changes without carrying the whole report.
        "change_lines": changes.describe(report, limit=12),
    }

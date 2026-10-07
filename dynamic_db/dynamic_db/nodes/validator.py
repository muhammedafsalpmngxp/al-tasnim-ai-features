"""Node 5 - the hard gate. Deterministic, model-free, before any database round trip.

NO MODEL IS INVOLVED, AND THAT IS THE POINT. This is the boundary that
guarantees the application can never modify the source database, whatever
privileges the login holds and whatever a model was persuaded to write.

It also extracts the candidate's DEPENDENCIES here rather than asking the
author to declare them. A model that reports its own footprint is a model that
can under-report it, and an under-recorded dependency is a column whose removal
nobody notices until the report fails one morning.

A failure here is a MECHANICAL fault with an obvious fix, so the exact
deterministic message goes straight back to the author and costs one attempt
from the SQL budget -- not from the review budget, which is reserved for
disagreements about meaning.

Running before execution is not an optimisation. A rejected query costs
microseconds here; reaching the database with it costs a connection, a scan,
and then a reasoning-model review of something that was never going to work.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from dynamic_db import capabilities, dependencies as deps, validation
from dynamic_db.state import CompileState

logger = logging.getLogger(__name__)


def validator_node(state: CompileState) -> Dict[str, Any]:
    capability = capabilities.get(state["capability"])
    sql = state.get("generated_sql", "")
    snapshot = state["snapshot"]

    problems = validation.validate(sql, capability, snapshot)

    if problems:
        logger.warning(
            "dynamic[%s]: validation rejected the candidate (%d problem(s))",
            capability.id,
            len(problems),
        )
        # Each problem is logged, not just the count. A bare count is unusable
        # when a capability burns its whole budget: the log says it was
        # rejected three times and never says what for.
        for problem in problems:
            logger.warning("dynamic[%s]:   - %s", capability.id, problem[:220])
        return {
            "validation_errors": problems,
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
        }

    footprint = deps.extract(sql, snapshot)
    fingerprint = snapshot.dependency_fingerprint(footprint.tables, footprint.columns)
    logger.info(
        "dynamic[%s]: validation passed - one read-only statement over %s",
        capability.id,
        deps.summarise(footprint),
    )
    return {
        "validation_errors": [],
        "dependencies": footprint.as_dict(),
        "dependency_fingerprint": fingerprint,
    }

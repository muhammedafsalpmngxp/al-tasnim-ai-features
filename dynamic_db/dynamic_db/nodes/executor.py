"""Node 6 - actually run the candidate, once, bounded. No model.

A QUERY THAT HAS NEVER RUN IS NOT EVIDENCE OF ANYTHING. Everything before this
node reasons about text; this is where the database gets a say, and it settles
the questions text cannot: whether the result set really carries the aliases it
appeared to, whether a single-row capability really returns one row, and
whether a comparison the type system permitted actually converts on real data.

BOUNDED AT THE SOURCE, FOR THREE REASONS AT ONCE.
  * Cost      only a handful of rows is fetched, so verifying a capability
              costs the same whether its query returns ten rows or a million.
  * Safety    a compile probe must never be able to pull a production-sized
              result into memory.
  * Honesty   what the reviewer is shown is a SAMPLE and is labelled as one, so
              it cannot be mistaken for the population.

Only the validator's output reaches this node. No model ever hands SQL straight
to the database.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List

import pyodbc

from dynamic_db.db import assert_read_only, get_connection
from dynamic_db.config import get_settings
from dynamic_db import capabilities, sqlcheck, validation
from dynamic_db.state import CompileState

logger = logging.getLogger(__name__)

#: Longest a single cell may be in the sample handed to the reviewer. A free
#: text column can hold a kilobyte, and twenty of those would be most of the
#: prompt -- for a value nobody is judging the query on.
_MAX_CELL_CHARS = 120


def _truncate(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (int, float, bool)):
        return value
    text = str(value)
    return text if len(text) <= _MAX_CELL_CHARS else text[:_MAX_CELL_CHARS] + "..."


def _run(sql: str, params: List[Any], limit: int):
    """(columns, sample_rows, total_fetched, elapsed_ms).

    ``total_fetched`` counts rows past the sample too, up to a hard ceiling, so
    a single-row contract can be checked without ever holding a large result.
    """
    assert_read_only(sql)
    settings = get_settings()
    started = time.perf_counter()
    with get_connection() as connection:
        connection.timeout = settings.dynamic_execution_timeout
        cursor = connection.cursor()
        try:
            cursor.execute(sql, *params)
            columns = [column[0] for column in cursor.description or []]
            sample: List[Dict[str, Any]] = []
            total = 0
            # A ceiling well above the sample: enough to tell "one row" from
            # "many rows" and to report a realistic size, without ever being a
            # reason to hold a production result set in memory.
            ceiling = max(limit * 20, 200)
            while total < ceiling:
                batch = cursor.fetchmany(50)
                if not batch:
                    break
                for row in batch:
                    if len(sample) < limit:
                        sample.append(
                            {name: _truncate(value) for name, value in zip(columns, row)}
                        )
                    total += 1
        finally:
            cursor.close()
    return columns, sample, total, (time.perf_counter() - started) * 1000


def executor_node(state: CompileState) -> Dict[str, Any]:
    capability = capabilities.get(state["capability"])
    sql = state.get("generated_sql", "")
    params = list(state.get("probe_params") or [])
    settings = get_settings()

    if len(params) != capability.parameter_count:
        # Never invented. A capability whose probe parameters could not be
        # derived from a verified source is reported as exactly that, rather
        # than executed against a value somebody made up.
        message = (
            f"{capability.id} binds {capability.parameter_count} parameter(s) but "
            f"{len(params)} were supplied for the probe, so it cannot be executed. "
            f"Parameters are derived from a real, verified source - never invented."
        )
        logger.warning("dynamic[%s]: %s", capability.id, message)
        return {"execution_ok": False, "execution_error": message}

    try:
        columns, sample, total, elapsed_ms = _run(sql, params, settings.dynamic_sample_rows)
    except pyodbc.Error as exc:
        # The database's own message, minus anything environmental. This is the
        # single most actionable rewrite instruction available: it says exactly
        # which conversion, join or name the schema would not accept.
        sqlstate = exc.args[0] if exc.args else "unknown"
        detail = str(exc.args[1]) if len(exc.args) > 1 else str(exc)
        message = f"The database refused the query (SQLSTATE {sqlstate}): {detail[:400]}"
        logger.warning("dynamic[%s]: %s", capability.id, message)
        return {
            "execution_ok": False,
            "execution_error": message,
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
        }
    except Exception as exc:  # noqa: BLE001 - a compile must never take the app down
        logger.exception("dynamic[%s]: the probe execution failed", capability.id)
        return {
            "execution_ok": False,
            "execution_error": f"The probe could not be executed: {exc}",
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
        }

    contract = validation.execution_problems(capability, total, columns)
    if contract:
        logger.warning(
            "dynamic[%s]: the executed result breaks the capability contract", capability.id
        )
        for problem in contract:
            logger.warning("dynamic[%s]:   - %s", capability.id, problem[:220])
        return {
            "execution_ok": False,
            "execution_error": "\n".join(contract),
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
        }

    summary = {
        "rows_returned": total,
        "rows_capped": total >= max(settings.dynamic_sample_rows * 20, 200),
        "columns": len(columns),
        "duration_ms": round(elapsed_ms, 1),
        "sample_size": len(sample),
    }
    concerns = sqlcheck.check(sql, state["snapshot"], capability)

    logger.info(
        "dynamic[%s]: probe ran in %.0f ms, %d row(s), %d deterministic concern(s)",
        capability.id,
        elapsed_ms,
        total,
        len(concerns),
    )
    return {
        "execution_ok": True,
        "execution_error": "",
        "execution_summary": summary,
        "execution_sample": sample,
        "concerns": concerns,
    }

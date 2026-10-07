"""The compile graph. A real LangGraph ``StateGraph``, not a sequence of calls.

    START
      |
    INTROSPECT            read the live database                    (no model)
      |
    DETECT_CHANGE         what moved since last time                (no model)
      |
    AFFECTED              does it touch THIS capability?            (no model)
      |-- no  --> END  the verified artifact keeps serving; nothing is spent
      |
    SQL_AUTHOR            resolve concepts against the CURRENT schema  (reasoning)
      |
    VALIDATOR             safety, schema and contract                (no model)
      |-- failed, budget left ----> SQL_AUTHOR
      |-- failed, budget spent ---> ABANDON
      |
    EXECUTOR              run it once, bounded, read-only            (no model)
      |-- failed, budget left ----> SQL_AUTHOR
      |-- failed, budget spent ---> ABANDON
      |
    VERIFIER              does it MEAN what the rules say?           (reasoning)
      |-- rejected, budget left --> SQL_AUTHOR
      |-- rejected, budget spent -> ABANDON
      |-- not applicable ---------> PROMOTE (recorded, never approved)
      |-- approved ---------------> PROMOTE
                                      |
                                    END

ABANDON is a real node rather than an edge to END, because running out of
attempts is an OUTCOME and has to be recorded as one. Routed straight to END,
a compile that gave up ended in the state it started in -- "pending" -- and the
caller could not tell "still going" from "tried three times and failed", which
is exactly the moment somebody needs to be told.

WHY A GRAPH AND NOT A FUNCTION THAT CALLS SEVEN FUNCTIONS. The edges are the
design. Two different failures route back to the same author node carrying
different evidence; two different budgets bound two different kinds of failure;
and a rejection resets one counter while consuming the other. Written as
straight-line code those rules end up scattered through the middle of the
nodes, where nobody can see that they are bounded.

EVERY LOOP IS BOUNDED. Total work per capability is capped at
(verify retries + 1) x (sql retries + 1) author calls, and the recursion limit
below is a second, independent stop so no edge condition can ever spin.
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from langgraph.graph import END, START, StateGraph

from dynamic_db.nodes import (
    affected_node,
    detect_change_node,
    executor_node,
    introspect_node,
    promote_node,
    sql_author_node,
    validator_node,
    verifier_node,
)
from dynamic_db.state import STATUS_FAILED, CompileState

logger = logging.getLogger(__name__)

# Node names. Stable strings: they appear in logs and in the tests that assert
# which route a compile took.
N_INTROSPECT = "introspect"
N_DETECT_CHANGE = "detect_change"
N_AFFECTED = "affected"
N_AUTHOR = "sql_author"
N_VALIDATOR = "validator"
N_EXECUTOR = "executor"
N_VERIFIER = "verifier"
N_PROMOTE = "promote"
N_ABANDON = "abandon"


def abandon_node(state: CompileState) -> Dict[str, Any]:
    """Record that the bounded budget ran out, and why. No model.

    The reason is assembled from what the last gate actually said, so the log
    and the returned outcome name the real obstacle rather than "failed".
    """
    if state.get("validation_errors"):
        reason = "the rewrites never passed validation: " + state["validation_errors"][0]
    elif state.get("execution_error"):
        reason = "the rewrites never ran: " + state["execution_error"][:200]
    elif state.get("verifier_feedback"):
        reason = "the review kept rejecting it: " + state["verifier_feedback"][:200]
    else:
        reason = "the bounded attempts were exhausted without a verified query"
    logger.warning("dynamic[%s]: giving up - %s", state.get("capability", ""), reason[:220])
    return {"status": STATUS_FAILED, "failure_reason": reason}


def _after_affected(state: CompileState) -> str:
    """Nothing changed for this capability -> stop before spending anything."""
    if state.get("status") == STATUS_FAILED:
        return END
    return N_AUTHOR if state.get("affected", True) else END


def _after_author(state: CompileState) -> str:
    """A model outage ends the run; a malformed reply is a mechanical fault."""
    if state.get("status") == STATUS_FAILED:
        return END
    if state.get("validation_errors"):
        # The author itself reported a formatting fault (no SQL block). Treated
        # exactly like a validator rejection so one budget governs both.
        return N_AUTHOR if _sql_budget_left(state) else N_ABANDON
    return N_VALIDATOR


def _sql_budget_left(state: CompileState) -> bool:
    return state.get("sql_retry_count", 0) <= state.get("max_sql_retries", 2)


def _verify_budget_left(state: CompileState) -> bool:
    return state.get("verify_retry_count", 0) <= state.get("max_verify_retries", 2)


def _after_validator(state: CompileState) -> str:
    if not state.get("validation_errors"):
        return N_EXECUTOR
    if _sql_budget_left(state):
        return N_AUTHOR
    logger.warning(
        "dynamic[%s]: out of rewrite attempts after validation",
        state.get("capability", ""),
    )
    return N_ABANDON


def _after_executor(state: CompileState) -> str:
    if state.get("execution_ok"):
        return N_VERIFIER
    if _sql_budget_left(state):
        return N_AUTHOR
    logger.warning(
        "dynamic[%s]: out of rewrite attempts after execution",
        state.get("capability", ""),
    )
    return N_ABANDON


def _after_verifier(state: CompileState) -> str:
    # not_applicable is an OUTCOME, not a failure: it is recorded through the
    # same promotion path so the decision and its reason are stored, and the
    # capability is reported unavailable rather than silently answering nothing.
    if state.get("not_applicable"):
        return N_PROMOTE
    if state.get("verifier_ok"):
        return N_PROMOTE
    if _verify_budget_left(state):
        return N_AUTHOR
    logger.warning(
        "dynamic[%s]: out of review attempts", state.get("capability", "")
    )
    return N_ABANDON


def build_graph():
    """Compile the graph once. Structure only -- it holds no state of its own."""
    graph = StateGraph(CompileState)

    graph.add_node(N_INTROSPECT, introspect_node)
    graph.add_node(N_DETECT_CHANGE, detect_change_node)
    graph.add_node(N_AFFECTED, affected_node)
    graph.add_node(N_AUTHOR, sql_author_node)
    graph.add_node(N_VALIDATOR, validator_node)
    graph.add_node(N_EXECUTOR, executor_node)
    graph.add_node(N_VERIFIER, verifier_node)
    graph.add_node(N_PROMOTE, promote_node)
    graph.add_node(N_ABANDON, abandon_node)

    graph.add_edge(START, N_INTROSPECT)
    graph.add_edge(N_INTROSPECT, N_DETECT_CHANGE)
    graph.add_edge(N_DETECT_CHANGE, N_AFFECTED)

    graph.add_conditional_edges(
        N_AFFECTED, _after_affected, {N_AUTHOR: N_AUTHOR, END: END}
    )
    graph.add_conditional_edges(
        N_AUTHOR,
        _after_author,
        {N_VALIDATOR: N_VALIDATOR, N_AUTHOR: N_AUTHOR, N_ABANDON: N_ABANDON, END: END},
    )
    graph.add_conditional_edges(
        N_VALIDATOR,
        _after_validator,
        {N_EXECUTOR: N_EXECUTOR, N_AUTHOR: N_AUTHOR, N_ABANDON: N_ABANDON},
    )
    graph.add_conditional_edges(
        N_EXECUTOR,
        _after_executor,
        {N_VERIFIER: N_VERIFIER, N_AUTHOR: N_AUTHOR, N_ABANDON: N_ABANDON},
    )
    graph.add_conditional_edges(
        N_VERIFIER,
        _after_verifier,
        {N_PROMOTE: N_PROMOTE, N_AUTHOR: N_AUTHOR, N_ABANDON: N_ABANDON},
    )
    graph.add_edge(N_PROMOTE, END)
    graph.add_edge(N_ABANDON, END)

    return graph.compile()


_COMPILED = None


def compiled_graph():
    """The compiled graph, built once per process."""
    global _COMPILED
    if _COMPILED is None:
        _COMPILED = build_graph()
    return _COMPILED


def recursion_limit(state: CompileState) -> int:
    """A hard ceiling on node executions, independent of the retry counters.

    Belt and braces on purpose. The counters are what SHOULD bound the loops;
    this is what bounds them if a future edge condition gets one wrong. Derived
    from the configured budgets so raising a retry limit does not silently make
    this the binding constraint instead.
    """
    per_attempt = 4  # author -> validator -> executor -> verifier
    attempts = (state.get("max_verify_retries", 2) + 1) * (
        state.get("max_sql_retries", 2) + 1
    )
    return 8 + per_attempt * attempts


def run(state: CompileState) -> Dict[str, Any]:
    """Run one capability through the graph and return its final state."""
    return compiled_graph().invoke(
        state, config={"recursion_limit": recursion_limit(state)}
    )

"""The typed state one capability's compile carries through the graph.

IT CARRIES WHAT THE NEXT NODE NEEDS, AND NOT A COPY OF EVERYTHING. Two rules
keep it that way, and both are cost decisions as much as design ones:

  * ONE COPY OF EACH LARGE STRING. The schema block and the selected rule text
    are built once, in the first node, and every later node reads the same
    object. Duplicating them into per-node fields would multiply the largest
    payloads in the system by the number of nodes that touch them.

  * STRUCTURED FACTS, NOT PROSE. The snapshot travels as the object
    introspection built, not as text; only the block that actually goes into a
    prompt is rendered, and it is rendered identically for the author and the
    verifier so a provider can prefix-cache it across both calls.

RESULT DATA IS BOUNDED AT THE SOURCE. ``execution_sample`` is a handful of rows
with every cell truncated, and ``execution_summary`` is counts. The full result
of a compile probe never enters the state and therefore can never reach a
prompt -- which is what keeps the cost of verifying a capability flat no matter
how much data the query returns.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from dynamic_db.introspect import SchemaSnapshot

# Terminal statuses. Stable strings: they are persisted and asserted on.
STATUS_PENDING = "pending"
STATUS_REUSED = "reused"  # nothing changed; the verified artifact still serves
STATUS_PROMOTED = "promoted"  # a new artifact was verified and put into service
STATUS_NOT_APPLICABLE = "not_applicable"  # this database cannot represent it
STATUS_FAILED = "failed"  # bounded attempts exhausted, or a gate refused


class CompileState(TypedDict, total=False):
    """One capability's journey from "the schema moved" to "this SQL serves"."""

    # -- identity ---------------------------------------------------------
    run_id: str
    capability: str
    #: Parameters the probe execution binds. Derived from the request or from
    #: another capability's verified result -- never a hardcoded identifier.
    probe_params: List[Any]
    probe_params_source: str

    # -- the database, as it is now ---------------------------------------
    snapshot: Optional[SchemaSnapshot]
    schema_fingerprint: str
    live_fingerprint: str
    #: The rendered block, built once and shared byte-identically by the author
    #: and the verifier.
    schema_block: str

    # -- the rules, sliced to this capability ------------------------------
    #: Whatever the capability's ``rules()`` callable returned -- ONE string.
    #: This engine does not know or care how many source documents it came
    #: from; that shape belongs to the application that registered the
    #: capability, not to the compile graph.
    rules_text: str
    rules_version: str

    # -- what moved --------------------------------------------------------
    schema_changes: Dict[str, Any]
    change_lines: List[str]
    affected: bool
    affected_reason: str
    #: An operator asked for a recompile explicitly. The deterministic check
    #: still runs and is still reported -- this only says "do it anyway", so a
    #: forced run and an automatic one take exactly the same path.
    force: bool
    replacement_candidates: List[str]

    # -- the candidate -----------------------------------------------------
    generated_sql: str
    mapping_notes: str
    dependencies: Dict[str, Any]
    dependency_fingerprint: str
    model_used: str

    # -- the gates ---------------------------------------------------------
    validation_errors: List[str]
    execution_ok: bool
    execution_error: str
    execution_summary: Dict[str, Any]
    execution_sample: List[Dict[str, Any]]
    concerns: List[str]

    verifier_ok: bool
    verifier_feedback: str
    #: Every rejection this capability has already received, so a later review
    #: cannot demand the opposite of what an earlier one demanded.
    verifier_history: List[str]
    verifier_note: str
    not_applicable: bool
    not_applicable_reason: str

    # -- budget ------------------------------------------------------------
    sql_retry_count: int
    verify_retry_count: int
    max_sql_retries: int
    max_verify_retries: int
    llm_calls: int

    # -- outcome -----------------------------------------------------------
    status: str
    failure_reason: str


def initial(
    *,
    run_id: str,
    capability: str,
    probe_params: Optional[List[Any]] = None,
    snapshot: Optional[SchemaSnapshot] = None,
    max_sql_retries: int = 2,
    max_verify_retries: int = 2,
) -> CompileState:
    """A state with every counter at zero and every list empty.

    Explicit rather than relying on ``.get`` defaults everywhere: a counter that
    is absent instead of zero is how a bounded retry loop quietly becomes an
    unbounded one.
    """
    return CompileState(
        run_id=run_id,
        capability=capability,
        probe_params=list(probe_params or []),
        probe_params_source="",
        snapshot=snapshot,
        schema_fingerprint="",
        live_fingerprint="",
        schema_block="",
        rules_text="",
        rules_version="",
        schema_changes={},
        change_lines=[],
        affected=True,
        affected_reason="",
        force=False,
        replacement_candidates=[],
        generated_sql="",
        mapping_notes="",
        dependencies={},
        dependency_fingerprint="",
        model_used="",
        validation_errors=[],
        execution_ok=False,
        execution_error="",
        execution_summary={},
        execution_sample=[],
        concerns=[],
        verifier_ok=False,
        verifier_feedback="",
        verifier_history=[],
        verifier_note="",
        not_applicable=False,
        not_applicable_reason="",
        sql_retry_count=0,
        verify_retry_count=0,
        max_sql_retries=max_sql_retries,
        max_verify_retries=max_verify_retries,
        llm_calls=0,
        status=STATUS_PENDING,
        failure_reason="",
    )


def outcome(state: CompileState) -> Dict[str, Any]:
    """A compact, non-secret description of how one compile ended."""
    return {
        "capability": state.get("capability", ""),
        "status": state.get("status", STATUS_PENDING),
        "affected": state.get("affected", False),
        "reason": state.get("affected_reason", "") or state.get("failure_reason", ""),
        "llm_calls": state.get("llm_calls", 0),
        "sql_retries": state.get("sql_retry_count", 0),
        "verify_retries": state.get("verify_retry_count", 0),
        "model_used": state.get("model_used", ""),
        "not_applicable_reason": state.get("not_applicable_reason", ""),
        "validation_errors": list(state.get("validation_errors") or [])[:3],
        "verifier_note": state.get("verifier_note", ""),
    }

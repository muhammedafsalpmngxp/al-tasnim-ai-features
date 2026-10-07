"""Node 4 - write the query. The reasoning model, and one of only two calls that use it.

WHAT THIS NODE IS ACTUALLY FOR is not "writing SQL" -- a template could do that.
It is resolving a BUSINESS CONCEPT against a schema nobody has described to it:
deciding which of the objects that exist right now carries "the daily task
source", and being able to say why. That is a judgement, it is the one this
system genuinely needs a reasoning model for, and it is why routing it to the
cheap model would be the most expensive saving available -- the result would
parse, run, and measure the wrong thing.

THE PROMPT IS ASSEMBLED FOR CACHING, NOT FOR READABILITY. The schema block goes
first because it is the largest part and is byte-identical to the one the
verifier will see; the capability contract next because it is fixed per
capability; and everything that varies per attempt -- the schema changes, the
validator's complaint, the reviewer's instruction -- goes last, where it cannot
invalidate the cached prefix above it.

IT NEVER TOUCHES THE DATABASE. It writes text. The executor runs only what the
validator has already passed.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

from dynamic_db import capabilities, llm, prompts
from dynamic_db.state import STATUS_FAILED, CompileState

logger = logging.getLogger(__name__)

_SQL_BLOCK = re.compile(r"```sql\s*(.*?)```", re.DOTALL | re.IGNORECASE)
_NOTES_BLOCK = re.compile(r"```notes\s*(.*?)```", re.DOTALL | re.IGNORECASE)
#: Mapping notes are re-sent to the verifier, so they need a bound -- but never
#: a hard slice, which can cut an explanation in half and leave the reviewer
#: adjudicating a fragment.
_MAX_NOTES_CHARS = 1500


def _parse(text: str) -> tuple:
    """(sql, notes) from the model's reply.

    A reply with no fenced SQL block at all is treated as no SQL, not as
    "maybe the whole reply is SQL". Guessing there would hand the validator a
    paragraph of prose to reject, spending an attempt on a formatting slip.
    """
    sql_match = _SQL_BLOCK.search(text or "")
    notes_match = _NOTES_BLOCK.search(text or "")
    sql = (sql_match.group(1).strip() if sql_match else "").strip()
    notes = (notes_match.group(1).strip() if notes_match else "").strip()
    return sql, prompts.truncate(notes, _MAX_NOTES_CHARS)


def _build_user_prompt(state: CompileState, capability) -> str:
    parts: List[str] = []

    # 1. The schema. Largest, most repetitive, identical for the verifier.
    parts.append(
        prompts.schema_heading(bool(state.get("change_lines")))
        + "\n"
        + state.get("schema_block", "")
    )

    # 2. The contract. Fixed for this capability across every attempt.
    parts.append(
        prompts.capability_block(
            capability_id=capability.id,
            intent=capability.intent,
            concepts=capability.concepts,
            required_columns=capability.required_columns,
            parameters=[
                {"name": p.name, "kind": p.kind, "description": p.description}
                for p in capability.parameters
            ],
            single_row=capability.single_row,
            requires_grain_resolution=capability.requires_grain_resolution,
        )
    )

    # 3. Everything below here varies per attempt.
    if state.get("change_lines"):
        parts.append(
            "WHAT CHANGED IN THIS DATABASE SINCE THIS CAPABILITY WAS LAST COMPILED. "
            "The rules were written before this, so where they name something that is no "
            "longer here, resolve the concept against the schema above instead:\n"
            + prompts.numbered(state["change_lines"])
        )
    if state.get("replacement_candidates"):
        parts.append(
            "AN OBJECT THIS CAPABILITY USED TO READ HAS GONE. These are structurally similar "
            "tables inside the approved boundary. They are EVIDENCE, not an answer: a shared "
            "column name proves nothing about meaning. Use one only if the rules and the "
            "structure together justify it, and say so in your notes. If none of them "
            "genuinely carries the concept, say the capability cannot be written:\n"
            + prompts.numbered(state["replacement_candidates"])
        )
    if state.get("validation_errors"):
        parts.append(
            "YOUR PREVIOUS ATTEMPT FAILED THESE MECHANICAL CHECKS. They are not opinions and "
            "cannot be argued with - fix each one exactly:\n"
            + prompts.numbered(state["validation_errors"])
        )
    if state.get("execution_error"):
        parts.append(
            "YOUR PREVIOUS ATTEMPT FAILED WHEN THE DATABASE RAN IT:\n"
            + state["execution_error"]
        )
    if state.get("verifier_feedback"):
        parts.append(
            "THE REVIEWER REJECTED YOUR PREVIOUS QUERY AND ASKED FOR THIS ONE CHANGE:\n"
            + state["verifier_feedback"]
        )
    if state.get("verifier_history") and len(state["verifier_history"]) > 1:
        parts.append(
            "EARLIER REVIEW INSTRUCTIONS, so you do not undo one while satisfying another:\n"
            + prompts.numbered(state["verifier_history"][:-1])
        )

    parts.append(
        "Write the query now, in the two fenced blocks described above, and nothing else."
    )
    return "\n\n".join(parts)


def sql_author_node(state: CompileState) -> Dict[str, Any]:
    capability = capabilities.get(state["capability"])
    user_prompt = _build_user_prompt(state, capability)
    system_prompt = prompts.author_system(state.get("rules_text", ""))

    try:
        reply = llm.complete(
            system_prompt,
            user_prompt,
            system=llm.SYSTEM_REASONING,
            node="reasoning_sql_author",
            capability=capability.id,
            temperature=0.0,
        )
    except llm.ModelUnavailable as exc:
        # A model outage is not a reason to serve a query nobody wrote. Fail
        # closed: the previous verified artifact keeps serving if it is still
        # valid, and is reported unavailable if it is not.
        logger.warning("dynamic[%s]: the author call failed - %s", capability.id, exc)
        return {
            "status": STATUS_FAILED,
            "failure_reason": f"the SQL author could not be reached: {exc}",
            "llm_calls": state.get("llm_calls", 0) + 1,
        }

    sql, notes = _parse(reply)
    calls = state.get("llm_calls", 0) + 1

    if not sql:
        logger.warning("dynamic[%s]: the author returned no SQL block", capability.id)
        return {
            "generated_sql": "",
            "mapping_notes": notes,
            "llm_calls": calls,
            "model_used": llm.route(llm.SYSTEM_REASONING).model,
            "validation_errors": [
                "Your reply contained no ```sql fenced block. Reply with exactly the two "
                "fenced blocks described, and nothing else."
            ],
            "sql_retry_count": state.get("sql_retry_count", 0) + 1,
        }

    logger.info(
        "dynamic[%s]: authored %d chars of SQL (attempt %d)",
        capability.id,
        len(sql),
        state.get("sql_retry_count", 0) + 1,
    )
    return {
        "generated_sql": sql,
        "mapping_notes": notes,
        "model_used": llm.route(llm.SYSTEM_REASONING).model,
        "llm_calls": calls,
        # Cleared so the next validator run starts from a clean slate; the
        # reviewer's feedback is NOT cleared here -- see the verifier node for
        # why it survives until the rewrite has actually been judged.
        "validation_errors": [],
        "execution_error": "",
    }

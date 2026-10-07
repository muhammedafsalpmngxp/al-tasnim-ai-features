"""Node 7 - does this query MEAN what the rules say? The second reasoning call.

WHY THIS IS WORTH A REASONING CALL WHEN EVERY GATE BEFORE IT WAS FREE.
Each earlier gate answers a mechanical question: is it read-only, does every
object exist, does it carry the right aliases, does it run. None of them can
answer the only question that actually matters -- does this query implement the
business capability the rules describe? A query can be safe, well-formed,
contract-perfect and measure entirely the wrong thing, and it would then run
unattended for months reporting a confident number about nothing.

WHAT IT IS SHOWN, AND WHAT IT IS NOT.
The same schema block the author saw (byte-identical, so the prefix caches),
the same rule sections the author was given, the query, the deterministic
concerns, a one-object execution summary and a bounded row sample. Never the
full result. The prompt says so explicitly, because a model shown twenty rows
will otherwise reason about the population from them.

IT FAILS CLOSED. An unreadable verdict is a REJECTION, not an approval.
Defaulting the other way lets a malformed reply -- a trailing comma is enough --
silently turn a rejection into an approval, with nothing in the log to say it
happened.

IT IS SHOWN ITS OWN PREVIOUS INSTRUCTIONS. A reviewer that cannot see what it
already demanded has no way to notice it is reversing itself, and two attempts
spent obeying opposite instructions is a correct query recorded as a failure.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from dynamic_db.config import get_settings
from dynamic_db import capabilities, llm, prompts
from dynamic_db.state import CompileState

logger = logging.getLogger(__name__)

#: Feedback is re-sent on every rewrite, so it needs a bound -- never a hard
#: slice, which can cut inside the instruction and leave the author acting on a
#: fragment of it.
_MAX_FEEDBACK_CHARS = 1000


def _build_user_prompt(state: CompileState, capability) -> str:
    settings = get_settings()
    parts: List[str] = []

    # Byte-identical to the author's first block, deliberately. Same heading,
    # same schema text, same position -- so the provider can serve this prefix
    # from its cache instead of re-reading the largest part of both prompts.
    parts.append(
        prompts.schema_heading(bool(state.get("change_lines")))
        + "\n"
        + state.get("schema_block", "")
    )
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
    parts.append("THE QUERY AS WRITTEN:\n```sql\n" + state.get("generated_sql", "") + "\n```")

    if state.get("mapping_notes"):
        parts.append(
            "THE AUTHOR'S OWN CONCEPT MAPPING. Check it against the query: a mapping that "
            "describes something the SQL does not do is itself the defect.\n"
            + state["mapping_notes"]
        )
    if state.get("change_lines"):
        parts.append(
            "THE DATABASE CHANGED SINCE THIS CAPABILITY WAS LAST COMPILED, which is why it "
            "was rewritten:\n" + prompts.numbered(state["change_lines"])
        )

    parts.append(
        "MECHANICAL CHECKS: PASSED. One read-only statement; every object exists inside the "
        "approved boundary; parameters bound; required output aliases present; the result "
        "shape matches the contract. Do not re-check any of that."
    )
    parts.append(
        "WHAT IT RETURNED WHEN RUN:\n"
        + json.dumps(state.get("execution_summary") or {}, sort_keys=True)
        + f"\n\nRow sample (at most {settings.dynamic_sample_rows} rows, for judging the KIND "
        "of record returned - never count, total or rank from it):\n"
        + json.dumps(state.get("execution_sample") or [], default=str)[:4000]
    )

    if state.get("concerns"):
        parts.append(
            "DETERMINISTIC CONCERNS TO ADJUDICATE. Each is derived from the database's own "
            "declared keys, measured grain and measured scales - facts about the query, not "
            "opinions. They are conservative and can flag something legitimate, so decide "
            "each on its merits and reject only where it genuinely affects the result:\n"
            + prompts.numbered(state["concerns"])
        )

    if state.get("verifier_history"):
        parts.append(
            "YOU ALREADY REJECTED AN EARLIER VERSION OF THIS QUERY, SAYING:\n"
            + prompts.numbered(state["verifier_history"])
            + "\n\nThe author has acted on that. Do NOT now ask for the opposite of what you "
            "asked for before - if your earlier instruction was wrong, say so plainly in the "
            "feedback and give the corrected instruction ONCE. If the author did what you "
            "asked and the query is now sound, APPROVE it."
        )

    parts.append(
        "Does this query correctly implement the capability against THIS database? Reply with "
        "the JSON verdict and nothing else."
    )
    return "\n\n".join(parts)


def verifier_node(state: CompileState) -> Dict[str, Any]:
    capability = capabilities.get(state["capability"])
    system_prompt = prompts.verifier_system(state.get("rules_text", ""))
    user_prompt = _build_user_prompt(state, capability)
    calls = state.get("llm_calls", 0) + 1

    try:
        reply = llm.complete(
            system_prompt,
            user_prompt,
            system=llm.SYSTEM_REASONING,
            node="reasoning_verifier",
            capability=capability.id,
            temperature=0.0,
            json_object=True,
        )
    except llm.ModelUnavailable as exc:
        # A provider failure is not evidence the query is good. Fail closed,
        # exactly as an unreadable verdict does -- but say plainly that the
        # review did not happen, so nobody reads this as a considered rejection.
        logger.warning("dynamic[%s]: the review call failed - %s", capability.id, exc)
        return _reject(
            state,
            "The automated review could not be completed. Re-check the query against the "
            "schema and the rules and correct anything that looks wrong.",
            calls,
        )

    verdict = llm.extract_json(reply)
    if verdict is None or "ok" not in verdict:
        logger.warning(
            "dynamic[%s]: the verdict was unreadable (%d chars) - failing closed, not approving",
            capability.id,
            len(reply or ""),
        )
        return _reject(
            state,
            "The automated review returned nothing usable. Re-check the query against the "
            "schema and the rules and correct anything that looks wrong.",
            calls,
        )

    note = str(verdict.get("note") or "").strip()

    if bool(verdict.get("not_applicable")):
        reason = str(verdict.get("reason") or "").strip() or note or "no reason given"
        logger.warning(
            "dynamic[%s]: NOT APPLICABLE to this database - %s", capability.id, reason[:200]
        )
        return {
            "not_applicable": True,
            "not_applicable_reason": reason,
            # Nothing is pending a rewrite, so the graph moves on rather than
            # spending its remaining attempts on something no query can fix.
            "verifier_ok": True,
            "verifier_feedback": "",
            "verifier_note": note,
            "llm_calls": calls,
        }

    if bool(verdict.get("ok")):
        logger.info("dynamic[%s]: review APPROVED - %s", capability.id, note[:160])
        return {
            "verifier_ok": True,
            # Cleared HERE, not in the author. That is what lets an instruction
            # survive a rewrite that tripped the validator or the database on
            # its way back: it is only genuinely spent once the rewrite has
            # been judged, and approved.
            "verifier_feedback": "",
            "verifier_note": note,
            "llm_calls": calls,
        }

    feedback = str(verdict.get("feedback") or "").strip()
    if not feedback:
        # A rejection with no instruction is unusable: the author would rewrite
        # blind and is most likely to re-emit the same thing.
        feedback = (
            "The review rejected this query without naming a fix. Re-read the capability's "
            "business concepts and the rule sections supplied, and make the query match them "
            "exactly."
        )
    logger.info("dynamic[%s]: review REJECTED - %s", capability.id, feedback[:200])
    return _reject(state, feedback, calls, note=note)


def _reject(state: CompileState, feedback: str, calls: int, note: str = "") -> Dict[str, Any]:
    """Record the rejection and fund the rewrite.

    ``sql_retry_count`` is reset because this rejection discards a query that
    was mechanically fine: the work has to be redone, so it must be funded.
    Leaving it alone meant a couple of unrelated syntax slips earlier could
    leave a correctly identified defect with no attempts left to fix it -- the
    reviewer would be right, and immediately overruled.

    That is not unbounded: ``verify_retry_count`` still caps how many times this
    path can be taken at all, so total work per capability stays bounded by
    (verify retries + 1) x (sql retries + 1).
    """
    bounded = prompts.truncate(feedback, _MAX_FEEDBACK_CHARS)
    return {
        "verifier_ok": False,
        "verifier_feedback": bounded,
        # Appended, never replaced: the whole point is that the NEXT review can
        # see what this one demanded.
        "verifier_history": list(state.get("verifier_history") or []) + [bounded],
        "verifier_note": note,
        "verify_retry_count": state.get("verify_retry_count", 0) + 1,
        "sql_retry_count": 0,
        "llm_calls": calls,
    }

"""The explanation layer -- and nothing more.

The LLM receives deterministic evidence that has already been calculated and
classified. It summarises and explains; it never calculates, never reclassifies
and never reaches the database.

Failure here is expected and contained: the caller receives ``available=False``
with a reason, and the deterministic dashboard carries on unchanged. No
explanation is ever fabricated.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import logging
<<<<<<< HEAD
import os
=======
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
import threading
import time
from collections import OrderedDict
from datetime import date
<<<<<<< HEAD
from pathlib import Path
=======
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
from typing import Any, Dict, Optional, Tuple

import httpx

from app.config.settings import BACKEND_DIR, get_settings

logger = logging.getLogger(__name__)


def _load_usage_tracker():
    """Load ``llm_usage_tracker.py`` (project root) by file path.

    That module is deliberately independent of this application -- see its
    own docstring -- so it is loaded this way, rather than by adding the
    project root to ``sys.path`` and doing a normal ``import``, to keep that
    independence one-directional and explicit: this app reaches out to it,
    it never reaches back in. A missing or broken tracker file disables
    usage logging (logged once, below) without affecting any explanation.
    """
    tracker_path = BACKEND_DIR.parent / "llm_usage_tracker.py"
    if not tracker_path.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("llm_usage_tracker", tracker_path)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:  # noqa: BLE001 - usage logging must never block startup
        logger.warning("Could not load llm_usage_tracker.py; usage will not be logged", exc_info=True)
        return None


_usage_tracker = _load_usage_tracker()

#: Transient provider failures worth a short retry before giving up. Measured
#: against the live Groq endpoint: 429 (rate limited) and, intermittently,
#: 413 both occurred for a request that succeeded moments later with the
#: identical payload -- i.e. upstream flakiness, not a hard, repeatable limit
#: on this evidence. A momentary hiccup should not surface to the operator as
#: "unavailable" when one retry would have worked.
_RETRYABLE_STATUS_CODES = {408, 413, 429, 500, 502, 503, 504}
_MAX_ATTEMPTS = 3
_RETRY_BACKOFF_SECONDS = 0.75
#: Ceiling on how long a single retry waits, including a provider-supplied
#: Retry-After -- long enough to clear a brief rate-limit window, short enough
#: that the operator is never left waiting well past the configured timeout.
_MAX_RETRY_DELAY_SECONDS = 3.0

SYSTEM_INSTRUCTION = """You are an operational data explanation assistant.

The supplied JSON is authoritative deterministic evidence.

Do not calculate, modify, reinterpret, infer or invent values.

Do not invent causes.

Do not assign responsibility unless explicitly provided by the evidence.

Do not create business rules.

Explain only what the supplied evidence supports.

The "summary" figures (well_count, task_count, status_counts) always cover every well and \
task in scope. The "tasks" list is only a representative sample -- chosen to span every \
status and as many distinct wells as possible -- never the full list. When the scope covers \
more than one well, describe the scope as a whole using the summary figures first, and refer \
to specific wells only as individual examples within that whole. Never describe the scope as \
if it were about only the well(s) shown in the sample.

If information is missing, state that it is unavailable.

Use the supplied quantity_status exactly as provided.

Do not replace SQL/Python classifications with your own classification.

Do not describe quantity differences as errors unless the evidence explicitly \
identifies them as errors.

Report the progress value exactly as given. Its unit is not defined in the \
business rules, so never call it a percentage, never multiply it, and never \
describe how complete the task is from it.

<<<<<<< HEAD
=======
When a task carries "crew_match_status", it compares the crew type actually \
assigned to the task against the crew the WBS/activity mapping expects. \
`MATCHED` and `MISMATCHED` are both plain facts, never an error and never a \
judgement of the crew -- say only that the two agree or do not, and never \
speculate why. `NOT_COMPARABLE` means no crew is recorded on the task, or no \
WBS crew is mapped, so nothing could be compared; say that plainly rather than \
treating it as either a match or a mismatch.

When a task carries "start_date_status" or "end_date_status", each compares \
one reported actual date against its own planned date -- start against start, \
end against end, never against each other. Use the supplied classification \
exactly: `EARLY` and `LATE` are both descriptive facts, never errors and never \
a sign the crew is at fault -- being early is not an anomaly. `NO_ACTUAL` means \
that date has not been reported yet. Never compute a variance yourself, never \
state a number of days, and never call a date "wrong", "incorrect" or "bad" -- \
say only that it is earlier than, later than, or the same as the plan.

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
Write for a construction operations supervisor reading a morning brief. Write \
in flowing prose paragraphs, the way a person would explain it out loud -- NOT \
as a bulleted or numbered list, and not broken into labelled sections. Do not \
use markdown formatting of any kind: no asterisks, no bold, no headings, no \
bullet points, no tables.

One exception, and the only one: wrap every literal value you copy directly \
from the evidence in backticks -- a well ID, task code, activity code, WBS \
name, crew code, unit of measure, quantity, date, or status code -- for \
example `30365`, `54 Joint`, `BELOW_PLAN`, `FLME1150-30365-T07`. This lets the \
reader tell your own words apart from a value taken straight from the record. \
Never wrap your own wording, a rounded or approximate figure, or anything you \
did not copy verbatim from the evidence.

Two to four short paragraphs is normal; a single \
paragraph is fine for a small scope. Use conservative wording: say "the \
reported actual quantity is below the planned quantity", never "the crew \
underperformed". Never convert between units of measure. Keep the response \
under 220 words.

When "summary" carries "no_daily_entry", nothing was recorded for this \
selection on this report date. Say that in one plain sentence and move on to \
whatever else the evidence carries. When the selection is a single well, never \
mention a well count at all -- there is one well, it is the one being \
explained, and saying "0 wells" about it is both confusing and wrong.

Never read the shape of the payload out loud. Do not write "0 wells and 0 \
tasks in scope", do not recite a list of zeroed status counts, and never \
introduce a figure by naming the block it came from -- not "the summary \
indicates", "the task state summary shows", "the evidence lists" or anything \
of that shape. State the fact itself, the way a person reading the record \
would say it out loud.

A task reported with no actual quantity is still a task that was reported. \
`NO_ACTUAL` means the entry carries no actual quantity, NOT that the well \
reported nothing that day -- a well with a reported task must never be \
described as having reported no activity, and the two are separate facts that \
must never be merged into one sentence.

When the evidence includes a "live_well_task_activity" object, it covers \
EVERY live well with a task record as of the report date -- not only the \
wells that reported something on it. Use it to describe the standing position \
behind the day: how many live wells have unfinished tasks, how many of those \
reported nothing on the date, and how much work that adds up to. Its \
open_total is incomplete_total plus ongoing_total, which do not overlap -- \
the same split each well row shows. Keep it \
clearly apart from the day's own reported figures above it: one describes \
what was entered on this date, the other where every live well's tasks stand. \
A day with one reported task and hundreds of wells carrying open work is an \
ordinary thing to describe plainly, not a problem to raise. The wells in \
"wells_with_most_open_work" are examples only, chosen for carrying the most \
unfinished tasks; the counts beside them cover every well, so never describe \
the day as if it were about only the wells you name, and never total, rank or \
compare them yourself beyond the order given. A well appearing there with no \
task reported on the date means exactly that -- no entry that day -- never \
that it is idle, abandoned, delayed or behind.

When the evidence includes a "well_task_activity" object, it describes where \
that one well's tasks stand overall as of the report date -- not what it did \
on the date alone. Every figure in it was already calculated: never add, \
subtract, compare or re-derive one, and never turn one into a percentage or a \
share. Read the "definitions" it carries and say what each figure means in \
plain words rather than naming the field. today_reported_task_count is how \
many tasks the well reported on the selected date, and zero means simply that \
no task was recorded for it that day -- never that work stopped, that the \
well is idle, or that someone failed to report. open_task_count counts \
every task whose latest record does not say completed, and it splits into two \
figures that do NOT overlap: incomplete_task_count (open but not ongoing \
-- no recorded actual start, or an actual end recorded without \
completion) and ongoing_task_count (a recorded actual start and no recorded \
actual end). Incomplete and ongoing add up to open, so never call ongoing a \
subset or a part of incomplete, never describe one task as counting toward \
both, and never add, subtract or re-derive these figures yourself -- each is \
already exactly what it says. last_task_date is the latest date the well appears in \
the task records: say it that way, and never call it a completion date, a \
finish date, or the date work stopped. A task listed as ended without being \
completed is exactly that -- the record carries an end date and does not say \
completed -- so report both halves and do not resolve the two yourself. \
Never describe a task or a well as delayed, late, overdue, stalled, behind \
schedule or at risk: no rule in this system defines any of those, and no \
evidence here establishes one. Never suggest what should be done about the \
well. A well with tasks but none reported on the selected date is an ordinary \
case worth one plain sentence, not a concern to raise.

When the evidence includes a "crew_suggestion" object, it is deterministic \
evidence about this one task only -- calculated entirely by SQL/Python, \
never by you. It is advisory only: never describe it as an assignment, a \
reassignment, or something that has happened. Never say a crew "will be" or \
"has been" put on the task.

<<<<<<< HEAD
=======
ALWAYS give a crew's id the first time you mention that crew, wrapped in \
backticks like every other literal value -- for example "crew `10570`" or \
"crew `10570` (Cellar Crew, supervised by Ali)". This applies to every crew \
in the evidence: the one recorded on the task now (current_crew.crew_id) and \
the one being surfaced (suggested_crew.crew_id or consult_crew.crew_id). The \
id is what an operator uses to actually find the crew, and a crew type name \
alone does not identify one crew -- several crew instances can share a type. \
Never invent an id, and when current_crew.crew_id is null say plainly that no \
crew is recorded on the task rather than naming one.

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
Everywhere below, never write the name of a JSON field or an internal status \
code as if it were a phrase in your own sentence -- not "eligible", \
"ineligible", "crew_suggestion", "suggested_crew", "consult_crew", \
"suppression_reason", "the suppression reason states...", "no_suggestion_reason", \
or anything of that shape. These are internal field names for carrying data \
between programs, not words a person would say out loud; writing one \
describes the evidence structure instead of the task. Always restate what a \
field says in plain prose instead of naming the field itself.

If crew_suggestion.eligible is false and there is no consult_crew field \
either, write your explanation exactly as you would if the word \
"crew_suggestion" never appeared anywhere in the evidence. Do not write any \
sentence about a crew suggestion, an alternative crew, or crew availability \
-- including a sentence that says one is not needed, not provided, or not \
applicable. Silence on the topic is correct; a sentence explaining its \
absence is not. This happens whenever the task is already completed, or \
already shows recorded progress with no crew to point to -- a routine case \
that needs no comment.

If crew_suggestion.eligible is true but suggested_crew is null, do not \
invent a crew. Say plainly, in your own words, that no suitable alternative \
crew was identified from the available records, using no_suggestion_reason \
if it is present.

If suggested_crew is present, weave it naturally into the same explanation \
-- do not present it as a separate feature or a separate paragraph header. \
Explain why that crew is being surfaced using only the supplied fields: how \
many times it has completed this same activity (historical_completed_task_count), \
on how many distinct wells (distinct_completed_well_count), its typical \
(median) completion time (typical_completion_days) and, where useful, its \
average (average_completion_days) -- these are two different figures and \
must never be conflated or called by the other's name. If \
completed_on_incomplete_well_count is greater than zero, mention that some of \
that history comes from wells that were not themselves complete, so the \
evidence reflects finishing this activity, not finishing the whole well. \
derived_availability of NO_CURRENT_UNFINISHED_TASK means only that no current \
unfinished task was found for that crew in the available records -- never \
call this "available" or "free" without that qualification, and never claim \
physical or workforce availability. evidence_strength describes how much \
history backs the suggestion (say so only if it adds something, never as a \
raw label). Never rank or compare crews yourself -- exactly one crew is ever \
supplied, and it is already the top-ranked one. suggested_crew is only ever \
supplied for a task that is stalled or has not yet started moving -- never \
say this task "is progressing normally", "is going smoothly", or "does not \
need a change": that framing belongs only to consult_crew below, on a \
different kind of task, and would contradict the evidence here. Conclude \
this crew's mention as a possible alternative for the task itself -- e.g. \
"may be worth considering as an alternative crew for this task" -- never as \
something to consult for feedback or information, which is consult_crew's \
framing, not this one.

If consult_crew is present instead of suggested_crew, the task is already \
progressing normally -- no change is needed. Refer to the crew the ordinary \
<<<<<<< HEAD
way you would refer to any other crew in this evidence: by its crew type \
and/or supervisor name when given, or by its crew id only if nothing else is \
available -- exactly as you would for suggested_crew or current_crew, never \
by naming the field it came from.
=======
way you would refer to any other crew in this evidence: by its crew id, plus \
its crew type and/or supervisor name when those are given -- exactly as you \
would for suggested_crew or current_crew, never by naming the field it came \
from.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

Say, in plain natural words, that the task is going smoothly and another \
crew is not necessary right now, then add -- as a small, friendly aside, not \
a warning -- that if any feedback or information is ever needed about this \
activity, that crew has handled this same work before (using its own \
historical_completed_task_count / distinct_completed_well_count / \
typical_completion_days the same way you would for suggested_crew) and could \
be worth asking. Never call this a suggestion, a recommendation, or an \
alternative crew -- it is only a "here is who has done this before, in case \
you want their input" mention on an otherwise fine task. The same rules as \
suggested_crew apply to what you may claim: never call derived_availability \
"available" without qualification, never invent a reason beyond the supplied \
fields, and never rank or compare crews. A good shape for this, adapted in \
your own words and grounded in the actual crew type/supervisor given, is: \
"the task is going smoothly, so another crew is not necessary, but if any \
feedback or information is needed on this activity, the crew that previously \
completed this work could be worth asking."

current_crew tells you what is already recorded on the task. If \
current_crew.recorded is false, say the crew is not recorded or associated \
with this task record -- never say "no crew was assigned", which claims more \
than the evidence supports. Never criticise, blame, or judge the performance \
of the current crew; the current task simply taking longer than the \
historical pattern is not evidence of fault.

Respect the report date. For a past report date, describe the crew evidence \
in the past tense, as what was already known BY that date -- e.g. "Crew X had \
already completed..." or "could have been considered as an alternative at \
that time" -- never as if it were being decided today. Never use evidence \
timestamped after the selected report date; the evidence you were given is \
already limited to what existed by then, so simply describe it as of that \
date. Never claim a crew "would definitely have finished faster" or "would \
have solved the delay" -- say only that it could have been considered, based \
on the evidence available then."""

#: Endpoint used when LLM_BASE_URL is not set. Both providers speak the
#: OpenAI-compatible chat completions protocol.
_PROVIDER_BASE_URLS = {
    "groq": "https://api.groq.com/openai/v1",
    "openai": "https://api.openai.com/v1",
}


def _retry_delay(response: httpx.Response, attempt: int) -> float:
    """Seconds to wait before the next attempt.

    Honours a provider-supplied ``Retry-After`` (seconds form) when present,
    capped so one flaky response can never stall the request past a few
    seconds; otherwise backs off a little longer on each attempt.
    """
    retry_after = response.headers.get("Retry-After")
    if retry_after is not None:
        try:
            return min(float(retry_after), _MAX_RETRY_DELAY_SECONDS)
        except ValueError:
            pass
    return min(_RETRY_BACKOFF_SECONDS * attempt, _MAX_RETRY_DELAY_SECONDS)


class LLMUnavailable(RuntimeError):
    """The explanation could not be produced. The dashboard continues regardless."""


#: Bump this whenever SYSTEM_INSTRUCTION, the evidence-sampling strategy
#: (``EvidenceService._representative_sample``), or anything else that could
#: change what a given evidence payload *should* produce is edited. It is
#: mixed into the cache key below, so every explanation cached under an
#: older version becomes permanently unreachable the moment this changes --
#: a prompt improvement takes effect on the very next request instead of
#: being masked by an answer generated under the old prompt, with no need to
#: find and clear the persisted cache file by hand.
#:
#: v3: added the crew-suggestion system-instruction rules. A task-scope
#: evidence payload now sometimes carries a "crew_suggestion" key (see
#: app/services/crew_suggestion_service.py) that a v2-cached explanation was
#: never told how to read.
#: v4: tightened the ineligible-suggestion rule -- a v3 explanation could
#: still name "crew suggestion" as suppressed for a progressing task instead
#: of omitting it entirely.
#: v5: tightened further -- a v4 explanation still added a sentence saying no
#: alternative crew was suggested; the rule now says silence, not an
#: explained absence.
#: v6: added the consult_crew rule -- an in-progress task with a historically
#: proven crew now gets a brief, non-replacement "worth asking for feedback"
#: mention instead of pure silence, so the feature stays visible even when a
#: switch is not warranted. A v5-cached explanation for such a task never
#: learned this framing.
#: v7: tightened the consult_crew rule -- a v6 explanation named internal
#: field/status words ("ineligible", "consult crew", "crew suggestion was
#: generated") instead of describing the task in plain, natural language.
#: v8: tightened again -- a v7 explanation still said "identified in the
#: consult crew section, `10028`" instead of referring to the crew by its
#: type/supervisor the same way it already does for suggested_crew.
#: v9: fixed a real regression -- a v8 explanation for a genuinely stalled
#: task (suggested_crew present) borrowed consult_crew's "progressing
#: normally, no change needed" framing, directly contradicting its own
#: evidence (NO_ACTUAL, no crew recorded). The two framings are now
#: explicitly told apart.
#: v10: the field-name-leak rule ("eligible", "suppression_reason", etc.) is
#: now stated once, up front, covering every branch -- a v9 explanation for
#: an in-progress task still wrote "as indicated by the suppression reason
#: stating that the task is currently being worked on".
#: v11: a v10 explanation for a genuinely stalled task (suggested_crew) used
#: consult_crew's "worth consulting for feedback" framing instead of
#: presenting the crew as a possible alternative for the task itself --
#: suggested_crew now has its own explicit closing template.
#: v12: a well-scoped evidence payload now carries a "well_task_activity"
#: block (incomplete / ongoing task counts, whether anything was reported on
#: the report date, the last task date) that a v11-cached explanation was
#: never told how to read -- including the rules that a zero reported-task
#: count is not an idle well and that last_task_date is not a completion date.
#: v13: two things a v12 answer got wrong, both seen live. A day-scope payload
#: now carries "live_well_task_activity" -- every live well's open work, not
#: just what was reported that day -- which v12 was never given, so its
#: whole-view summary could only describe the one well that happened to
#: report. And a scope with no entry for the date now carries a summary of
#: its own instead of one full of zeroes, because v12 read those zeroes out
#: loud: explaining one well, it opened with "there are 0 wells and 0 tasks in
#: scope".
#: v14: two slips a v13 answer made on the very first real day it saw. It
#: described a well that HAD reported a task as having "did not report any
#: activity", having read that task's NO_ACTUAL status as an absent entry;
#: and it still introduced figures by naming the block they came from ("the
#: task state summary indicates...") rather than simply stating them.
#: v15: "incomplete" changed meaning. It used to count every not-completed
#: task, with ongoing as a subset of it, so the two figures on a well's row
#: overlapped and could not be added. They are now disjoint -- open =
#: incomplete + ongoing -- and a v14 answer would describe the new numbers
#: under the old relationship, calling ongoing a subset of incomplete.
_PROMPT_VERSION = 15


<<<<<<< HEAD
def _evidence_hash(evidence: Dict[str, Any]) -> str:
    """A content-address for one evidence payload, salted by `_PROMPT_VERSION`.
=======
def _evidence_hash(evidence: Dict[str, Any], model: str = "") -> str:
    """A content-address for one evidence payload, salted by `_PROMPT_VERSION`
    and by the model asked to explain it.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

    Two requests that resolve to byte-identical evidence, under the same
    prompt version, describe the same day, the same scope and filters, and
    the same figures the dataset resolved for them -- there is nothing left
    for a second LLM call to add. Hashing the evidence itself (rather than
    the request's scope/filter parameters) is what makes that guarantee
    exact rather than assumed: if the underlying data changes for any
    reason, the evidence changes and the hash changes with it, so a stale
    explanation can never be served for changed data. ``sort_keys`` makes
    the JSON dump stable regardless of the dict's insertion order.
<<<<<<< HEAD
    """
    canonical = json.dumps(evidence, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(f"{_PROMPT_VERSION}:{canonical}".encode("utf-8")).hexdigest()


class _ExplainCache:
    """Cache of successful explanations, keyed by report date, persisted to disk.
=======

    The model is part of the key because the same evidence can now be sent to
    more than one model (the front page's day summary has its own, see
    ``Settings.llm_day_model``): switching that model must produce its own
    answer, never replay one another model wrote.
    """
    canonical = json.dumps(evidence, sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(
        f"{_PROMPT_VERSION}:{model}:{canonical}".encode("utf-8")
    ).hexdigest()


def _is_reasoning_model(model: str) -> bool:
    """True for OpenAI's reasoning models (gpt-5 family, o-series).

    They reject ``max_tokens`` in favour of ``max_completion_tokens``, and
    accept only their default temperature, so the request is shaped
    differently for them.
    """
    name = (model or "").lower().rsplit("/", 1)[-1]
    return name.startswith(("gpt-5", "o1", "o3", "o4"))


class _ExplainCache:
    """Cache of successful explanations, keyed by report date, in memory only.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

    Not a fixed-size cache of hashes alone: entries are grouped by report date
    so a "Refresh" on one date can drop only that date's cached explanations
    (`invalidate_date`) without touching any other date the operator has open.
    Within a date, the key is the evidence's content hash (`_evidence_hash`),
    so identical evidence always resolves to the same cached text and changed
    evidence never does.

    Only successful explanations are ever stored -- see `LLMService.explain_safe`.
    A transient failure must never be "remembered" as permanent; the next
    identical request should retry the provider, not repeat a stale error.

<<<<<<< HEAD
    **Why this writes to disk.** A purely in-memory cache is wiped by every
    process restart -- a dev auto-reload, a redeploy, a plain re-run -- so the
    very first request after any restart always paid for a fresh LLM call
    again, even for a well explained a minute before the restart. Every
    change is written straight through to `file_path` (default
    `backend/.cache/explain_cache.json`, see `Settings.explain_cache_file`),
    and the constructor loads whatever is already there, so a fresh process
    starts warm: only a genuinely new question -- one this file has never
    seen, on this date, in any previous run -- ever costs a new LLM call.
    Content-hashing is still what makes this *correct*, not just cheap: a
    real data change still produces a different hash and is never served a
    stale answer, no matter how many restarts sit between the two requests.
    """

    def __init__(self, file_path: Optional[Path] = None) -> None:
        self._file_path = file_path
=======
    **Why this is never written to disk.** An explanation belongs to the run
    that produced it. Within one run, asking again for the same well costs no
    tokens; a "Refresh" drops that date's answers; stopping the application
    drops all of them, so the next run always writes its summaries afresh
    rather than replaying text from an earlier one.
    """

    def __init__(self) -> None:
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        self._by_date: "OrderedDict[str, OrderedDict[str, Dict[str, Any]]]" = OrderedDict()
        self._lock = threading.Lock()
        # One lock per (date, evidence hash) key, used to serialise concurrent
        # requests for the exact same not-yet-cached evidence -- see
        # `key_lock`. Deliberately never pruned: a `threading.Lock` is a few
        # dozen bytes, and the number of distinct (date, evidence) pairs this
        # process will ever be asked to explain is small next to that.
        self._key_locks: Dict[Tuple[str, str], threading.Lock] = {}
        self._key_locks_guard = threading.Lock()
<<<<<<< HEAD
        self._load()

    def _load(self) -> None:
        """Warm the cache from disk. Never raises -- a missing or corrupt
        file just means starting cold, exactly like before this existed."""
        if self._file_path is None:
            return
        try:
            raw = self._file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return
        except OSError:
            logger.warning("Could not read explanation cache file %s", self._file_path)
            return
        try:
            data = json.loads(raw)
        except ValueError:
            logger.warning(
                "Explanation cache file %s is not valid JSON; starting empty",
                self._file_path,
            )
            return
        if not isinstance(data, dict):
            return
        loaded: "OrderedDict[str, OrderedDict[str, Dict[str, Any]]]" = OrderedDict()
        for report_date, bucket in data.items():
            if isinstance(bucket, dict):
                loaded[report_date] = OrderedDict(bucket)
        self._by_date = loaded
        logger.info(
            "Explanation cache warmed from %s: %d date(s), %d entr%s",
            self._file_path,
            len(loaded),
            sum(len(b) for b in loaded.values()),
            "y" if sum(len(b) for b in loaded.values()) == 1 else "ies",
        )

    def _persist(self) -> None:
        """Write the whole cache back out. Called with `self._lock` already
        held, so the file on disk never reflects a half-updated in-memory
        state. Failure to write is logged, never raised -- a full disk or a
        permissions problem must degrade to an in-memory-only cache for the
        rest of this process, not break explanations altogether."""
        if self._file_path is None:
            return
        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = self._file_path.with_suffix(self._file_path.suffix + ".tmp")
            tmp_path.write_text(
                json.dumps(self._by_date, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # Atomic on both POSIX and Windows (Python 3.3+): a reader never
            # observes a half-written file, even if this process is killed
            # mid-write.
            os.replace(tmp_path, self._file_path)
        except OSError:
            logger.warning(
                "Could not persist explanation cache to %s; continuing in-memory only",
                self._file_path,
            )
=======
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

    def key_lock(self, report_date: str, evidence_hash: str) -> threading.Lock:
        """The lock guarding this one (date, evidence) key.

        Held only around the "still a miss? then call the LLM and store it"
        step in `LLMService.explain_safe` -- never around a cache read, and
        never across two different keys -- so requests for a different well,
        scope or date proceed fully in parallel; only two requests racing on
        the exact same not-yet-cached evidence ever wait on each other.
        """
        with self._key_locks_guard:
            lock = self._key_locks.get((report_date, evidence_hash))
            if lock is None:
                lock = threading.Lock()
                self._key_locks[(report_date, evidence_hash)] = lock
            return lock

    def get(self, report_date: str, evidence_hash: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            bucket = self._by_date.get(report_date)
            if bucket is None:
                return None
            entry = bucket.get(evidence_hash)
            if entry is None:
                return None
            # Touch both levels so an active date and its active entries are
            # the least likely to be evicted next.
            bucket.move_to_end(evidence_hash)
            self._by_date.move_to_end(report_date)
            return entry

    def put(
        self, report_date: str, evidence_hash: str, entry: Dict[str, Any], *, max_entries: int
    ) -> None:
        with self._lock:
            bucket = self._by_date.setdefault(report_date, OrderedDict())
            bucket[evidence_hash] = entry
            bucket.move_to_end(evidence_hash)
            self._by_date.move_to_end(report_date)

            # Two-level LRU eviction: trim the oldest entry within whichever
            # date holds the most, dropping an emptied date entirely, until
            # the total across every date is back within budget. This keeps
            # one very busy date from starving every other date's cache.
            total = sum(len(b) for b in self._by_date.values())
            while total > max_entries and self._by_date:
                oldest_date = next(iter(self._by_date))
                oldest_bucket = self._by_date[oldest_date]
                if oldest_bucket:
                    oldest_bucket.popitem(last=False)
                if not oldest_bucket:
                    del self._by_date[oldest_date]
                total -= 1

<<<<<<< HEAD
            self._persist()

    def invalidate_date(self, report_date: str) -> None:
        with self._lock:
            self._by_date.pop(report_date, None)
            self._persist()
=======
    def invalidate_date(self, report_date: str) -> None:
        with self._lock:
            self._by_date.pop(report_date, None)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)


class LLMService:
    """Thin OpenAI-compatible chat-completions client."""

    def __init__(self) -> None:
<<<<<<< HEAD
        cache_file = get_settings().explain_cache_file
        path = (BACKEND_DIR / cache_file) if cache_file else None
        self._cache = _ExplainCache(path)
=======
        self._cache = _ExplainCache()
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

    def invalidate_date(self, report_date: date) -> None:
        """Drop every cached explanation for one report date.

        Called wherever the day's dataset itself is force-refreshed (the
        "Refresh" control bypasses the dataset's own TTL cache), so a refresh
        always gets a fresh explanation on the next request even in the rare
        case where the reloaded data happens to be byte-identical to before.
        """
        self._cache.invalidate_date(report_date.isoformat())
<<<<<<< HEAD
=======
        # The agents keep their own per-run answers; a Refresh drops those too.
        from app.agentic import service as agent_service

        agent_service.invalidate_date(report_date)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

    def status(self) -> Dict[str, Any]:
        settings = get_settings()
        return {
            "configured": settings.llm_configured,
            "provider": settings.llm_provider,
            "model": settings.llm_model if settings.llm_configured else None,
        }

    def _endpoint(self) -> str:
        settings = get_settings()
        base = settings.llm_base_url or _PROVIDER_BASE_URLS.get(
            (settings.llm_provider or "").lower()
        )
        if not base:
            raise LLMUnavailable(
                "No LLM endpoint configured. Set LLM_BASE_URL in backend/.env."
            )
        return base.rstrip("/") + "/chat/completions"

    def _post_with_retry(self, payload: Dict[str, Any], settings: Any) -> httpx.Response:
        """POST the chat-completion request, retrying a transient failure once or twice.

        Raises:
            LLMUnavailable: the request could not be sent, or every attempt
                (including retries) came back with an error.
        """
        last_response: Optional[httpx.Response] = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = httpx.post(
                    self._endpoint(),
                    headers={
                        "Authorization": f"Bearer {settings.llm_api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=settings.llm_timeout_seconds,
                )
            except httpx.HTTPError as exc:
                logger.warning("LLM request failed: %s", type(exc).__name__)
                raise LLMUnavailable(
                    "The AI explanation service could not be reached."
                ) from None

            if response.status_code < 400:
                return response

            last_response = response
            retryable = response.status_code in _RETRYABLE_STATUS_CODES
            if not retryable or attempt == _MAX_ATTEMPTS:
                break

            delay = _retry_delay(response, attempt)
            logger.info(
                "LLM returned HTTP %s on attempt %d/%d; retrying in %.2fs",
                response.status_code,
                attempt,
                _MAX_ATTEMPTS,
                delay,
            )
            time.sleep(delay)

        # Log the status only: the body can echo request content.
        logger.warning("LLM returned HTTP %s", last_response.status_code)
        if last_response.status_code == 413:
            # Evidence size is capped per scope in evidence_service, so this
            # should now be rare -- but a plain-language reason still beats
            # surfacing a raw HTTP status to an operator.
            raise LLMUnavailable(
                "This selection has too much data for the AI to summarise at once."
            )
        raise LLMUnavailable(
            f"The AI explanation service returned HTTP {last_response.status_code}."
        )

<<<<<<< HEAD
    def explain(self, evidence: Dict[str, Any]) -> Tuple[str, str]:
        """Return ``(explanation, model)`` for the supplied evidence.

=======
    def explain(
        self,
        evidence: Dict[str, Any],
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Tuple[str, str]:
        """Return ``(explanation, model)`` for the supplied evidence.

        ``model`` and ``max_tokens`` default to ``LLM_MODEL`` and
        ``LLM_MAX_TOKENS``; the front page's day summary passes its own.

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        Raises:
            LLMUnavailable: for any configuration, transport or response
                problem. The caller reports it; it never fabricates text.
        """
        settings = get_settings()
        if not settings.llm_configured:
            raise LLMUnavailable(
                "The AI explanation service is not configured. "
                "Set LLM_API_KEY and LLM_MODEL in backend/.env."
            )

<<<<<<< HEAD
        payload = {
            "model": settings.llm_model,
            "temperature": settings.llm_temperature,
            "max_tokens": settings.llm_max_tokens,
=======
        model = model or settings.llm_model
        max_tokens = max_tokens or settings.llm_max_tokens
        payload: Dict[str, Any] = {
            "model": model,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
            "messages": [
                {"role": "system", "content": SYSTEM_INSTRUCTION},
                {
                    "role": "user",
                    "content": (
                        "Explain the following deterministic evidence for the "
                        "daily morning brief. Use only these values.\n\n"
                        + json.dumps(evidence, ensure_ascii=False, indent=2)
                    ),
                },
            ],
        }
<<<<<<< HEAD
=======
        if _is_reasoning_model(model):
            payload["max_completion_tokens"] = max_tokens
        else:
            payload["temperature"] = settings.llm_temperature
            payload["max_tokens"] = max_tokens
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

        started = time.perf_counter()
        response = self._post_with_retry(payload, settings)
        elapsed_seconds = time.perf_counter() - started

        try:
            body = response.json()
            message = body["choices"][0]["message"]
            text = (message.get("content") or "").strip()
            if not text:
                # Some reasoning models place the answer in a separate field.
                text = (message.get("reasoning_content") or "").strip()
        except (ValueError, KeyError, IndexError, TypeError):
            logger.warning("LLM returned an unreadable response body")
            raise LLMUnavailable(
                "The AI explanation service returned an unreadable response."
            ) from None

        if not text:
            raise LLMUnavailable("The AI explanation service returned no text.")

<<<<<<< HEAD
        logger.info("LLM explanation generated with model %s", settings.llm_model)
=======
        logger.info("LLM explanation generated with model %s", model)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

        # This point is reached only for a real, successful call to the
        # provider -- never for a cache hit (explain_safe short-circuits
        # before ever calling this method) -- so every row in the usage log
        # represents tokens actually spent, never a reused answer.
        usage = body.get("usage") if isinstance(body, dict) else None
        if _usage_tracker is not None:
            try:
                # prompt_tokens_details.cached_tokens is OpenAI's breakdown of
                # how much of prompt_tokens was served from its own prompt
                # cache (typically billed at a discount). Not every provider
                # reports it -- absent entirely on a provider that doesn't,
                # so this stays None rather than a misleading 0.
                cache_details = (usage or {}).get("prompt_tokens_details") or {}
<<<<<<< HEAD
=======
                # Labelled as the FAST system explicitly, rather than left to a
                # default. This project also spends tokens on a reasoning model
                # that compiles SQL, and "what is the compiler costing me as
                # opposed to the report?" is only answerable if each call says
                # which of the two it was at the point it was made.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
                _usage_tracker.log_usage(
                    input_tokens=(usage or {}).get("prompt_tokens"),
                    output_tokens=(usage or {}).get("completion_tokens"),
                    input_cache_tokens=cache_details.get("cached_tokens"),
                    duration_seconds=elapsed_seconds,
<<<<<<< HEAD
=======
                    system="fast",
                    node="fast_explanation",
                    provider=settings.llm_provider,
                    model=str(model),
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
                )
            except Exception:  # noqa: BLE001 - logging usage must never break an explanation
                logger.warning("Could not log LLM usage", exc_info=True)

<<<<<<< HEAD
        return text, str(settings.llm_model)

    def explain_safe(self, evidence: Dict[str, Any]) -> Dict[str, Any]:
=======
        return text, str(model)

    def explain_safe(
        self,
        evidence: Dict[str, Any],
        *,
        model: Optional[str] = None,
        max_tokens: Optional[int] = None,
    ) -> Dict[str, Any]:
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        """Never raises. Returns the explanation or a plain unavailable reason.

        Identical evidence is served from cache instead of calling the LLM
        again -- pressing "AI summary" a second time for the same well, group
        or day, with nothing changed underneath it, costs no additional
        tokens. See ``_evidence_hash`` and ``_ExplainCache`` for exactly what
        "identical" means and how a real data change or a "Refresh" still
        forces a fresh call.

        Two requests for the same not-yet-cached evidence arriving together --
        two browser tabs on the same well, or a client that fires a request
        twice in quick succession -- must not both reach the LLM: whichever
        arrives second waits on ``key_lock`` and then finds the first one's
        answer already cached, rather than paying for a second, independently
        generated (and, at a non-zero temperature, differently worded) answer
        to the exact same question.
        """
        report_date = str(evidence.get("report_date") or "")
<<<<<<< HEAD
        evidence_hash = _evidence_hash(evidence)
=======
        evidence_hash = _evidence_hash(evidence, model or get_settings().llm_model or "")
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

        cached = self._cache.get(report_date, evidence_hash)
        if cached is not None:
            return {**cached, "cached": True}

        with self._cache.key_lock(report_date, evidence_hash):
            # Re-check: whoever held this lock before us may have just
            # finished computing and storing the very answer we were about
            # to ask the LLM for.
            cached = self._cache.get(report_date, evidence_hash)
            if cached is not None:
                return {**cached, "cached": True}

            try:
<<<<<<< HEAD
                text, model = self.explain(evidence)
                result = {"available": True, "explanation": text, "model": model, "error": None}
=======
                # Only the overrides actually given are passed on, so a call
                # that names none reaches `explain` exactly as it always has.
                overrides = {
                    key: value
                    for key, value in (("model", model), ("max_tokens", max_tokens))
                    if value is not None
                }
                text, used_model = self.explain(evidence, **overrides)
                result = {
                    "available": True,
                    "explanation": text,
                    "model": used_model,
                    "error": None,
                }
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
            except LLMUnavailable as exc:
                # Never cached: a transient failure must be retried next
                # time, not remembered as a permanent answer.
                return {
                    "available": False,
                    "explanation": None,
                    "model": None,
                    "error": str(exc),
                    "cached": False,
                }
            except Exception:  # noqa: BLE001 - the dashboard must survive anything here
                logger.exception("Unexpected LLM failure")
                return {
                    "available": False,
                    "explanation": None,
                    "model": None,
                    "error": "The AI explanation service failed unexpectedly.",
                    "cached": False,
                }

            self._cache.put(
                report_date,
                evidence_hash,
                result,
                max_entries=get_settings().explain_cache_max_entries,
            )
            return {**result, "cached": False}

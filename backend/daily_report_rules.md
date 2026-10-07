# DAILY REPORT RULES — AL TASNIM DAILY MORNING BRIEF

Authoritative rules for classifying, grouping and presenting one day's `well.task_daily`
entries. Follow it exactly. Do not infer, modify, or invent a definition. If a question
needs a rule that is not stated below, say the rule is **not yet defined** — do not assume
one.

**This file is the daily-report-specific sibling of `business_rules.md`.** General
organisational rules that apply beyond a single day's report — PDO/Al Tasnim scope and
responsibility, well lifecycle dates, milestone deadlines, schedule variance, PMS weightage
— live in `business_rules.md` instead. Nothing here overrides or repeats those; a live well,
for instance, is still defined by `business_rules.md`'s well-completion rule (its §7), not
redefined here. This file covers only what is specific to classifying and displaying one
day's task entries: actual vs. planned, UOM, progress, mapping, and data quality — plus, in
§9, how a live well's own tasks are counted and described alongside that day on the front
page.

## 1. Column dictionary

Use these exact names; never a synonym.

| Concept | Definition |
|---|---|
| Daily task source | `well.task_daily`, filtered to one `ActionOn` date |
| Daily actual quantity | `TRY_CONVERT(DECIMAL(18,4), JSON_VALUE(daily_data, '$.actual_quantity'))`, guarded by `ISJSON` |
| Planned quantity | `task_daily.planned` |
| Progress | `task_daily.progress`, reported exactly as stored |
| Activity id | `LEFT(task_code, NULLIF(CHARINDEX('-', task_code), 0) - 1)` |
| Activity code | `dbo.mapping_master.New_Activity_Code`, joined on `CAST(mapping_master.Activity_ID AS nvarchar(50)) = activity_id` |
| WBS | `activity_master_csv.activity_group_description` — only this |
| Crew | `activity_master_csv.crew_code` — only this |
| UOM | `ref.uom.uom_code`, joined on `task_daily.uom_id` |

`status_id`, `progress` and `flowline_const_status_id` are **not** used to decide whether a
well is live — that is `business_rules.md`'s rule (§7), not this file's. Task rows with
`well_id <= 1`, or with a non-numeric `well_id`, are excluded. `task_daily.data_qty` is
**never** read as the actual quantity, and `task_daily.required` is **not read at all** (§7,
below).

`daily_completed` and `ph_name` are read from the `daily_data` JSON (`$.completed`,
`$.ph_name`), falling back to the `daily_completed` / `daily_ph_name` columns only because
those columns are `NULL` throughout the table while the JSON carries the real values — the
same JSON the actual quantity comes from.

**Schema drift — `task_daily.well_id` is `varchar`, not `int`.** `well.well_master.well_id`
is still `int`; `task_daily.well_id` was migrated to `varchar` and a minority of rows hold
non-numeric junk (`'0000F'`, `'0000I'`, `'0000J'`, blank). A bare comparison or JOIN against
the int `well_master.well_id` throws `SQLSTATE 22018` and takes the whole report down with
it. Every use of `task_daily.well_id` in this codebase goes through `TRY_CONVERT(int,
td.well_id)`, which returns `NULL` for junk instead of raising; the `INNER JOIN` to live
wells then excludes it the same way it already excludes `well_id <= 1`. Enforced by a test
(`test_well_id_is_never_compared_or_joined_without_try_convert`) so this cannot regress.

## 2. Task code → activity → WBS → crew

`well.task_daily.task_code` encodes the activity. Resolve it through TWO lookups, in this
order. Never guess a WBS, and never use the activity id as one.

```
task_code                       e.g. 'FLME1180-30356'
└ text before the FIRST '-'  = activity_id           'FLME1180'
    → dbo.mapping_master.Activity_ID   (CAST to nvarchar — stored as TEXT)
       → .New_Activity_Code                           'FL-ME-ML08-03'
          → dbo.activity_master_csv.activity_code
             → .activity_group_description = WBS      'Straightline Welding incl. supports'
             → .crew_code                  = crew     'MWS0602'
```

- **`dbo.mapping_master` is the current source, not `dbo.activity_master_mapping`.** The old
  table is superseded and resolves only a minority of activity_ids seen in `task_daily`
  (measured ~30% overall, and 0% for some wells entirely). `dbo.mapping_master.New_Activity_Code`
  is the column that plays `activity_master_mapping.activity_code`'s former role, feeding
  `activity_master_csv` below. `mapping_master.Activity_ID` is stored as `TEXT`; `CAST` it to
  `nvarchar` before comparing.
- `mapping_master` has no equivalent of the old table's `project_type`, `composition_code` or
  `class_b_ptw` columns. Report those as not recorded — do not backfill them from the old,
  superseded table.
- The text before the FIRST '-' is ALWAYS `activity_id`; a task_code may hold more dashes
  (`FLME1180-34516-T02` is still `FLME1180`). Extract it NULL-safely:
  `LEFT(task_code, NULLIF(CHARINDEX('-', task_code), 0) - 1)`
- `task_code` is an INTERNAL key. Use it ONLY to derive `activity_id`; NEVER display it when
  the question asks about a well's ACTIVITIES — identify an activity by `activity_code`
  (from `mapping_master.New_Activity_Code`), and list them DISTINCT.
- WBS is ONLY `activity_master_csv.activity_group_description`. Crew is ONLY `.crew_code`.
- LEFT JOIN both hops whenever unmapped work must stay visible (the WBS shapes below) — an
  inner JOIN drops it silently and makes the unmapped tally read 0. The ACTIVITY LIST is the
  exception: it uses an inner JOIN, because an activity with no mapping row is not a listable
  activity.
- A repeating `activity_id` is CORRECT — one well runs the same activity many times. Never
  de-duplicate it away; choose the GRAIN the question asks for instead:

```sql
-- per ACTIVITY  ("what activities does this well have")  -> one row per activity, NO task_code
--   INNER JOIN here on purpose: an activity with no mapping row is NOT listed.
SELECT DISTINCT a.activity_id, mm.New_Activity_Code AS activity_code
FROM a JOIN dbo.mapping_master mm ON CAST(mm.Activity_ID AS nvarchar(50)) = a.activity_id

-- per TASK  (ONLY when the question asks for tasks)  -> one row per task_code
SELECT a.task_code, a.activity_id, mm.New_Activity_Code AS activity_code,
       amc.activity_group_description AS wbs, amc.crew_code

-- WBS BREAKDOWN  ("which WBS", "tasks per WBS")  -> one row per WBS
--   ISNULL is correct here: unmapped tasks are a real row of the breakdown.
SELECT ISNULL(amc.activity_group_description, '(unmapped)') AS wbs,
       COUNT(DISTINCT a.task_code) AS tasks
GROUP BY amc.activity_group_description

-- WBS COUNT  ("how many WBS")  -> the number, with the unmapped tally beside it
SELECT COUNT(DISTINCT amc.activity_group_description) AS wbs_count,
       COUNT(DISTINCT CASE WHEN amc.activity_group_description IS NULL
                           THEN a.task_code END)      AS unmapped_tasks
```

⚠ NEVER `COUNT(DISTINCT ISNULL(activity_group_description, '(unmapped)'))` — "(unmapped)" is
not a WBS but a task whose WBS is unknown, so it adds a phantom +1. One real well has 21 WBS
and 2 unmapped tasks; that expression reports 22. Give both figures: "21 WBS; 2 tasks not
mapped to one." Never count the per-TASK rows for a per-WBS question either — that same well
returns 90 task rows.

**Both lookup tables are de-duplicated to one row per key** inside the query. Without that,
the handful of `mapping_master` rows sharing an `activity_id` would fan out the daily rows
and inflate every total on the dashboard.

**`activity_uom` — connected as reference evidence, never as a substitute.**
`dbo.mapping_master` also carries a `UOM` column: the unit the activity master expects, as
opposed to `uom_code` (§5 below — the unit actually recorded on this daily row). Measured
against actual-entry rows table-wide, `uom_code` is missing on 26 of 11,277; all 26 have a
`mapping_master.UOM` value available. That value is surfaced as `activity_uom` — in the API,
the well/task detail views, and the Excel export — precisely because it fills a real
evidence gap. It is **never** used to fill in, group by, or convert a quantity: `uom_code`
stays the sole UOM for those purposes (§5), and `MISSING_UOM` (§6) still fires exactly as
before when `uom_code` is absent, regardless of whether `activity_uom` is available. The two
are also never compared for a "mismatch" flag: `mapping_master.UOM` values like `'No'` vs.
`ref.uom.uom_code` values like `'Nos'` look like naming-convention variants of the same
abbreviation rather than a real discrepancy, and no rule exists to tell the two apart — so
both values are shown side by side and left for a person to judge, rather than the system
guessing.

## 3. Daily row-grain resolution

`well.task_daily` holds several rows that share `(well_id, schedule_id, task_code,
ActionOn)`. They are different planning snapshots plus, usually, one actual-entry row.
Getting this wrong is the fastest way to a wrong number, so the strategy is explicit,
isolated in `sql/daily_tasks.sql`, and covered by tests.

**What the data actually looks like** (measured across the whole table, not assumed):

* 2,729 groups have more than one row;
* **zero** groups have more than one actual-entry row;
* multi-row groups differ in their `planned` value — they are planning snapshots.

**The strategy.** Partition by `(well_id, schedule_id, task_code, ActionOn)` and rank:

1. `is_actual_entry DESC` — an actual-entry row outranks a planning snapshot;
2. `updated_at DESC` — then the most recently updated row;
3. `id DESC` — final tie-break.

Keep rank 1. Carry `group_row_count` and `group_actual_entry_count` forward so the condition
stays visible rather than being silently collapsed:

* more than one row → `MULTIPLE_TASK_ROWS`;
* more than one actual entry → `DUPLICATE_ACTUAL_ENTRY`.

`DISTINCT` would merge genuinely different snapshots. `MAX(id)` alone could pick a planning
snapshot over the actual entry and lose a reported quantity. Summing every row would double
count. Ranking avoids all three.

State the arithmetic openly wherever a reduced count is shown — e.g. *"3 duplicate task rows
set aside by grain resolution (197 raw rows → 194 logical tasks)"* — so the reduction is
auditable, never a silent shrink.

`DUPLICATE_ACTUAL_ENTRY` currently never fires on this data. The handling exists anyway,
because the grain is a property of the data, not a guarantee.

## 4. Quantity status

The single classification of a daily entry's reported quantity against its planned
quantity. Classify once; never recompute or override it downstream (React and the LLM both
repeat it verbatim).

| Condition | Status |
|---|---|
| `actual = planned` | `ON_PLAN` |
| `actual > planned` | `ABOVE_PLAN` |
| `actual < planned` | `BELOW_PLAN` |
| `actual IS NULL` | `NO_ACTUAL` |
| `actual` present, `planned IS NULL` | `NOT_VALIDATED` |

**`ABOVE_PLAN` is not an error.** It states that the reported actual is above the planned
quantity, nothing more. `planned = 0` with `actual > 0` is likewise `ABOVE_PLAN` and not an
error. **No tolerance band exists** — a difference of `0.0001` is `BELOW_PLAN`, not "close
enough." No percentage variance is computed when planned is zero or missing.

**`NOT_VALIDATED` is deliberate, not a gap.** A row can carry an actual quantity with no
planned quantity to compare it against. Treating the missing planned as zero would invent a
rule and would report the task as a false `ABOVE_PLAN`; instead the condition is exposed as
unresolved (`NOT_VALIDATED`) and flagged `MISSING_PLANNED` (§6). `NO_ACTUAL` still wins over
`NOT_VALIDATED` when there is no reported actual at all — that judgement needs no
comparison.

These five values are also this report's top-level grouping in definition order — `ON_PLAN
→ ABOVE_PLAN → BELOW_PLAN → NO_ACTUAL → NOT_VALIDATED` — fixed regardless of the data, so
the same status is always in the same place from one day's report to the next.

## 5. UOM

`ref.uom.uom_code` is displayed as stored. **No conversion between units is defined**: Km ↔
M, Joint ↔ Joints and well ↔ Nos are all distinct units. Consequently:

* quantities are summed **only within a single UOM** — never across units, never converted
  to make a total fit;
* a group (however it is formed) reports a planned/actual total only when every task in it
  shares one unit; when it spans several, the total is withheld — **not reported as
  zero** — and the units involved are named instead;
* day-level totals report task and well counts but deliberately carry **no** quantity, for
  the same reason;
* an explanation scope that spans several UOM has its totals withheld the same way, with the
  reason stated in the evidence sent to the model.

`uom_code` is a `varchar` column holding `m²` and `m³` as single-byte characters; the
connection sets `DB_ANSI_CODEPAGE` (default `cp1252`) so those arrive intact.

`activity_uom` is reference evidence only — see §2. It is never used to group, convert, or
fill in a missing `uom_code`.

## 6. Mapping and data quality

`mapping_status` reports the first break in the §2 chain, and only the first — a task with
no `activity_code` is `UNMAPPED_ACTIVITY`, not four separate failures:

`MAPPED` · `UNMAPPED_ACTIVITY` · `UNMAPPED_WBS` · `UNMAPPED_DESCRIPTION` · `UNMAPPED_CREW`

Missing information stays missing at every stage — never a substituted value.

Data-quality conditions are tracked **separately from the operational numbers** and never
fold into a quantity or a status:

`UNMAPPED_ACTIVITY` · `UNMAPPED_WBS` · `UNMAPPED_DESCRIPTION` · `UNMAPPED_CREW` ·
`MISSING_UOM` · `INVALID_DAILY_JSON` · `UNPARSEABLE_ACTUAL_QUANTITY` ·
<<<<<<< HEAD
`DUPLICATE_ACTUAL_ENTRY` · `MULTIPLE_TASK_ROWS` · `MISSING_PLANNED` · `MALFORMED_TASK_CODE`
=======
`DUPLICATE_ACTUAL_ENTRY` · `MULTIPLE_TASK_ROWS` · `MISSING_PLANNED` · `MALFORMED_TASK_CODE` ·
`INVALID_DATE_SEQUENCE` (§10 — `actual_end` recorded before `actual_start`)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

## 7. Not yet defined

Answer what you can and state plainly that the rule is undefined — never invent one:

* **Quantity tolerance.** None applied; any difference is classified exactly (§4).
* **Whether a quantity difference is an error.** It is not — `ABOVE_PLAN`/`BELOW_PLAN` are
  descriptive facts, never error states (§4).
* **UOM conversion.** None defined; units are never converted or summed across (§5).
* **Meaning of `task_daily.required`.** The column is not read anywhere in this version.
* **Unit of `task_daily.progress`.** Reported exactly as stored, never as a percentage.
  Observed values range from −0.05 to 66.7, so a 0–1 fraction cannot be assumed. Never
  describe it as a completion percentage.
* **Cause of a quantity difference.** Not recorded anywhere in this evidence. Never inferred.
* **Accountability / crew performance.** Not recorded. Never assigned from a quantity status
  or a data-quality flag.

(A well's or a construction phase's *actual completion date* is a well-lifecycle question,
not a daily-report one — see `business_rules.md` for that.)

## 8. Crew suggestion

An advisory extension of the existing task-specific AI summary — never a separate feature.
Nothing below changes what the summary endpoint is, only what it can additionally say when
the selected task qualifies. Rules marked **V1 derived** are this feature's own presentation
choices, not an authoritative workforce rule from anywhere else in this system.

1. **No new surface.** A crew suggestion appears only inside the existing task-specific AI
   summary (`scope="task"` with exactly one resolved task on `/api/daily/explain`). There is
   no "Suggest Crew" button, no crew-suggestion page or modal, and no second request — the
   frontend calls the same endpoint it already calls today.
2. **Advisory only.** A suggestion is evidence for a person to weigh, nothing more. It is
   never described as an assignment or a reassignment, and no database write occurs anywhere
   in this feature — see `sql/crew_suggestion.sql` and `app/repositories/crew_repository.py`,
   both pure `SELECT`s validated by the same read-only guard as every other query.
3. **Task/activity-specific.** The suggestion is scored against the ONE resolved activity of
   the ONE target task (`well_id`, `task_code`, `report_date`), resolved dynamically through
   the same `task_code → activity_id → mapping_master → activity_master_csv` chain as §2 —
   never a hardcoded well, task, activity or crew.
4. **Historical success is task-level.** A crew has proven an activity by completing that
   specific task (`task_daily.completed = 1`) on another well — **the historical well itself
   need not be completed.** A crew that finished a task on a well that is otherwise still
   incomplete is still valid evidence for that activity. Historical evidence is never filtered
   by `well_master.eng_completion_date IS NOT NULL`.
5. **Historical evidence respects the report date.** Only historical completions with
   `ActionOn <= report_date` count. An old, pending task is never explained using a crew's
   later success the record-keeping did not yet know about at that date — the same report-date
   discipline this file already applies everywhere else.
6. **A task already progressing is not eligible for a *replacement* suggestion.** Decided in
   SQL, never by the LLM: `crew_suggestion_eligible = 0` when the target task's latest logical
   state (as of `report_date`) has `completed = 1`, or has `progress IS NOT NULL AND progress
   > 0`. A machine-checkable `crew_suggestion_suppression_code` (`NULL` / `IN_PROGRESS` /
   `COMPLETED`) travels alongside the human-readable `crew_suggestion_suppression_reason`, so
   the service branches on the code, never on the sentence text.

   A **completed** task (`COMPLETED`) gets nothing further — no replacement, no informational
   mention; there is nothing left to add once a task is done. A task that is merely
   **in progress** (`IN_PROGRESS`) still gets the same ranked historical crew attached, when one
   exists, but framed only as an informational aside — "this crew has relevant experience with
   this activity and could be worth asking for feedback or information" — never as a
   replacement, an assignment, or advice to change anything. This exists so the feature stays
   visible even on a task that needs no change, rather than going silent every time nothing is
   wrong. When no historical crew exists at all for an in-progress task, the summary continues
   with no crew mention of any kind, exactly as it would for a completed task.
7. **`task_daily.progress` is never a percentage here either.** It is used only as a
   `> 0` / not `> 0` signal to decide eligibility — never rendered, multiplied, or described as
   a degree of completion, per §7 above.
8. **Typical vs average duration.** "Typical" always means the **median** historical
   duration (`PERCENTILE_CONT(0.5)`); "average" always means the **arithmetic mean**. The two
   are never conflated or labelled with each other's name. Duration is
   `DATEDIFF(DAY, actual_start, actual_end)` for historical rows where both dates exist —
   no other definition of duration is used.
9. **Crew availability is a derived V1 signal, not an authoritative workforce status.** There
   is no availability/status table in this system. A crew is treated as busy only when its own
   latest logical task state (same `PARTITION BY well_id, schedule_id, task_code ORDER BY
   ActionOn DESC, updated_at DESC, id DESC` grain as everywhere else raw snapshots are
   resolved) shows `completed = 0 AND actual_end IS NULL` on a still-incomplete well. Multiple
   daily snapshots of the same task are never counted as multiple busy tasks. `NO_CURRENT_
   UNFINISHED_TASK` means exactly that — no current unfinished task was found in the available
   records — and must never be reported as "available" or "free" without that qualification.
   `ref.employee.emp_status` is employee status, not crew availability, and is never read by
   this feature.
10. **Busy crews are excluded, not penalised.** Availability is a hard filter applied before
    ranking, never a ranking score.
11. **Ranking is fixed and deterministic (V1 rule), never an ML score.** Among available
    candidates with at least one historical completion: most same-activity completions first,
    then most distinct completed wells, then closest-to-typical (lowest) median duration, then
    most recent success, then `crew_id` as the final tie-break.
12. **Evidence strength** is a plain, fixed bucketing of the historical completed-task count:
    `>= 5` → `STRONG_HISTORY`, `2–4` → `LIMITED_HISTORY`, `1` → `SINGLE_HISTORY`, `0` →
    `NO_HISTORY`. Only crews with at least one completed historical record are ever suggested.
13. **Every suggested crew carries its own "why".** The evidence always includes the
    historical completed-task count, distinct-well count, completion-on-incomplete-well count,
    typical/average/shortest/longest duration, most recent success date, evidence strength and
    derived availability — the LLM explains only these supplied facts, never a reason of its
    own invention.
14. **The current crew, if `NULL`, is reported exactly as that.** `task_daily.crew_id IS NULL`
    is reported as "crew is not recorded/associated with this task record" — never as "no crew
    was assigned," which would claim more than a missing value proves.
15. **No blame, ever.** The current crew is never described as bad, slow, or at fault. A task
    running longer than the historical pattern is stated as a fact about the pattern, never as
    a judgement of the crew already on the task — consistent with §7's "accountability /
    crew performance: not recorded" rule.
16. **SQL/Python decides; the LLM only narrates.** Eligibility, historical statistics,
    availability and ranking are all computed in `sql/crew_suggestion.sql` and
    `app/services/crew_suggestion_service.py`. The LLM (`gpt-4o-mini`, via the same
    `LLM_PROVIDER`/`LLM_MODEL` configuration and system instruction as every other
    explanation) never calculates a duration, never invents a cause, and never assigns a crew
    of its own choosing.
17. **Evidence with nothing to say never reaches the model.** A system-instruction rule
    cannot by itself guarantee a non-deterministic model stays silent when there is truly
    nothing to add, so `app/api/routes_explain.py` withholds the `crew_suggestion` evidence
    from the LLM call entirely whenever it carries none of `suggested_crew`, `consult_crew` or
    `no_suggestion_reason` — a completed task, or an in-progress task with no historical crew to
    point to. It has nothing to narrate in that case. The full evidence, including the
    suppression reason, still reaches the client in the response for transparency and audit;
    only the model's own input is narrowed. An in-progress task that *does* have a historical
    crew to point to (`consult_crew`) is deliberately **not** withheld — see §6/#16 below — so
    the feature's existence stays visible even when nothing needs to change.
18. **Never say the task is "progressing normally" for a replacement suggestion, and never
    call a replacement suggestion "worth consulting for feedback."** `suggested_crew` (a
    replacement candidate) and `consult_crew` (an informational, in-progress mention) use
    deliberately different closing language, enforced in the system instruction, so the two
    cases — a stalled task and a healthy one — are never described with each other's framing.
19. **No hardcoded business values.** `well_id`, `task_code` and `report_date` are the only
    parameters `sql/crew_suggestion.sql` takes; no well, project, WBS, activity, task or crew
    identifier is ever hardcoded anywhere in this feature.

## 9. Well task activity

How a live well's own tasks are counted and described on the front page and in its AI
summary. Rules marked **V1 derived** are this feature's presentation choices, not an
authoritative rule from elsewhere in this system. Nothing here changes §4 (quantity status):
that classifies one day's reported quantity, this describes where a well's tasks stand.

1. **The well universe is `well.well_master`, never `task_daily`.** A live well is defined by
   `business_rules.md` §7 (`eng_completion_date IS NULL`) and is listed whether or not it
   reported a task on the selected date. A `task_daily` row never creates a well: the join
   runs from `well_master` into `task_daily` through `TRY_CONVERT(int, td.well_id)` (§1), and
   `well_id <= 1` is excluded exactly as everywhere else. A live well with no task record at
   or before the report date returns no row — not a row of zeroes, which would claim it had
   been measured.
2. **One logical task is `(well_id, schedule_id, task_code)`.** Its state as of the report
   date is its latest daily record ordered `ActionOn DESC, updated_at DESC, id DESC`, taken
   only from rows with `ActionOn <= report_date`. A row dated after the report date never
   affects an earlier one. The same `task_code` under two `schedule_id`s is **two tasks**,
   not a duplicate; the schedule id is carried everywhere the pair could otherwise be
   mistaken for one row.
3. **Task state is read from the task's own state columns.** Four states, mutually exclusive
   and covering every task:

   | State | Condition | Counted in |
   |---|---|---|
   | `COMPLETED` | `completed = 1` | — |
   | `ONGOING` | not completed, `actual_start` recorded, `actual_end` not recorded | Ongoing |
   | `NOT_STARTED` | not completed, no `actual_start` | Incomplete |
   | `ENDED_NOT_COMPLETED` | not completed, yet an `actual_end` is recorded | Incomplete |

   `ENDED_NOT_COMPLETED` is reported as itself. The record says both things and no rule
   resolves them, so the condition is exposed rather than guessed at — the same discipline
   `NOT_VALIDATED` follows in §4. It is not an error.
4. **`task_daily.progress` is never read by this feature at all** — not as a filter, not as a
   tie-break, not in the payload. Its unit is undefined (§7), so it cannot say whether a task
   is finished or under way. This is stricter than §8's crew-suggestion rule, which uses it as
   a `> 0` signal: here it is not used at all.
5. **`startDate` / `endDate` are never read as proof of work either.** A planned schedule is
   not evidence that work is physically happening. Only `actual_start` / `actual_end` are.
6. **`actual_end IS NULL` alone is not "ongoing".** It also matches a task that has never
   started. `ONGOING` always requires all three conditions in rule 3.
7. **The reported figures are disjoint and add up (V1 derived).**

       open        = every task whose latest record does not say completed
       incomplete  = open AND NOT ongoing  (NOT_STARTED + ENDED_NOT_COMPLETED)
       ongoing     = open AND actual_start recorded AND no actual_end

   so `open = incomplete + ongoing`, and `logical = open + completed`. No task is ever
   counted in both figures shown beside each other. "Incomplete" therefore means open but
   not ongoing — it is **not** a synonym for "not completed", which is `open`. An earlier
   version had incomplete include the ongoing tasks, so the two numbers on a well's row
   overlapped and could not be added.
8. **`last_task_date` is `MAX(ActionOn)` on or before the report date**: the latest date the
   well appears in the task-daily data. It is **not** a completion date, a finish date, or
   the date work stopped, and must never be labelled or described as one — no column is
   approved as a construction actual completion date (`business_rules.md` §10).
9. **The report-date task count reuses §3's grain**, read from the day's already-resolved
   dataset rather than re-derived: repeated planning snapshots of one task are never counted
   twice, and the figure is by construction identical to that well's task count elsewhere in
   the brief. Zero means no task was recorded for that well on that date — nothing more.
10. **SQL and Python calculate every figure; the model only explains them.** The counts, the
    state classification, the dates and the samples are computed in
    `sql/well_task_state.sql`, `sql/well_task_activity.sql`,
    `sql/well_task_activity_detail.sql` and `app/services/well_activity_service.py`. React
    displays what it is given. The LLM receives the finished figures, their plain meaning and
    a bounded sample of the ongoing tasks, and derives nothing.
11. **Nothing here is described as late (V1 derived).** No rule in this system defines when a
    task is delayed, overdue, behind schedule or at risk, so no task and no well is ever
    described that way — consistent with §7's "cause" and "accountability" rules. A well that
    reported nothing on a date is stated as exactly that, never as idle, stalled or failing to
    report.
12. **The whole-view summary covers the same universe this file defines (V1 derived).** The
    day-scope explanation is given the live-well figures above — how many wells have task
    evidence, how many reported on the date, how many carry incomplete or ongoing work —
    alongside the day's own reported tasks, and the two are kept apart: one is what was
    entered on this date, the other where every live well's tasks stand as of it. A request
    narrowed to a status, WBS, activity, unit or well is explaining a slice of the day's
    reported tasks and is **not** given the well universe, which would be a different
    population than the one in scope.
13. **An absence of entries is stated as itself, never as a row of zeroes.** A scope with no
    daily task on the report date carries one plain statement to that effect — not a zeroed
    well count, zeroed status counts and withheld quantity totals, which are true of an empty
    selection but describe the payload rather than the well. A single-well scope never reports
    a well count at all. A task reported with `NO_ACTUAL` **was** reported: an entry carrying
    no actual quantity and a well that recorded nothing that day are separate facts and must
    never be merged.
14. **Every count is checkable (V1 derived).** A well's AI summary carries, beside the
    explanation, the evidence the model was given, the queries the figures came from as they
    are executed, and one row per incomplete task stating whether it is also counted as
    ongoing and why. The "why" is assembled from that task's own `completed`, `actual_start`
    and `actual_end` values and nothing else -- a restatement of the record, never an
    interpretation of it. Neither the queries nor the proof rows are ever sent to the LLM:
    it explains figures SQL already decided, and giving it the query would invite it to
    reason about the query instead (rule 10).
15. **No hardcoded business values.** The report date is the only parameter these queries
    take, bound once; no well, project, WBS, activity, task, crew or date is hardcoded
    anywhere in this feature.

<<<<<<< HEAD
## 10. Strict instructions
=======
## 10. Crew match and date validation

Both computed in `app/services/validation_service.py`, from raw evidence carried by
`sql/daily_tasks.sql` — same discipline as §4: exact, deterministic, never a guessed
tolerance. Neither field is read by §8's crew-suggestion feature or §9's well task
activity, and neither of those features feeds this one.

1. **Crew match compares two independently-maintained columns that happen to share
   one code space.** `crew_match_status` compares `activity_master_csv.crew_code`
   (§2's WBS-planned crew) against `ref.crew_type.crew_type_code` (the TYPE of the
   crew instance actually recorded via `task_daily.crew_id` — not `crew_type_name`,
   which is a free-text label, not a code). Measured, on rows where both resolve:
   22,947 of 23,314 (98.4%) match exactly, byte for byte — that measured agreement
   rate is what justifies comparing them at all, and no hyphen or case rewriting is
   applied beyond a case-insensitive, whitespace-trimmed comparison.
2. **`task_daily.planned_crew` is not used.** It was the more obviously-named
   "assigned crew" candidate and was rejected after measurement: it is `NULL` on
   every row observed (110,184 of 110,184). Using it would silently produce no
   evidence at all while looking like a real check.
3. **Values: `MATCHED` · `MISMATCHED` · `NOT_COMPARABLE`.** `NOT_COMPARABLE` covers
   both a task with no crew recorded (`task_daily.crew_id IS NULL` — the majority
   case, measured ~69% of rows) and a task with no WBS crew mapped (§6's
   `UNMAPPED_CREW`). Never forced into a match or a mismatch either way.
4. **No blame.** A mismatch is a fact about two records, never a judgement of the
   crew on the task — same doctrine as §8 rule 15 ("no blame, ever").
5. **Date validation compares `task_daily.startDate`/`.endDate` (the planned start/
   end) against `task_daily.actual_start`/`.actual_end` (the reported start/end),
   independently for start and end.** Three candidate "planned" column pairs exist
   on `task_daily` — `startDate`/`endDate`, `target_start`/`target_end` and
   `committed_start`/`committed_end` — and they disagree with each other on 40-60%
   of rows measured. `startDate`/`endDate` is the one used here, by decision, not
   by default; `target_start`/`target_end` and `committed_start`/`committed_end` are
   not read by this feature.
6. **Values: `ON_TIME` · `EARLY` · `LATE` · `NO_ACTUAL` · `NOT_VALIDATED`.** Mirrors
   §4's shape exactly. Being early is a descriptive fact, never an anomaly
   (`business_rules.md` §4); `NOT_VALIDATED` (planned date missing) has not been
   observed in practice — `startDate`/`endDate` are populated on every row measured
   — but is exposed rather than assumed impossible.
7. **`INVALID_DATE_SEQUENCE` is a separate data-quality flag** (§6's list), fired
   when `actual_end < actual_start` on the same task — a logically impossible
   sequence, not a variance from plan. Measured: 214 of 110,184 rows (~0.19%).
8. **Neither classification is quantity status.** A task can be `ON_PLAN` on
   quantity and `MISMATCHED` on crew, or `LATE` on `end_date_status` and `MATCHED`
   on crew — all three are independent facts about the same row, never merged into
   one verdict.

## 11. Strict instructions
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

* These rules are authoritative for every question about a daily task's quantity status,
  UOM, activity/WBS/crew mapping, or data quality.
* Do not infer, modify, or invent a rule.
* If the database holds a value that conflicts with a rule here, **report the database
  value** and explain the rule's interpretation separately. Never silently change a database
  result.
<<<<<<< HEAD
=======

## 12. Agentic amendments and rules

**Agentic edition.** §1–§11 above are the original project's rules, byte for byte. They are
also the text `dynamic_db` compiles SQL against, so they are deliberately left untouched.
Three of them forbade what agents do; §12.1 amends exactly those three, for agents only.
**Where §12 and an earlier rule disagree, §12 governs agent behaviour.**

How the agent layer (`backend/app/agentic/`) may use everything above. §12.1 amends three
earlier rules, and only for agents; §12.2 adds rules of this edition's own. Neither changes a
definition, a calculation or a classification in §1–§11.

### 12.1 Amended rules

* **§8 rule 1 — where a crew suggestion may appear [AMENDED].** A crew suggestion appears
  inside the task-specific AI summary (`scope="task"` on `/api/daily/explain` or
  `/api/agent/brief`) **and** in an agent's answer to a question about that task, reached
  through the `crew_options` / `task_detail` tools. Either way it is the same SQL result
  (`sql/crew_suggestion.sql` via `crew_suggestion_service`) for exactly one resolved task,
  with every other rule of §8 unchanged. There is still no "Suggest Crew" button, page or
  modal, and no agent produces a crew suggestion for a task it has not resolved.
  *Original:* only inside the task summary, with no second request.

* **§8 rule 16 — who decides [AMENDED].** Eligibility, historical statistics, availability
  and ranking are still computed only in `sql/crew_suggestion.sql` and
  `crew_suggestion_service`. An agent may decide *whether* to call the crew tool for a task
  it has resolved; the writer agent (model per `AGENT_WRITER_MODEL`, falling back to
  `LLM_MODEL`, with the same system instruction as every other explanation) narrates the
  result. No agent calculates a duration, re-ranks crews, invents a cause, or assigns a crew.
  *Original:* the LLM (`gpt-4o-mini`) only narrates.

* **§9 rule 11 — open work before a deadline [AMENDED].** Still: no rule defines when a
  *task* is delayed, behind schedule or at risk, so no task and no well is described that
  way, and a well that reported nothing is stated as exactly that. Added: one deterministic,
  factual condition, computed in Python (`app/agentic/tools.py`, `deadline_pressure`) from
  two existing results and nothing else —
  * **open work before an upcoming deadline**: at least one open task (§9 rule 2) **and** a
    milestone deadline (`business_rules.md` §3) within `AGENT_DEADLINE_WINDOW_DAYS` of today;
  * **open work past an overdue deadline**: at least one open task **and** a milestone
    deadline that has already passed (`business_rules.md` §3 defines an overdue milestone).

  These are statements of open work and a date, worded exactly as above — never a
  judgement. *Late, delayed, behind, stalled, idle* and *at risk* stay forbidden, and the
  verifier (§12.2 rule 5) rejects a draft that uses them. Milestone deadlines are evaluated
  against **today**, task records against the report date, and the two are never merged.
  *Original:* nothing is described as late, and no deadline condition existed.

### 12.2 Agent rules

1. **Agents choose; tools compute.** An agent decides *which* tool to call and with *which*
   arguments. Every figure comes from a tool, and every tool is a fixed, read-only call into
   the same SQL/Python services the classic dashboard uses. No agent writes SQL, names a
   column, supplies a formula, or reaches the database any other way.
2. **Tools validate their arguments.** A well id must be an integer, a limit is clamped, an
   unknown filter or tool is refused. A refusal is returned to the agent as data; it is never
   guessed around.
3. **The dashboard's own summaries use fixed plans.** The day, well and task briefs run the
   same tool steps every time (no planning call), so they stay as repeatable as the classic
   summaries. Only an operator's open question is planned by the planner agent, whose plan
   is validated against the tool catalogue and capped at `AGENT_MAX_TOOL_CALLS`.
4. **The writer states only what the tools returned.** It never adds, subtracts, averages,
   converts or turns figures into percentages, never names a tool or a JSON field, and says
   plainly when a tool failed or when the question cannot be answered from the report.
5. **Every draft is verified before it is shown.** A deterministic check requires every
   figure, date and well id in the draft to appear in the tool results (or the question),
   rejects the judgements §12.1 keeps forbidden unless the evidence carries the word as a
   status code (such as `LATE` in §10), and rejects field names written into the prose. A
   rejected draft is revised at most `AGENT_MAX_REVISIONS` times; an answer that still fails
   is shown **marked as unverified**, with the issues listed — never silently.
6. **Advisory only, as before.** Nothing an agent says assigns, reassigns, approves or
   changes anything. The crew rules of §8 apply to every agent answer that mentions a crew.
7. **Transparent by default.** Every answer carries its plan, each tool call (arguments,
   duration, success), each model used and the verifier's verdict, beside the classic
   evidence, SQL and proof drawers.
8. **One run, one cache.** Verified answers are reused within a run for identical tool
   results; Refresh drops that date's answers and a restart drops all of them. Nothing an
   agent produced is stored on disk.
9. **No new definitions.** If a question needs a rule that is not defined in this file or in
   `business_rules.md`, the agent says the rule is not yet defined (§7) rather than using one
   of its own.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

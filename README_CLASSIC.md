# Al Tasnim — Daily Morning Brief & Daily Entry Validation

An operational dashboard over the Al Tasnim well-construction database. It shows what
live wells did on a given day, validates each daily entry's reported quantity against
its planned quantity, and lets an operator drill from a single summary figure all the
way down to the individual database records behind it.

```
Daily Summary (one row per well) → Well → Daily Tasks → AI Explanation
```

The front page reads top to bottom in the order an operator needs it (§22):

1. **What is running out of time** — upcoming and overdue lifecycle deadlines, measured
   against *today* rather than against the selected report date (§16).
2. **The day's AI summary**, opened by the page itself on first load, over everything the
   front page can see — the brief is there to be read, not to be asked for.
3. **The wells table** — one row per live well, sorted busiest first: well ID, its main
   activity (plus how many others), task count, its non-zero status counts (`On Plan`,
   `Above Plan`, `Below Plan`, `No Actual`, `Not Validated`), that well's task activity as
   of the selected date — incomplete, ongoing, reported today, and the last date it appears
   in the task records at all (§19) — and, as the **last cell of every row, that one well's
   own AI summary**.

An earlier version organised the day by validation status, then work category, then
activity, before ever reaching a well; that hierarchy was accurate but took several clicks
to answer the question the brief exists to answer first: *what did each well do, and how did
it come out?* The backend still computes and serves that grouped hierarchy
(`/api/daily/summary`, §5, §8) — nothing about the classification changed — the well-first
table is a front-end presentation choice on top of the same evidence. Clicking a well id
opens that well's full detail; each task-activity figure expands, in place inside that same
row group, into the tasks behind it; and the row's AI summary expands an explanation of that
one well underneath it, without navigating away.

The architecture has one rule that everything else follows:

```
                    SOURCE OF TRUTH
                         │
                         ▼
                     SQL Server
                         │
                         ▼
               SQL / Python evidence layer
                         │
              ┌──────────┴──────────┐
              ▼                     ▼
         Dashboard              LLM input
              │                     │
              ▼                     ▼
       Deterministic UI        Explanation only
```

**SQL and Python calculate. React displays and interacts. The LLM explains.**
The LLM never queries the database, never calculates a value and never overrides a
classification.

---

## 1. Quick start

### Both together

```bash
python run.py
```

Starts FastAPI and the React dev server in one terminal, streaming both with an
`[api]` / `[ui]` prefix. Ctrl+C stops both, and if either exits on its own the other
is stopped too, so you never end up with a half-running stack.

| Flag | Effect |
|---|---|
| *(none)* | both, with backend autoreload |
| `--backend` | FastAPI only |
| `--frontend` | React only |
| `--install` | `npm install` first, then start |
| `--no-reload` | no backend autoreload — steadier for a demo |
| `--build` | build the frontend and serve the production build via Vite preview |

Host and port come from `backend/.env` (`API_HOST`, `API_PORT`) and `frontend/.env`
(`VITE_PORT`); nothing is hardcoded in the launcher. It refuses to start if a port is
already taken, and tells you which one. It also prefers the project's `.venv`
interpreter, so `python run.py` works from any shell.

The sections below are the equivalent manual steps.

### Backend

```bash
cd backend
python -m venv .venv && .venv/Scripts/activate      # Windows
pip install -r requirements.txt
cp .env.example .env                                 # then fill it in
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

API docs: <http://127.0.0.1:8000/docs> · Health: <http://127.0.0.1:8000/api/health>

Requires the Microsoft ODBC Driver for SQL Server (the driver name goes in `DB_DRIVER`).

### Frontend

```bash
cd frontend
npm install
npm run dev
```

The dev server proxies `/api` to `VITE_API_PROXY_TARGET`, so no backend URL is baked
into the React build.

### Tests

```bash
cd backend
python -m pytest
```

323 tests, including 7 in `tests/test_dynamic_db_contract.py` that exercise DYNAMIC_DB (§20)
through `app/dynamic_client.py` only — never its internals. The deterministic tests run
offline; the tests in `tests/test_live_wells_db.py`, `tests/test_wells_live_db.py`,
`tests/test_crew_suggestion_live_db.py` and `tests/test_well_activity_live_db.py` run against
SQL Server and skip themselves when it is unreachable.

```bash
cd DYNAMIC_DB
python -m pytest
```

164 tests for the schema-adaptive engine itself, run independently of the Daily Report suite
above — see §20 for what this project is and why it is separate. All but a handful (marked
`database`) run offline against an invented fixture schema.

```bash
cd frontend
npm test
```

57 component tests (Vitest + Testing Library, jsdom). They render a component with fixed
backend responses and assert what an operator would see — the task-activity figures on a
well row, which well an expanded task list belongs to, that a date change closes an open
explanation, that "Refresh" reloads every part of the front page, that a freshly
generated explanation can never be shown as a reused one, the order the front page loads in
(deadlines, then the day's summary, then the wells), that a reopened summary comes back from
the session cache without asking again while an unavailable one never does, and that a
suggested crew is named by its id. None of them reaches the API, the database or the LLM.

---

## 2. Project layout

```
project-root/
├── README.md                    ← this file
├── ARCHITECTURE.md               ← full design of the DYNAMIC_DB / Daily Report split (§20)
├── run.py                       ← dev launcher: starts backend + frontend together
├── llm_usage_tracker.py         ← independent LLM token-expense logger (§10) -- reads only backend/.env
├── llm_usage_log.xlsx           ← its output: LLM Usage (one row per real call this app made) +
│                                 OpenAI Usage + Reconciliation (what OpenAI actually billed, via `sync`)
├── test_llm_usage_tracker.py    ← its own tests -- run standalone: pytest test_llm_usage_tracker.py
├── DYNAMIC_DB/                  SCHEMA-ADAPTIVE ENGINE -- an independent project (§20)
│   ├── .env                     ← its OWN environment (own DB connection, own reasoning model)
│   ├── .env.example
│   ├── requirements.txt
│   ├── pyproject.toml
│   ├── dynamic_db/
│   │   ├── config.py             its own Settings, its own .env -- no fallback to backend/.env
│   │   ├── db.py                 its own read-only SQL Server client
│   │   ├── identity.py           which database, and where its cache lives
│   │   ├── introspect.py         live schema -> snapshot, rendering, fingerprints
│   │   ├── changes.py            what moved, as a machine-readable report
│   │   ├── capabilities.py       a GENERIC, empty-at-import capability registry
│   │   ├── rules.py              a generic numbered-markdown-section slicer (no document names)
│   │   ├── prompts.py            generic prompts -- no physical name appears here
│   │   ├── sqltext.py            deterministic SQL text analysis
│   │   ├── dependencies.py       what one query actually reads
│   │   ├── validation.py         the hard gate: safety, schema, contract
│   │   ├── sqlcheck.py           advisory, schema-derived concerns
│   │   ├── llm.py                the reasoning model call + injectable usage-accounting sink
│   │   ├── llm_config.py         provider/model-aware generation-parameter resolution
│   │   ├── artifacts.py          versioned compiled SQL, with promotion
│   │   ├── bootstrap.py          the fingerprint-gated startup orchestrator (state machine)
│   │   ├── logging_setup.py      one structured logging pipeline, own logger namespace
│   │   ├── state.py              the typed compile state
│   │   ├── nodes/                one module per graph node
│   │   ├── graph.py              the LangGraph StateGraph
│   │   ├── service.py            which SQL serves a request, right now
│   │   ├── audit.py              physical names in generic code (a test; caller-parameterised)
│   │   └── cli.py                operator commands -- `python -m dynamic_db.cli ...`
│   └── tests/                    164 tests, run standalone: `cd DYNAMIC_DB && python -m pytest`
├── backend/
│   ├── .env                     ← environment-specific values (never committed)
│   ├── .env.example
│   ├── business_rules.md        ← general/organisational rules (PDO, Al Tasnim, well lifecycle)
│   ├── daily_report_rules.md    ← daily-report-specific rules (quantity status, UOM, mapping)
│   ├── requirements.txt
│   ├── pytest.ini
│   ├── app/
│   │   ├── main.py              FastAPI app, request logging, error handling, starts the
│   │   │                        DYNAMIC_DB bootstrap in a background thread at startup
│   │   ├── api/                 routes + shared dependencies (incl. routes_bootstrap.py, §20)
│   │   ├── services/            all business processing (incl. crew_suggestion_service.py)
│   │   ├── repositories/        all database access (incl. crew_repository.py)
│   │   ├── schemas/             API request/response contracts (incl. crew_suggestion.py)
│   │   ├── models/              internal domain objects
│   │   ├── config/              settings + read-only database access
│   │   ├── capability_manifest.py  DAILY REPORT'S OWN 7 capabilities, as data for DYNAMIC_DB (§20)
│   │   ├── dynamic_client.py    THE stable interface to DYNAMIC_DB -- the only other way in (§20)
│   │   ├── _dynamic_db_path.py  makes ../DYNAMIC_DB importable, order-independent
│   │   └── utils/               SQL loader, logging
│   ├── sql/                     BASELINE implementations, registered as each
│   │                            capability's version 1: daily_tasks · daily_detail ·
│   │                            daily_summary · daily_activity_dates · well_milestones ·
│   │                            crew_suggestion (§18) · well_task_state ·
│   │                            well_task_activity · well_task_activity_detail (§19)
│   └── tests/                   323 tests, incl. test_crew_suggestion.py,
│                                 test_crew_suggestion_wiring.py, test_crew_suggestion_live_db.py,
│                                 test_well_activity.py, test_well_activity_api.py,
│                                 test_well_activity_live_db.py, and
│                                 test_dynamic_db_contract.py -- exercises DYNAMIC_DB only through
│                                 dynamic_client.py, never its internals (§20)
└── frontend/
    ├── package.json
    ├── vite.config.js
    ├── .env.example
    └── src/
        ├── components/          DayStrip · WellList · StatusCount ·
        │                        WellDetail · MilestoneBanner (MilestonesPage) ·
        │                        ExplainPanel · Bootstrap (BootstrapGate, §20) · common
        │                        (*.test.jsx beside the component it covers)
        ├── pages/DailyMorningBrief/
        ├── services/api.js      the only place the UI calls the backend
        ├── hooks/
        └── styles/
```

---

## 3. Definitions this application uses

All of these come from one of two rules files, never invented: `backend/business_rules.md`
(general — PDO/Al Tasnim scope, well lifecycle, milestones) or `backend/daily_report_rules.md`
(specific to classifying and grouping one day's `task_daily` entries — quantity status, UOM,
progress, activity/WBS/crew mapping). The two are kept apart so a change to how a daily task
is classified never has to touch, or risk touching, a well-lifecycle rule that has nothing to
do with it, and vice versa.

| Concept | Definition used | Source |
|---|---|---|
| **Live well** | `well.well_master.eng_completion_date IS NULL` | business_rules.md §7 |
| **Completed well** | `well.well_master.eng_completion_date IS NOT NULL` | business_rules.md §7 |
| **Daily task source** | `well.task_daily`, filtered to one `ActionOn` date | daily_report_rules.md §1 |
| **Daily actual quantity** | `TRY_CONVERT(DECIMAL(18,4), JSON_VALUE(daily_data, '$.actual_quantity'))`, guarded by `ISJSON` | daily_report_rules.md §1 |
| **Planned quantity** | `task_daily.planned` | daily_report_rules.md §1 |
| **Progress** | `task_daily.progress`, reported exactly as stored | daily_report_rules.md §1 |
| **Activity id** | `LEFT(task_code, NULLIF(CHARINDEX('-', task_code), 0) - 1)` | daily_report_rules.md §2 |
| **Activity code** | `dbo.mapping_master.New_Activity_Code`, joined on `CAST(mapping_master.Activity_ID AS nvarchar(50)) = activity_id` | daily_report_rules.md §2 |
| **WBS** | `activity_master_csv.activity_group_description` — only this | daily_report_rules.md §2 |
| **Crew** | `activity_master_csv.crew_code` — only this | daily_report_rules.md §2 |
| **UOM** | `ref.uom.uom_code`, joined on `task_daily.uom_id` | daily_report_rules.md §1 |

`status_id`, `progress` and `flowline_const_status_id` are **not** used to decide whether a
well is live. Task rows with `well_id <= 1`, or with a non-numeric `well_id`, are excluded.
`task_daily.data_qty` is never read as the actual quantity, and `task_daily.required` is not
read at all (see §9).

**Schema drift, discovered while fixing a crash:** `well.task_daily.well_id` is now stored as
`varchar`, not `int` — it used to be `int` (`well.well_master.well_id` still is). A minority of
rows hold non-numeric junk in it (`'0000F'`, `'0000I'`, `'0000J'`, and blank). A bare comparison
or JOIN against the int `well_master.well_id` throws `SQLSTATE 22018` and takes the whole report
down with it. Every use of `task_daily.well_id` in this codebase goes through
`TRY_CONVERT(int, td.well_id)`, which returns `NULL` for junk instead of raising — the `INNER
JOIN` to live wells then excludes it the same way it already excludes `well_id <= 1`. This is
enforced by a test (`test_well_id_is_never_compared_or_joined_without_try_convert`) so a future
edit can't reintroduce the crash.

---

## 4. Daily row-grain resolution

`well.task_daily` holds several rows that share `(well_id, schedule_id, task_code, ActionOn)`.
They are different planning snapshots plus, usually, one actual-entry row. Getting this
wrong is the fastest way to a wrong number, so the strategy is explicit, isolated in
`sql/daily_tasks.sql`, and covered by tests.

**What the data actually looks like** (measured across the whole table, not assumed):

* 2,729 groups have more than one row;
* **zero** groups have more than one actual-entry row;
* multi-row groups differ in their `planned` value — they are planning snapshots.

**The strategy.** Partition by `(well_id, schedule_id, task_code, ActionOn)` and rank:

1. `is_actual_entry DESC` — an actual-entry row outranks a planning snapshot;
2. `updated_at DESC` — then the most recently updated row;
3. `id DESC` — final tie-break.

Keep rank 1. Carry `group_row_count` and `group_actual_entry_count` forward so the
condition stays visible rather than being silently collapsed:

* more than one row → `MULTIPLE_TASK_ROWS`;
* more than one actual entry → `DUPLICATE_ACTUAL_ENTRY`.

`DISTINCT` would merge genuinely different snapshots. `MAX(id)` alone could pick a
planning snapshot over the actual entry and lose a reported quantity. Summing every row
would double count. Ranking avoids all three.

The dashboard states the arithmetic openly — e.g. *"3 duplicate task rows set aside by
grain resolution (197 raw rows → 194 logical tasks)"* — so the reduction is auditable.

`DUPLICATE_ACTUAL_ENTRY` currently never fires on this data. The handling exists anyway,
because the grain is a property of the data and not a guarantee.

---

## 5. Quantity status

Classified once, in `app/services/validation_service.py`. React never recomputes it and
the LLM is instructed to repeat it verbatim.

| Condition | Status |
|---|---|
| `actual = planned` | `ON_PLAN` |
| `actual > planned` | `ABOVE_PLAN` |
| `actual < planned` | `BELOW_PLAN` |
| `actual IS NULL` | `NO_ACTUAL` |
| `actual` present, `planned IS NULL` | `NOT_VALIDATED` |

**These five are also the dashboard's top-level grouping** — summary → status → WBS →
activity — and the order above is the order they appear in, every day, whatever the data.
It comes from `QuantityStatus` definition order in `app/models/daily.py`; the API emits
sections in it and React does not re-sort them, so an operator always finds Below Plan in
the same place. A status with no tasks has no section; the day totals still report it as a
zero.

Every task carries a status, so — unlike the UOM hierarchy this replaced — nothing can fall
out of the grouping into a "not recorded" bucket at the top level. And because every task
under a status section shares that status, the WBS and activity levels beneath carry a task
count and no status breakdown: there is nothing left to break down.

**`ABOVE_PLAN` is not an error.** It states that the reported actual is above the planned
quantity, nothing more. `planned = 0` with `actual > 0` is likewise `ABOVE_PLAN` and not an
error. No tolerance band is applied, because none is defined — a difference of 0.0001 is
`BELOW_PLAN`, not "close enough". No percentage variance is computed when planned is zero
or missing.

`NOT_VALIDATED` is an addition, and it is deliberate. One row in the table has an actual
quantity but no planned quantity. Treating the missing planned as zero would invent a rule
and would report the task as a false `ABOVE_PLAN`; there is no planned quantity to compare
against, so the condition is exposed as unresolved and flagged `MISSING_PLANNED` instead.

---

## 6. UOM

`ref.uom.uom_code` is displayed as stored. **No conversion between units is implemented**,
because none is defined: Km ↔ M, Joint ↔ Joints and well ↔ Nos are all treated as distinct
units. Consequently:

* quantities are summed **only within a single UOM**;
* a summary group reports `planned_quantity` / `actual_quantity` only when every task in it
  shares one unit. When it spans several, both totals are `null`, `quantities_summable` is
  `false`, and `uom_codes` names the units involved — the UI shows
  *"3 units (Joint, Nos, no) — not totalled"* and the Excel export writes
  *"mixed (…)"* in its UOM column. **A withheld total is not a zero**, and both surfaces say
  so rather than leaving a blank to be misread;
* day-level totals report task and well counts but deliberately carry **no** quantity;
* when an explanation scope spans several UOM, the evidence payload withholds the totals
  and says why.

UOM was the top level of the grouping hierarchy in an earlier version, which guaranteed no
group ever spanned two units. Grouping by validation status instead (§5) means a status
section *can* span units — so the withholding rule above, which the per-well roll-up and the
evidence payload already applied, now runs at every level of the summary. Nothing is
converted to make a total fit, anywhere.

`uom_code` is a `varchar` column holding m² and m³ as single-byte characters. The
connection sets `DB_ANSI_CODEPAGE` (default `cp1252`) so those arrive intact rather than
as replacement characters.

**`activity_uom` — connected as reference evidence, never as a substitute.** `dbo.mapping_master`
also carries a `UOM` column: the unit the activity master expects, as opposed to `uom_code`
(the unit actually recorded on this daily row). Measured against actual-entry rows table-wide,
`uom_code` is missing on 26 of 11,277; all 26 have a `mapping_master.UOM` value available. That
value is surfaced as `activity_uom` — in the API, the well/task detail views, and the Excel
export — precisely because it fills a real evidence gap. It is **never** used to fill in, group
by, or convert a quantity: `uom_code` stays the sole UOM for those purposes, and `MISSING_UOM`
still fires exactly as before when `uom_code` is absent, regardless of whether `activity_uom` is
available. The two are also never compared for a "mismatch" flag: `mapping_master.UOM` values
like `'No'` vs. `ref.uom.uom_code` values like `'Nos'` look like naming-convention variants of
the same abbreviation rather than a real discrepancy, and no rule exists to tell the two apart —
so both values are shown side by side and left for a person to judge, rather than the system
guessing.

---

## 7. Mapping and data quality

The chain `task_code → activity_id → activity_code → description / WBS / crew` is resolved
with `LEFT JOIN` at every hop, so unmapped work stays visible. `mapping_status` reports the
first break in the chain: `MAPPED`, `UNMAPPED_ACTIVITY`, `UNMAPPED_WBS`,
`UNMAPPED_DESCRIPTION` or `UNMAPPED_CREW`. Missing information stays missing — the UI prints
"Not mapped", never a substitute.

Both lookup tables are de-duplicated to one row per key inside the query. Without that, the
handful of `mapping_master` rows sharing an `activity_id` would fan out the daily rows and
inflate every total on the dashboard.

**`dbo.mapping_master` replaced `dbo.activity_master_mapping`** as the first hop — the old table
is superseded and, measured against the actual data, resolves only ~30% of the activity ids seen
in `task_daily`, and on one sample date resolved **0 of 130** tasks to a WBS at all (its
`activity_code` values no longer line up with `activity_master_csv`). `mapping_master` resolves
~46% of activity ids and 93 of the same 130 tasks to a WBS. `mapping_master.Activity_ID` is
stored as `TEXT` and is `CAST` to `nvarchar` before comparing. `mapping_master` also carries its
own `New_Crew_code`, `project_type`-equivalent and similar columns that are **not** used here —
crew stays `activity_master_csv.crew_code` only, per business rule, never backfilled from the
newer table just because it happens to have a similarly-named column.

Data-quality conditions are tracked **separately from the operational numbers** and never
folded into a quantity:

`UNMAPPED_ACTIVITY` · `UNMAPPED_WBS` · `UNMAPPED_DESCRIPTION` · `UNMAPPED_CREW` ·
`MISSING_UOM` · `INVALID_DAILY_JSON` · `UNPARSEABLE_ACTUAL_QUANTITY` ·
`DUPLICATE_ACTUAL_ENTRY` · `MULTIPLE_TASK_ROWS` · `MISSING_PLANNED` · `MALFORMED_TASK_CODE` ·
`INVALID_DATE_SEQUENCE` (§21)

Two further daily-entry checks are computed the same deterministic way but are not
part of this mapping chain — whether the crew actually assigned matches the WBS-planned
crew, and whether the reported dates land on, before or after plan. See §21.

---

## 8. API

| Endpoint | Purpose |
|---|---|
| `GET /api/health` | Database reachability + LLM configuration. No secrets. |
| `GET /api/daily/summary?date=` | Dataset A — grouped Status → WBS → Activity (`status_groups`), plus `view_mode` |
| `GET /api/daily/details?date=` | Dataset B — every logical daily task |
| `GET /api/daily/group-details?date=&status=&wbs=&activity_code=&uom=&refresh=` | The rows behind one figure — called with no filter at all, also the whole day's per-well rollup behind the main dashboard's well list |
| `GET /api/daily/well/{well_id}?date=` | Every task one well ran that day |
| `GET /api/daily/well-activity?date=&well_id=&refresh=` | Per-well task activity as of the date — incomplete / ongoing / reported-today counts and the last task date. With `well_id`, also the incomplete tasks behind that well's counts — see §19 |
| `GET /api/daily/dates?limit=` | Recent dates that carry daily entries |
| `GET /api/daily/export?date=` | `daily_morning_brief_YYYY-MM-DD.xlsx` |
| `POST /api/daily/explain` | Evidence + LLM explanation for a named scope. For `scope="task"`, the evidence and explanation also fold in an advisory crew suggestion when the task qualifies — see §18. |
| `GET /api/daily/milestones?window_days=` | Live wells approaching (or past) a pegging/FLAF/rig-on/rig-off deadline — see §15 |

The date defaults to the current date and is always bound as a parameter. An empty string
for `wbs` or `uom` selects the explicit "not recorded" group, so tasks missing a WBS or a
UOM stay reachable instead of disappearing from the drill-down. `uom` is no longer part of
the summary hierarchy but remains an optional filter, so a single unit stays reachable.

`POST /api/daily/explain` takes a **scope**, not figures. The client says *which* group,
status or well it wants explained; the backend re-derives the evidence itself. The client
cannot supply the numbers to be explained. Its response is served from cache when nothing
about the underlying evidence has changed since the last identical request — see §10.

`refresh=true` on `/summary` or `/group-details` bypasses the day's dataset cache **and**
purges any cached AI explanations for that date, so both endpoints must agree on the same
report date's freshness at once: reloading the well list without also invalidating a
just-explained well's cached text (or the reverse) would leave the two visibly out of step.

---

## 9. Unresolved business questions

Neither rules file defines the following. None of them have been guessed at, and each is
surfaced rather than silently resolved. All but one are `daily_report_rules.md` §7 questions
— specific to classifying a daily task, not to the well-lifecycle rules in `business_rules.md`:

| Question | How the application behaves | Source |
|---|---|---|
| **Quantity tolerance** | None applied. Any difference is classified exactly. | daily_report_rules.md §7 |
| **Is a quantity difference an error?** | No. `ABOVE_PLAN`/`BELOW_PLAN` are descriptive; nothing is called an error. | daily_report_rules.md §7 |
| **UOM conversion** | None. Units are never converted or summed across. | daily_report_rules.md §7 |
| **Meaning of `task_daily.required`** | The column is not read anywhere in this version. | daily_report_rules.md §7 |
| **Unit of `task_daily.progress`** | Reported exactly as stored, never as a percentage. Observed values range from −0.05 to 66.7, so a 0–1 fraction cannot be assumed. The LLM is explicitly forbidden from describing it as a completion percentage. | daily_report_rules.md §7 |
| **Cause of a quantity difference** | Not recorded. Never inferred, and the LLM is told so. | daily_report_rules.md §7 |
| **Accountability / crew performance** | Not recorded. Never assigned. | daily_report_rules.md §7 |
| **`planned IS NULL` with an actual** | Exposed as `NOT_VALIDATED` + `MISSING_PLANNED`. | daily_report_rules.md §4/§7 |
| **Construction actual completion date** | No column is approved as one, so none is substituted. | business_rules.md §10 |

Two further implementation notes, both consequences of what the data contains:

* **`daily_completed` and `ph_name`** are read from the `daily_data` JSON (`$.completed`,
  `$.ph_name`), falling back to the `daily_completed` / `daily_ph_name` columns. Those
  columns are NULL throughout the table, while the JSON carries the values — the JSON is
  the same source the actual quantity comes from.
* **Well count and task count are different measures.** One well can run several tasks in
  the same group on the same day, so `well_count` counts distinct wells while the status
  counts count tasks. Both are labelled as such everywhere they appear.

---

## 10. LLM boundary

The LLM may summarise the supplied evidence, describe which wells are on, above or below
plan, describe missing actual entries, and make the day easier to read.

It must not calculate totals, percentages, dates or variances; create risk scores; decide
business status; invent causes or missing data; assign blame; create business rules;
override a SQL/Python classification; reinterpret a UOM; or assume *why* an actual differs
from a plan. The system instruction states all of this, and the evidence payload carries the
undefined constraints (`quantity_tolerance`, `uom_conversion`, `progress_unit`,
`cause_of_difference`, `responsibility`) alongside the data so the model is told, in band,
what it may not infer.

**A broad scope's example tasks represent the whole scope, not whichever activity sorts
first.** Caught live against real data: a day with 46 wells produced an explanation naming
only 2-3 of them, even though its summary counts already covered all 46. The cause was the
task sample (§ below) being a plain prefix of `daily_detail.sql`'s row order — `(uom_code,
wbs, activity_code, well_id)` — which clusters on whichever activity sorts first, sometimes
just a couple of wells out of dozens in scope; the same raw order also mixed different
`quantity_status` values together with no structure, making it easy for the model's prose to
blend an `ON_PLAN` task's description into a `BELOW_PLAN` one's. `EvidenceService._representative_sample`
fixes both at once: status is the outer axis, so a handful of examples already touches every
status present before any status gets a second well, and well is the inner axis within each
status, so those examples are not all the same one or two wells. Verified against the same
real day afterward: the example tasks named three different wells, one for each of three
different statuses, in one pass. The system instruction was also strengthened to say plainly
that the summary figures cover the whole scope while the task list is only a sample, so a
multi-well scope is never described as if it were about only the wells shown as examples.
Because this changes what an old cached answer *should* say, `LLMService._PROMPT_VERSION` is
mixed into the cache key (§ below) — every answer cached under the old sampling became
unreachable the moment this shipped, with no need to clear the cache file by hand.

**Failure is contained.** If the LLM is unconfigured, unreachable, still rate-limited after
retrying, or returns an unreadable response, the endpoint returns `available: false` with a
plain reason and the evidence, and the panel reads:

> **AI explanation unavailable.** The underlying daily task data is still available and
> unchanged.

No explanation is ever fabricated, and nothing else on the dashboard depends on the LLM.

**Transient provider failures are retried, not surfaced immediately.** Measured against the
live Groq endpoint: a `429` (rate limited) and, intermittently, a `413` both occurred for a
request that succeeded moments later with the identical payload — upstream flakiness, not a
hard, repeatable limit. `LLMService._post_with_retry` retries `408`/`413`/`429`/`5xx` up to
twice more with a short backoff (honouring a provider `Retry-After` header, capped at 3
seconds) before giving up; a non-transient error (e.g. `401`) still fails on the first
attempt. This converts a momentary provider hiccup into a few extra seconds of "Generating an
explanation…" instead of a user-visible failure. A `413` that survives every retry no longer
surfaces as a raw HTTP status either: `LLMService.explain` reports it as "This selection has
too much data for the AI to summarise at once."

**Evidence size is capped per scope, not by one fixed number.** `EvidenceService` used to
include the same 40 sample tasks regardless of what was being explained; a busy day's "day"
scope (hundreds of tasks, just fewer of them shown as examples) was observed failing with a
real, repeatable `413` at that size. The per-task sample is now scope-aware
(`_TASK_EVIDENCE_LIMIT_BY_SCOPE` in `evidence_service.py`): `task` sends the one task, `well`
sends up to 30, and every broader scope (`day` and anything not narrowed to a well or task)
sends 10 — the summary counts already cover every task regardless, so a broad scope never
needed all of them listed individually. `EXPLAIN_MAX_WELLS` remains an operator-configurable
ceiling above all of these.

**The explanation renders next to what it explains, never only at the top of the page.**
Each well row in `WellList.jsx` and each task panel in `WellDetail.jsx` own their own
explain-toggle state and mount `ExplainPanel` inline, next to the well or task it describes.
Only "Explain this view" (day/well aggregate scope, in `DailyMorningBrief.jsx`) uses the
single page-level panel, because that request genuinely describes the whole view rather than
one well or task.

**No model name is shown to the operator.** The health status line says "AI explanation
available" rather than naming a model or provider: which LLM answers the request is an
operational/billing detail, not something the classification depends on, so nothing in the
panel names it either. `GET /api/health` still reports the configured model for anyone who
needs it (ops, `/docs`); the dashboard itself just doesn't surface it.

**Style: flowing prose, not bullets.** The system instruction asks for two to four short
paragraphs, the way a person would explain it out loud, and explicitly forbids markdown
formatting -- headings, bullet lists, tables, bold -- with one deliberate exception: every
literal value copied from the evidence (a well ID, task code, quantity, unit, date or status)
is wrapped in backticks. `MarkdownLite` renders that backtick span in italics, coloured with
the same `--num` teal the rest of the app already uses for a database-sourced figure
(`.explain__value`), so a value taken straight from the record visually separates from the
model's own sentence around it. Models format their answers even when asked not to, so the
renderer still tolerates the occasional stray bullet or heading rather than breaking on it,
and joins any prose the model wraps across several lines back into one paragraph. It never
renders raw HTML the model returns, so model output cannot inject markup into the page.

**Identical evidence is answered from cache, not asked of the LLM again.** Pressing "AI
summary" a second time for the same well, or reopening "Explain this view" for the same day,
when nothing underneath it has changed costs no additional tokens. `LLMService` keys a cache
on the evidence's own content hash (`_evidence_hash` — a SHA-256 of the canonical JSON)
rather than on the request's scope and filters: two requests resolve to the same cached text
only when the figures they describe are byte-identical, so a real change in the underlying
data is never served a stale answer, with no separate "is this still valid" check required.
Only a *successful* explanation is ever cached — a transient provider failure is retried on
the next identical request, never remembered as permanent. The browser keeps a second,
much shorter-lived cache in front of this one (§22), which follows the same rule for the
same reason. `ExplainResponse.cached` reports
which happened; the panel shows it as a colour, not a word, and **only the reused case is
coloured**: green (`.explain--cached`, tinted with the same `--on-plan` token used everywhere
else in the app) for an earlier answer reused, and the panel's ordinary styling for one just
generated. An earlier version had this the other way round and in the wrong palette — green
for fresh, red (`--danger`) for cached — which read as *something is wrong with this answer*
about the most ordinary thing that can happen, and spent the one colour this dashboard
reserves for failures on a success. Generating an explanation is not a status worth flagging;
reusing one, which is what says nothing underneath it has changed, is. A "cached" text label
was tried first and judged to be one more thing to read on a screen meant to be skimmed; a
colour already carries meaning throughout this dashboard (status pills, day-strip figures),
so this reuses that convention instead of introducing a new kind of label. Neither tone is
shown while loading or when the explanation is unavailable — there is nothing generated or
reused yet to signal.

**A first generation could also *report* itself as reused — the second half of the same bug.**
`ExplainResponse.cached` was always propagated correctly (backend → response → panel state), so
the colour was telling the truth about the response it was given; the problem was which
response the panel ended up holding. React's development StrictMode mounts a component twice,
so opening a panel fired the request twice: the first was aborted by the effect's cleanup,
the second arrived after the backend had already generated and cached the answer — and was
correctly marked `cached: true`. The very first time an operator opened a well's summary, the
panel therefore said "reused". `ExplainPanel` now holds one in-flight promise per identical
request and shares it, so a remount joins the request already running instead of starting a
second one, and the response shown is the one actually generated for it (verified live: one
`POST /api/daily/explain` per open, fresh on the first, green on the reopen). This is the
client-side counterpart of the backend's per-key lock below, which the lock cannot provide
from its side: it stops two requests from both reaching the LLM, but it cannot stop the second
one from being *answered* — correctly — as a cache hit. Aborting was also dropped along the
way: it never stopped the backend finishing and caching the work anyway, so it only ever
changed what the client was told about it.

**An open explanation always describes one specific date; changing the date closes it.**
Reworded from "should the operator have to notice and close it themselves" to "no": the
top-level "Explain this view" panel closes itself the moment `reportDate` changes (a
`useEffect` in `DailyMorningBrief.jsx`, the same way the drill-down path already resets), and
every well row's own inline panel closes with it — `WellList.jsx` keys its row list on
`reportDate`, so a date change unmounts and recreates every row, discarding each one's local
"is my AI summary open" state rather than leaving it sitting open and describing a date the
operator has since navigated away from. A well's own detail page closes the same way already,
for the same underlying reason: `selectDate` resets the drill-down path, which unmounts it.

**The cache lasts one run, in memory only.** Within a run, asking again for the same
well, group or day costs no tokens. Nothing is written to disk: stopping the application
drops every cached explanation, so the next run writes its summaries afresh rather than
replaying text generated by an earlier one. (An earlier version persisted the cache to
`backend/.cache/explain_cache.json` so it survived restarts; that was removed because a
summary belongs to the run that produced it.)

A "Refresh" additionally purges every cached explanation for that report date outright
(`LLMService.invalidate_date`, called from both `/api/daily/summary` and `/api/daily/group-details`
whenever `refresh=true`), so a deliberate refresh always gets a fresh explanation on the next
request even in the rare case where the reloaded data comes back byte-identical to before —
correctness here does not depend on guessing whether a refresh actually changed anything.

Two requests racing on the exact same *not-yet-cached* evidence — two browser tabs open on
the same well, or (as also caught live while verifying this) React's development-mode
double-effect firing a request twice for one click — do not both reach the LLM either: the
first to arrive holds a per-key lock (`_ExplainCache.key_lock`) for the duration of the call,
so the second waits, then finds the answer already cached rather than generating its own,
differently-worded one at a non-deterministic temperature. `EXPLAIN_CACHE_MAX_ENTRIES`
(default 500) bounds memory with oldest-first eviction across all dates combined; it is a
memory limit only; content-hashing is what already guarantees a stale answer is never served,
independent of this number.

**The provider and model are configuration, never code.** `LLM_PROVIDER` (`openai` or
`groq` — both speak the same chat-completions protocol) and `LLM_MODEL` in `backend/.env`
decide which service actually answers; `LLMService` reads them fresh from settings on every
call rather than hardcoding either. The default configuration is `openai` /
`gpt-4o-mini`; switching providers or models is a `.env` edit, never a code change.

### Token-expense log — deliberately independent of this application

Every real call to the LLM (never a cached one — see above) is also recorded by
`llm_usage_tracker.py`, a small script at the **project root**, outside both `backend/` and
`frontend/`. It appends one row to `llm_usage_log.xlsx` (created on first use, also at the
project root): timestamp, provider, model, how long the call took, its input/output token
counts, and — when the provider reports it — how much of the input was served from the
provider's own prompt cache (OpenAI's `usage.prompt_tokens_details.cached_tokens`, typically
billed at a discount; blank, not zero, when a provider doesn't report this breakdown at all).

**A live totals row always sits directly beneath the data**, never appended after it: its
first cell reads `TOTAL calls: N` (a real `COUNTA` formula, not a number typed in once), and
every numeric column carries a real `SUM` formula over exactly the data rows above it —
Duration, Input Tokens, Output Tokens, Input Cache Tokens. A new row is *inserted* directly
above the existing totals row, which is what keeps the totals row pinned to the bottom and
its SUM ranges correct as the log grows: `sheet.insert_rows()` at the totals row's own
position pushes it down by one, the new data goes where it was, and the totals row is then
rewritten one row lower with its ranges extended to include the new row. Opening the file in
Excel always shows a correct running total, with no manual work and no stale formula left
behind by an earlier version of the row count.

That file is deliberately **independent of the rest of this project**: it imports nothing
from `backend/` or `frontend/`, and the *only* file it reads is `backend/.env` (its own tiny
parser, not the backend's settings module), from which it takes the current provider and
model. Everything else it records — token counts, duration — is supplied as plain arguments
by whichever code just made a real call, since those are facts about one specific request
that no file could know in advance. `LLMService.explain` loads it by file path (not a normal
package import, to keep the dependency one-directional: the app reaches out to the tracker,
the tracker never reaches back in) and calls it once, right after a successful response,
never for a cache hit. A missing or broken tracker file disables logging, quietly, without
affecting a single explanation — this is expense bookkeeping, not a feature the dashboard
depends on.

**A real, fixed leak, caught while wiring this in.** The first end-to-end test showed the
log gaining three rows with `Duration (s)` of `0` and blank token counts before a single real
row — the test suite itself was writing to the *real* project-root log file, because
`llm_service.py`'s usage-tracker hook fires after any successful response, mocked or not, and
one existing test (`test_evidence_carries_classifications_never_raw_sql`) posted to
`/api/daily/explain` through the shared `LLMService` singleton without mocking `.explain()` —
meaning it could also have reached the real, now-genuinely-configured OpenAI endpoint and
spent real tokens on every test run, silently. Fixed two ways: that one test now stubs
`.explain()`, consistent with this suite's own stated principle of running entirely offline
(`backend/tests/conftest.py`), and a new autouse fixture (`_isolate_llm_side_effects`)
redirects the shared `LLMService` singleton's cache file to a throwaway path and disables
usage logging for every test, so no future test — mocked or not — can leave a trace in either
real project file again.

**What the per-call log cannot see — and the authoritative figure that can.** That row-per-call
log only ever records calls made *through this running application*. It never saw a call made
by the test suite (which disables the tracker on purpose, above, so a test run cannot land in a
real expense file), and it can never see anything else spending tokens on the same API key —
another tool, another machine, a colleague. Those are real money, and for a while they were
simply invisible: the workbook read as the whole bill when it was only the part this app
happened to be running for.

So the workbook now also carries what the provider itself reports, which is the figure that
actually gets billed:

```bash
python llm_usage_tracker.py sync            # last 30 days (OPENAI_USAGE_DAYS)
python llm_usage_tracker.py sync --days 60
```

| Sheet | What it is | Where it comes from |
|---|---|---|
| **LLM Usage** | one row per real call this app made, with how long it took | this application, as each call completes |
| **OpenAI Usage** | tokens and requests per day and per model | `GET /v1/organization/usage/completions` |
| **Reconciliation** | the two side by side per day, with the difference and the day's cost | both, plus `GET /v1/organization/costs` |

The Reconciliation sheet is the point of it: a `Requests not logged` column that states, per
day, how much usage was billed that never reached the per-call sheet. On a day of development
that number is large and completely expected — it is the test runs and the verification calls
— and it is stated as a fact rather than left to be inferred from a total that looked too
small.

**It needs an admin key, and deliberately not the application's own.** `OPENAI_ADMIN_KEY` in
`backend/.env` (Organization → Admin keys, `sk-admin-…`) is a *different* key from `LLM_API_KEY`:
organisation usage and cost are admin-scoped endpoints that refuse an ordinary project key, and
keeping them apart means the key that can read the whole organisation's spend is not the one
travelling in a request header on every explanation. With no admin key configured the sync does
nothing except explain, in full, what to set and where — it never guesses, never partially
writes, and never falls back to estimating from the per-call rows. `OPENAI_USAGE_DAYS`,
`OPENAI_USAGE_PROJECT_IDS` and `OPENAI_USAGE_BASE_URL` are the remaining knobs; nothing is
hardcoded.

Three properties worth stating, because each one is a way this could have quietly lied:

* **the sync rewrites, it never appends** — re-syncing an overlapping window replaces those
  days rather than double-counting them, while the per-call sheet stays append-only and is
  never touched by a sync;
* **every page is followed** — the API returns time buckets a page at a time, and stopping at
  the first page would under-report a long window, which is exactly the failure this feature
  exists to remove;
* **a day with no reported cost is left blank, not zero** — the two mean different things, the
  same rule this project applies to a withheld quantity total everywhere else.

The key is never printed, never logged and never written to the workbook; a `401`/`403` is
reported as "that key was refused, and here is the kind of key this needs" with no token in the
message, and `SecretRedactingFilter` (§11) already matches the `sk-admin-…` shape should one
ever reach a log record by accident. The tracker stays otherwise independent: the sync is the
one network call it makes, it happens only when explicitly invoked, and it still imports
nothing from `backend/` or `frontend/` — plain `urllib`, no new dependency.

---

## 11. Read-only guarantee

This feature only reads. The guarantee is enforced in three places:

1. connections are opened `readonly=True` with autocommit, so no transaction is left open;
2. every statement passes `assert_read_only()`, which requires a `SELECT`/`WITH` statement
   and rejects `INSERT`, `UPDATE`, `DELETE`, `MERGE`, `ALTER`, `DROP`, `TRUNCATE`,
   `CREATE`, `EXEC`, `GRANT`, `BACKUP` and stacked statements — while correctly ignoring
   those words inside comments and string literals;
3. tests assert both that the shipped queries pass the guard and that each write statement
   is rejected.

The Excel export is assembled in memory and the LLM never touches the database, so neither
writes back.

**Logged secrets are redacted, and the redaction is tested against the actual key shape in
use.** No credential is deliberately logged anywhere in this application; `SecretRedactingFilter`
(`app/utils/logging_config.py`) is a second line of defence in case one reaches a log record
by accident. An earlier version of its patterns only matched a hyphen-separated key
(`gsk-...`/`sk-...`) and only redacted as far as the word `Bearer` in an `Authorization: Bearer
<token>` header, leaving the actual token — the shape this app's own Groq key is in
(`gsk_...`, underscore) and the shape its own `httpx` client sends — unredacted in either
case. `tests/test_logging_redaction.py` locks in the fix: every credential shape this app can
actually produce (env-style assignment, a bare key, an `Authorization` header as a raw string
or a quoted dict repr, connection-string `PWD=`/`UID=`) is asserted redacted, and a handful of
ordinary operational log lines (`sql.daily_detail executed in …`, `req … -> 200 OK …`) are
asserted untouched, so the filter cannot silently regress into either failure mode again.

**No authentication is implemented at the application layer.** Every endpoint answers
without a login or an API key: this is a read-only reporting layer, and the assumption is
that network placement (an internal segment, a VPN, a reverse proxy in front) is what
restricts who can reach it. Nothing here should be exposed directly to an untrusted network
without such a layer in front of it.

---

## 12. Performance

* One day costs **two queries**, both filtered in SQL by report date and live wells, plus
  **one** for the front page's per-well task activity (§19) and **one** more only if a well's
  task list is actually expanded — that one covers every well at once, not the expanded one.
* The resolved day is held in a short TTL cache (`DAILY_CACHE_TTL_SECONDS`), so every
  drill-down level — group, status, well, task — is served from it; the task-activity
  evidence is cached the same way and for the same reason. There is **no query per
  well**, anywhere; tests assert this for both.
* Quantities cross the wire as exact decimal strings, so no float rounding is introduced
  between SQL Server and the browser.
* Aborted `fetch` requests mean a rapid date change can never leave stale data on screen.

**Recommended index — not applied.** The schema is never altered by this application. A
day's query currently scans `well.task_daily` (≈110k rows, ~1–1.5 s). If that becomes a
problem, the DBA may consider:

```sql
CREATE NONCLUSTERED INDEX IX_task_daily_ActionOn
    ON well.task_daily (ActionOn)
    INCLUDE (schedule_id, task_code, well_id, planned, progress, uom_id, crew_id,
             crew_type_id, daily_data, updated_at);
```

`well_id` is left out of the index key because it is now `varchar` and every query reads it
through `TRY_CONVERT(int, td.well_id)` (see §3) — a plain index on the raw column would not be
used by that expression. A computed, persisted column such as
`well_id_num AS TRY_CONVERT(int, well_id) PERSISTED`, indexed in its place, would let the
optimizer seek on it instead of scanning; that is a schema change and is left for the DBA to
evaluate, not applied here.

This is a recommendation for review, not a change to run automatically.

---

## 13. Configuration

Nothing environment-specific is hardcoded. Server, database, credentials, driver, ports,
LLM provider/key/model/endpoint, the grouped-vs-detail threshold, the cache TTL, the evidence
size limit, the explanation cache size and its persisted file location all come from
`backend/.env` (template: `backend/.env.example`). `llm_usage_tracker.py` (project root) is
the one exception worth calling out explicitly: it reads `backend/.env` too, for the current
provider and model, but through its own tiny parser rather than this settings module — see
§10.

The React app receives **no** backend secret: it calls a relative `/api` path through the
dev proxy, or `VITE_API_BASE_URL` in production.

Credentials are never logged. Beyond simply not logging them, a redaction filter on the
logging handler scrubs anything resembling a password, connection string or API key, and
database errors are logged by SQLSTATE rather than by driver message.

`USE_MOCK_DATA` defaults to `false` and must stay there. There is no fake data anywhere in
this application: when the database is unreachable the dashboard shows a database-error
state, and when a date genuinely has no records it shows an empty state. The two are
visibly different, because they mean different things to an operator.

---

## 14. Verified behaviour

Checked against the live `AlTasnimBI` database while building:

* **2026-08-01** — 197 raw rows resolved to 194 logical tasks across 67 live wells;
  143 `ON_PLAN`, 17 `BELOW_PLAN`, 5 `ABOVE_PLAN`, 29 `NO_ACTUAL`; 13 tasks unmapped.
  Grouped dataset, with the data-quality banner reporting the 3 superseded rows separately.
* **Traceability** — a card reading "13 tasks" under On Plan drills to exactly 13 tasks
  across 7 wells. A test asserts this for *every* count at *every* level — status, work
  category and activity — not just one.
* **Status grouping on 2026-08-01** — 224 tasks in 4 sections (170 On Plan, 6 Above Plan,
  17 Below Plan, 31 No Actual); Not Validated had none, so it had no section while the day
  totals still showed its zero. The On Plan section spans 10 units of measure, so its own
  totals are withheld and read *"10 units (Joint, Ls, M, +7) — not totalled"*, while every
  work category under it stays within one unit and reports its totals normally.
* **2026-09-11** — a single task, so `view_mode` came back `"detail"` (threshold 25) and
  `SummaryResponse.tasks` carried that one task directly.
* **Multi-dash task codes** — `FLME1165-10239-T014` resolves to activity `FLME1165`.
* **LLM down** — with an unreachable endpoint, the dashboard rendered in full and the panel
  reported the explanation as unavailable, with no fabricated text.
* **Excel** — a 3-sheet workbook (Daily Detail, Summary, Data Quality) named
  `daily_morning_brief_2026-08-01.xlsx`.
* **Schema drift caught and fixed** — `well.task_daily.well_id` drifted from `int` to `varchar`
  with 215 non-numeric rows (e.g. `'0000F'`); a bare comparison against it crashed every report
  with `SQLSTATE 22018`. Fixed with `TRY_CONVERT`, guarded by a test.
  `dbo.activity_master_mapping` was superseded by `dbo.mapping_master`: WBS resolution on a
  sample date went from 0/130 tasks to 93/130 after the swap.
* **`activity_uom` connection** — 26 of 11,277 actual-entry rows table-wide have no `uom_code`
  of their own; all 26 have a `mapping_master.UOM` reference value, now surfaced in the API,
  the well/task detail views and the Excel export without touching `MISSING_UOM` or grouping.
* **Theme toggle** — dark (default, unchanged look) and light, persisted per browser via
  `localStorage`, switching a `data-theme` attribute that every colour in the stylesheet is
  already expressed as a variable against.
* **AI explanation style** — the system instruction and renderer were changed from short
  bullet points to two-to-four-paragraph prose; verified by re-running the explain endpoint
  against a live evidence payload.
* **Crew personnel resolution** — `task_daily.crew_id`/`.crew_type_id` matched `ref.crew`/
  `ref.crew_type` for 100% of the rows that carried them (1,787/1,787 and 2,839/2,839 measured
  over 30 days of `well.task_daily`), giving a real supervisor and roster per task rather than
  just the WBS crew code.
* **Well milestones on 2026-09-14** — 1,216 outstanding (not-yet-reached) milestones across
  live wells; 42 within the 7-day priority window, 292 already overdue. A well already past
  `eng_completion_date` never appears in either list.
* **LLM retry recovers a real, reproducible failure** — a busy day's "Explain this view" (116
  tasks) was observed failing consistently with `HTTP 413` from Groq on the first attempt;
  with the retry-with-backoff added to `LLMService`, the same request succeeded on a
  subsequent attempt without any user-visible error.
* **A real, repeatable `413` on the day scope, fixed at the source** — a later live report
  (2026-08-01, 224 tasks, day scope, "all daily work") failed with `HTTP 413` even after every
  retry: the fixed 40-task evidence sample was too large for that scope regardless of
  attempts. Capping the sample per scope (`day` → 10 tasks, well → 30, task → 1) resolved it;
  re-run against the same live day, the day-scope explanation succeeded on the first attempt.
* **Well-first dashboard redesign, verified live** — the front page was changed from a
  status→WBS→activity drill-down to one row per well (`WellList.jsx`). Checked against
  2026-08-01 (224 tasks, 79 wells): the well list, sort order (busiest first), inline "AI
  summary" per row, and drill-through to `WellDetail` all matched the backend's per-well
  rollup exactly; a `validateDOMNesting` warning caught during this check (a status pill
  `<button>` nested inside the row's own `<button>`) was fixed by making the row container a
  `role="button"` `<div>` instead, confirmed absent afterward at both desktop and 375px mobile
  width, in both themes.
* **A real cache-stampede race, caught live and fixed** — verifying the explanation cache by
  hand in a browser, the very first "AI summary" click for a well returned text marked
  `cached: true` that did not match what a moment's re-read of the panel showed moments
  earlier. React's development-mode double-effect had fired two near-simultaneous requests
  for the same not-yet-cached well; both missed the empty cache and both called the LLM
  (visible as two `LLM explanation generated` log lines for one click), and whichever finished
  last overwrote the cache — a real token-doubling bug, not a display glitch. Fixed with a
  per-(date, evidence-hash) lock around the "still a miss? then call the LLM" step
  (`_ExplainCache.key_lock`): re-run against the same well afterward, exactly one
  `LLM explanation generated` line appeared per click, confirmed at the server log, and a
  concurrency test (two threads deliberately raced against a blocking fake `explain()`) fails
  without the lock and passes with it.
* **The well list did not actually refresh** — a gap introduced by the well-first redesign
  (§ below): the main dashboard's well list depended only on the report date, not on the
  "Refresh" button's token, so clicking Refresh updated the day totals and milestones but left
  the well list itself showing the previous load. Fixed by threading the same refresh signal
  through it and adding `refresh` support to `/api/daily/group-details`; confirmed via the
  network log that a Refresh click now issues `group-details?...&refresh=true`, matching the
  `summary` call it already accompanied.
* **Provider switched from Groq to OpenAI, live** — `backend/.env` changed to `LLM_PROVIDER=openai`,
  `LLM_MODEL=gpt-4o-mini`, no code change required. Confirmed at `/api/health` and the
  startup log (`'llm_provider': 'openai', 'llm_model': 'gpt-4o-mini'`), and with a real
  "AI summary" click against the live evidence for well 30365, which returned a genuine
  gpt-4o-mini explanation with the backtick-highlighted values still rendering correctly.
* **The explanation cache surviving a restart, proven across three real restarts** — before
  persistence, only the *second* request within one running process was ever served from
  cache; a restart (the dev server's own auto-reload, or a plain re-run) reset it to zero.
  Stopped and restarted the dev server three times in a row after explaining well 30365 once:
  each restart's log showed `Explanation cache warmed from …: 1 date(s), 1 entry`, and
  `POST /api/daily/explain` for that well returned `"cached": true` immediately every time —
  the very first request after each restart, not the second. `llm_usage_log.xlsx` gained
  exactly one row for the whole exercise (the one real call, before the first restart), never
  three, confirming zero additional LLM spend across all three restarts.
* **A real token-expense leak in the test suite, caught and fixed while wiring in usage
  logging** — the first test run after adding `llm_usage_tracker.py` produced three rows in
  the real project-root `llm_usage_log.xlsx` with `Duration (s)` of `0` and blank token
  counts, before the one genuine row. Traced to one existing test posting to
  `/api/daily/explain` through the real, shared `LLMService` singleton without mocking
  `.explain()` — meaning it could reach the real, now-genuinely-configured OpenAI endpoint and
  spend real tokens on every test run. Fixed by stubbing that one test's `.explain()` call
  (it only ever asserted on the evidence, never the generated text) and adding an autouse
  fixture that redirects the shared singleton's cache file to a throwaway path and disables
  usage logging for every test; re-ran the full suite afterward and confirmed
  `llm_usage_log.xlsx` did not exist at all once the run finished.
* **The usage log's live totals row, migration, and "never raises" contract** — verified
  against the actual project-root file with a genuinely old row already in it (no
  `Input Cache Tokens` column, no totals row): calling `log_usage` once migrated that row in
  place (the new column blank for it, since that data was never captured), added the new
  row, and appended a totals row covering both. A second real call (a different well, through
  the live app) grew the totals row's ranges from `A2:A2` to `A2:A3` correctly, with the new
  row inserted *above* the totals row rather than after it. `test_llm_usage_tracker.py`
  (12 tests, run standalone, isolated to a `tmp_path` file in every case) locks in the
  migration, the range growth, and that a write to an unwritable location returns `False`
  rather than raising — the tracker's whole contract is that a bookkeeping failure can never
  take an explanation down with it.
* **A day's example tasks representing the whole day, not the first well in SQL order** —
  reported live: a day with 46 wells kept reading as if it were about one well. Reproduced
  against the real day (2026-07-30, 75 wells, 270 tasks): the old sampling's example tasks
  named only wells that happened to sort first by `(uom_code, wbs, activity_code, well_id)`.
  After `EvidenceService._representative_sample`, the same day's examples named three
  different wells — 36273, 30365, 32517 — one for each of `ON_PLAN`, `BELOW_PLAN` and
  `ABOVE_PLAN` respectively, in a single explanation. 8 new tests
  (`test_evidence_sampling.py`) assert status diversity, well diversity, determinism, and that
  a well with many tasks never crowds out every other well from a fixed-size sample.
* **Colour instead of a "cached" word, and the panel closing itself on a date change** — both
  verified live. A fresh call showed `class="explain explain--fresh"` with a computed
  background of `rgba(26, 138, 61, 0.1)` (the `--on-plan` green); closing and reopening the
  same well's summary showed `explain--cached` with `rgba(196, 41, 26, 0.09)` (`--danger`
  red) instead, with no code-visible text label either time. Then, with a well's summary open,
  changing the report date was confirmed (via `find`, which returned no match for "Hide AI
  summary" afterward) to close it — both the top-level panel and every open per-well row.

---

## 15. Crew personnel (task → crew → supervisor → employees)

`activity_master_csv.crew_code` (daily_report_rules.md §2) stays the sole, authoritative WBS
crew label for a task and is never touched by this feature. Separately, `task_daily.crew_id`
and `.crew_type_id` identify the *specific crew instance* that actually ran the task, and
resolve cleanly (measured: 100% match rate where present) to:

```
task_daily.crew_id  → ref.crew.crew_id
                         ├─ ref.crew.supervisor_id → ref.employee.id   (the crew's supervisor)
                         └─ bridge.crew_employee.crew_id → ref.employee.id  (the crew's roster)
task_daily.crew_type_id → ref.crew_type.crew_type_id  (the crew's discipline/type name)
```

This is resolved once, in `crew_personnel` (a CTE in `sql/daily_tasks.sql`, joined by
`crew_id` — a scalar join, so it never fans out the task grain), and carried on every
`TaskOut` as `crew_type_name`, `crew_type_code`, `crew_instance_code`, `crew_supervisor` and
`crew_employees`. It is additive evidence, exactly like `activity_uom` in §6: shown alongside
`crew_code`, never in place of it, and absent (not guessed at) whenever a task carries no
`crew_id` or the crew instance has no supervisor/roster on file. The UI renders it as a chain
— Well → WBS → Activity → Crew type → Supervisor → Employees — on both the task card and well
detail views.

`crew_type_code` is the one field in this CTE that is *also* used for something beyond
display: it is compared against `crew_code` to produce `crew_match_status` — see §21.

The well detail page shows `crew_id` itself as well — as its own field
(`Crew ID (assigned)`) and again in the crew chain — because a crew type name does not
identify one crew, and the id is what an operator needs in order to find them (§22.4).

## 16. Well lifecycle priority window

A live well (`eng_completion_date IS NULL`) that is approaching, or has already missed, one
of the four dated milestones in business_rules.md §2/§3 — pegging sheet, FLAF, rig-on,
rig-off — is surfaced as a priority alert, independent of whichever report date the Daily
Morning Brief is currently showing (it is evaluated against today).

`sql/well_milestones.sql` is raw evidence only: one row per milestone that has not yet been
reached (its actual-date column is still `NULL`) and whose deadline is computable (its source
date is not `NULL`). It decides nothing about what counts as "near". That threshold —
`MILESTONE_PRIORITY_WINDOW_DAYS`, default 7 — is owned by `app/services/milestone_service.py`
exactly the way `DETAIL_VIEW_TASK_THRESHOLD` is: business_rules.md defines the deadlines
themselves but not how many days of advance notice counts as a priority, so it stays a
configurable presentation setting rather than an invented business rule.

Outstanding milestones split into two lists, never merged:

* **`upcoming`** — `0 <= days_remaining <= window_days`, soonest first. This is the
  "priority window" a well enters as it nears a deadline.
* **`overdue`** — `days_remaining < 0`, most-recently-missed first, capped at
  `MILESTONE_OVERDUE_DISPLAY_LIMIT` (default 20) with the true count in `overdue_count`.

They are kept apart because business_rules.md §3 already treats a missed deadline as a
standing condition, and on this data the overdue backlog runs into the hundreds (292 measured
on 2026-09-14) against a much smaller upcoming list (42 within 7 days) — folding them together
would bury the wells that are still approaching their deadline.

**The main dashboard never shows the list itself, only two numbers.** An early version put
the full `upcoming` list directly on the main dashboard as an expandable banner; with 40+
rows it made the first screen feel cluttered. A later version replaced that with a single
compact clickable banner row. Both numbers now live in `DayStrip.jsx`, the same one-line strip
that reports the day's task count — "N tasks today · M upcoming milestones · K overdue" —
so the day's headline figures and the well-lifecycle figures read as one summary instead of
two separate widgets stacked on top of each other. Selecting either milestone figure drills
into `MilestonesPage`, a dedicated level in the same `current.type` drill-down state that
`DailyMorningBrief.jsx` already uses for well/task, reached via the normal breadcrumb/back
navigation. That page lists `upcoming` openly and `overdue` behind a collapsed toggle;
clicking any alert expands the well's full set of lifecycle dates in place.

A milestone alert used to also offer a "View well's daily tasks →" link out of that expanded
detail. It was removed: a milestone is evaluated against **today**, independent of whatever
report date the Daily Morning Brief happens to be showing (§ above), so that link opened the
well's daily-task detail for the *currently selected* report date — which, for the wells this
list surfaces, very often has no daily task rows at all, surfacing as a 404/error instead of
the "in place" detail it was meant to be. Rather than resolve that mismatch by guessing at a
more appropriate date, the link (and the `onSelectWell` prop it depended on, in
`MilestonesPage.jsx`/`MilestoneRow`) was simply removed; the expanded lifecycle-date detail
already shown in place is what this page is for, and a well's own daily tasks are always one
click away from the main dashboard for whichever date the operator actually wants.

Milestone labels never repeat the PDO/Al Tasnim scope split that governs which organisation is
responsible for a deadline (`business_rules.md` §2/§3): `MILESTONE_LABELS`
(`app/models/wells.py`) reads "Pegging sheet", "FLAF", "Rig-on", "Rig-off" — plainly, with no
organisation name attached. That split is an internal accountability rule, not something an
operator reading a deadline list needs restated on every row.

The underlying `well.well_master` scan is cached with the same TTL discipline as the daily
dataset (`DAILY_CACHE_TTL_SECONDS`), so repeated banner/page loads do not repeat the full scan.

---

## 17. Presentation conventions

Rules that apply everywhere a well's or a task's figures are shown, not just in one
component:

* **The front page stays a table of main points; full detail lives one click away.**
  `WellList.jsx` shows one row per well — its busiest activity (plus how many others), task
  count, and only the non-zero status counts — never the tasks themselves. Nothing is hidden
  by this: every count on the row is the same figure the backend returns for that well, and
  the well id opens every one of that well's tasks on `WellDetail.jsx`. A well with
  many tasks is not a special case; every well works the same way, so there is no separate
  threshold to configure or reason about here (contrast `DETAIL_VIEW_TASK_THRESHOLD`, §12/§13,
  which is a backend decision about a different question — grouped vs. individual dataset
  shape — and is independent of this).
* **A table, because every well is measured by the same figures.** Eight columns an operator
  can read straight down is what makes two wells comparable at a glance; a list of cards is
  not. Each well is its own `<tbody>`, which is what keeps everything it expands inside its
  own row group. Below 700px the table collapses to one card per well and each figure's
  label reappears beside it, because the column headers are off-screen at that width.
* **A figure on the front page expands where it stands; it never becomes a page.** A well
  row's task-activity figures (§19) each open the tasks behind them *inside that same well's
  row group*, with the well named in the heading of what opens, so an expanded list can never
  be read as belonging to the row above or below it. This is the same principle as the inline
  explanation panel: the detail appears next to the number it explains, and the operator
  never loses their place in the list. It is also why the counts are aggregated in SQL rather
  than shipped as rows — the front page shows 1,468 incomplete tasks as one figure per well,
  and fetches the individual tasks only for the one well actually expanded.
* **Planned, Actual and Progress are always grouped as one unit.** `QuantityTrio`
  (`frontend/src/components/common/index.jsx`) renders the three together in a single boxed
  row; `WellDetail.jsx`'s per-task panel uses it instead of listing the three as separate rows
  among unrelated fields.
* **A numeric value is always coloured distinctly from labels and prose**, via the shared
  `.num` CSS class / `Num` component (`--num` CSS variable — teal, both themes). This is
  deliberately separate from the existing semantic status colours (on-plan green, below-plan
  amber, above-plan blue, danger red, not-validated purple, and the milestone
  upcoming/overdue amber/red): a plain count or quantity gets the generic numeric colour, and
  a value that already carries a status/urgency meaning keeps that colour instead — the status
  pill on a well row, for instance, keeps its status colour rather than switching to teal.

---

## 18. Crew suggestion in task AI summary

An **extension of the existing task-specific AI summary — not a separate feature.** There is
no "Suggest Crew" button, no crew-suggestion page or modal, and no second API call. When an
operator opens the same "Explain this task" panel they already use today
(`WellDetail.jsx` → `TaskDetailPanel` → `ExplainPanel`, `POST /api/daily/explain` with
`scope="task"`), the returned explanation naturally folds in a crew suggestion whenever the
selected task qualifies for one. Full rule set: `backend/daily_report_rules.md`, "Crew
suggestion" section.

**Where it appears.** Inside the same evidence payload and the same LLM call the task summary
already makes. `app/api/routes_explain.py` calls `CrewSuggestionService.build()` only when the
request resolves to exactly one task (`scope == "task"` and exactly one task matched), merges
the result under `evidence["crew_suggestion"]`, and hands the combined evidence to the same
`LLMService` / `gpt-4o-mini` this application already uses for every explanation. Nothing about
the frontend changed to make this appear — the same request the panel already sends is enough.

**Its crew ids are also shown outside the prose.** The panel renders the same
`crew_suggestion` evidence as a small deterministic card beside the model's text: the
suggested (or worth-consulting) crew's id, the crew currently recorded on the task and its
id, and the supporting history — see §22.4 for why the id specifically, and what the model
is now required to include in its own wording.

**Why it exists.** A task can sit open far longer than the historical pattern for its activity
while the crew currently on it is simply busy, unrecorded, or otherwise not making progress.
This surfaces a data-backed, advisory alternative at the exact moment an operator is already
asking "what's going on with this task?", instead of requiring a second tool or a manual query.

**Task-level historical evidence, and why an incomplete historical well still counts.** A crew
is credited with proving an activity by completing that specific task
(`task_daily.completed = 1`) — the historical well it did that work on does **not** need to be
complete overall. A crew that finished its wiring task on an otherwise-still-open well has
still demonstrably completed that activity; requiring the whole historical well to be finished
first would throw away most of the real evidence this system has. `sql/crew_suggestion.sql`
never filters historical rows by `well_master.eng_completion_date`.

**Progress suppression — and a deliberate exception for it.** A task already showing recorded
progress, or already marked `completed`, is never offered a *replacement* suggestion —
`crew_suggestion_eligible` is decided in SQL, never by the model. `progress` is used only as a
`> 0` signal here, exactly as everywhere else in this application (§9): never rendered as a
percentage.

A **completed** task gets nothing further; the summary simply continues describing it normally,
with no crew mention at all. A task that is merely **in progress**, however, still gets the
same ranked historical crew attached when one exists — surfaced as `consult_crew` rather than
`suggested_crew` — so the AI summary can say, in effect, "the task is going smoothly, so
another crew is not necessary right now, but if any feedback or information is ever needed
about this activity, the crew that previously completed similar work could be worth asking."
This exists specifically so the feature stays visible even on a task that needs no change,
rather than going silent every single time nothing is wrong. `suggested_crew` and
`consult_crew` are mutually exclusive and use deliberately different closing language in the
system instruction, so a stalled task is never described as "going smoothly" and a healthy one
is never described as needing "an alternative crew." When an in-progress task has no historical
crew to point to at all, the summary stays silent, exactly like a completed task.

**Derived availability — a V1 signal, not a workforce status.** There is no availability table
in this system. A crew is excluded as a candidate only when its own latest logical task state
(the same `PARTITION BY well_id, schedule_id, task_code ORDER BY ActionOn DESC, updated_at DESC,
id DESC` grain §4 already uses everywhere) shows an unfinished task on a still-incomplete well.
`NO_CURRENT_UNFINISHED_TASK` is reported exactly that way — never as "available" — and
`ref.employee.emp_status` (an employee attribute, not a crew one) is never read by this feature.
Availability is a hard filter applied before ranking, never a ranking score.

**Historical date awareness.** Every historical fact — completion counts, durations, the most
recent success date — is limited to `ActionOn <= report_date`. Opening an old, still-pending
task from months ago is explained using only what the record already showed by that date; a
crew's later, real success is never used to suggest it could have been picked earlier than the
evidence at the time actually supported.

**Advisory only.** Nothing in this feature writes to the database. `sql/crew_suggestion.sql` is
a single `SELECT`, validated by the same `assert_read_only` guard as every other query in this
project, and `CrewRepository` exposes no write path. A suggested crew is never described as an
assignment or a reassignment — the system instruction states this explicitly, and, because a
prompt rule alone cannot *guarantee* a non-deterministic model's wording, whenever there is
truly nothing to narrate (a completed task, or an in-progress task with no historical crew to
point to) the `crew_suggestion` evidence is withheld from the LLM call entirely (see
`routes_explain.py`) so there is structurally nothing for the model to narrate, rather than
relying on it to stay silent. When there *is* something worth saying — a replacement candidate,
or an in-progress task's `consult_crew` — the evidence does reach the model, and the system
instruction gives each of the two cases its own closing language so they are never confused.

**SQL/Python calculates; `gpt-4o-mini` only explains.** Eligibility, historical statistics,
availability and the fixed, deterministic ranking (most completions → most distinct wells →
closest-to-typical duration → most recent success → `crew_id` tie-break) are computed entirely
in `sql/crew_suggestion.sql` and `app/services/crew_suggestion_service.py`. The configured model
is `LLM_PROVIDER=openai` / `LLM_MODEL=gpt-4o-mini` — the same configuration and the same
`LLMService` every other explanation on this dashboard already uses; no second LLM integration
was introduced. "Typical" always means the median duration, "average" always the mean, and the
two are never conflated.

**No hardcoded identifiers, anywhere.** `sql/crew_suggestion.sql` takes exactly three
parameters — `well_id`, `task_code`, `report_date` — and resolves the activity, WBS and
historical evidence dynamically through the same `mapping_master` → `activity_master_csv` chain
as §7/daily_report_rules.md §2, for any target task.

Verified end-to-end against the live database while building this: an eligible, unfinished,
non-progressing task returned a suggested crew with a full evidence trail (historical count,
distinct wells, median/mean duration, most recent success, derived availability) woven directly
into the task's existing AI summary paragraph, closing with "may be worth considering as an
alternative crew for this task"; a task already showing recorded progress returned a summary
that said the task was going smoothly and no alternative crew was necessary, then added, as a
brief aside, that the crew which previously completed similar work could be worth consulting
for feedback — its `evidence.crew_suggestion` carried `eligible: false` with a suppression
reason for transparency, and a populated `consult_crew`; a completed task's summary made no
crew mention of any kind; and an old report date's evidence was confirmed to stop at that
date's own historical cutoff rather than reflecting a crew's later success.

---

## 19. Well task activity on the front page

The brief answers *what did each well do today*. It now also answers, on the same row,
*where does that well's work stand at all* — how much of it is unfinished, how much is
under way, whether the well reported anything today, and when it was last seen in the
records. Those are different questions about different spans of time, and the row keeps
them visibly apart rather than blending them into one number.

```
Well 10239 · Pipe Stringing +7 more · 14 tasks · [10 On Plan] [3 Below Plan] [1 No Actual]   [AI summary]
  31 Open   12 Incomplete   19 Ongoing   14 Reported today   Last task date 2026-08-01
```

**The figures beside the total add up to it.** `Open` is every task not recorded as
completed; `Incomplete` and `Ongoing` are its two halves and never overlap, so
`open = incomplete + ongoing` and a reader can add what they see. An earlier version had
`Incomplete` count every open task with `Ongoing` as a subset of it, so a row reading "48
incomplete, 24 ongoing" described 48 open tasks rather than 72 and nothing on the row said
which. "Incomplete" therefore now means *open but not ongoing*: no recorded actual start, or
an actual end recorded without completion. Every level keeps the same arithmetic — the well
row, the expandable task lists behind each figure, the AI evidence, and the day-scope
overview's `open_total = incomplete_total + ongoing_total`. Live check on 2026-09-17: across
269 wells, `2,008` open = `782` incomplete + `1,226` ongoing, and no single well's row broke
the identity.

**The well universe is `well.well_master`, not one day's task rows.** A live well
(`eng_completion_date IS NULL`, business_rules.md §7) is on this page whether or not it
reported anything on the selected date — which is exactly when "0 reported today, last seen
on `2026-07-14`" is worth an operator's attention. A `task_daily` row never creates a well:
`sql/well_task_state.sql` runs from `well_master` into `task_daily` through
`TRY_CONVERT(int, td.well_id)` (§3), so a row whose `well_id` does not resolve to a live well
has nowhere to attach. A well with no task record at or before the date returns no row at
all, rather than a row of zeroes claiming it was measured.

**One logical task is `(well_id, schedule_id, task_code)`**, and its state as of the report
date is its latest daily record under `ActionOn DESC, updated_at DESC, id DESC`, restricted
to `ActionOn <= report_date` — the same "latest logical state" grain §18's availability
signal already uses. A row dated after the report date can therefore never reach back into
an older one. The same `task_code` under two `schedule_id`s is two tasks, not a duplicate,
and both the expandable detail and the AI evidence carry the schedule id so the pair can be
told apart.

**Task state comes from the task's own state columns, never from `progress`.** Four states,
mutually exclusive and covering every task, so a count over them can neither double-count
nor lose one:

| State | Condition | Counted in |
|---|---|---|
| `COMPLETED` | `completed = 1` | — |
| `ONGOING` | not completed, `actual_start` recorded, `actual_end` not recorded | Ongoing |
| `NOT_STARTED` | not completed, no `actual_start` | Incomplete |
| `ENDED_NOT_COMPLETED` | not completed, yet an `actual_end` is recorded | Incomplete |

`task_daily.progress` is **not read anywhere in this feature** — not as a filter, not as a
tie-break, not in the payload. Its unit is undefined (§9): observed values run from −0.05 to
66.7, so it is not a percentage and cannot say whether a task is finished or under way. A
test asserts the column does not appear in either query at all, which is a stronger guarantee
than asserting it is used correctly. `startDate`/`endDate` are likewise never read: a planned
schedule is not evidence that work is physically happening. `ONGOING` is deliberately the
full three-part condition — `actual_end IS NULL` on its own also matches a task that has
never started.

`ENDED_NOT_COMPLETED` is reported as its own state rather than folded into either neighbour.
The record says both things — an end date, and not completed — and no rule in either rules
file resolves that, so it is shown as it stands rather than guessed at. It is not called an
error.

**`Last task date` is `MAX(ActionOn)` on or before the report date, and is never called
anything else.** It means: the latest date this well appeared in the task-daily data. It is
not a completion date, not a finish date, and not "when work stopped" — no column in this
schema is approved as a construction actual completion date (business_rules.md §10), so none
is substituted here. The label, the tooltip and the system instruction all say the same
thing.

**`Reported today` reuses the grain that already exists.** The count of logical tasks a well
reported on the date itself is read from the day's already-resolved dataset
(`sql/daily_tasks.sql`'s one-ranked-row-per-`(well_id, schedule_id, task_code, ActionOn)`
grain, §4), not re-derived by a second query with a subtly different rule. So it is, by
construction, the same number as that well's task count and its drill-down — a live test
asserts the two agree for every well — and repeated planning snapshots of one task are never
counted twice.

**Cost: one extra query per report date, and one more only if a task list is expanded.**
`sql/well_task_activity.sql` aggregates every count in SQL and returns one row per well
(measured: 252 rows, ~0.9 s warm). The tasks behind those counts come from
`sql/well_task_activity_detail.sql` — every well's incomplete tasks in one query, loaded the
first time any row is expanded and then cached for the date, so expanding a second, third and
fourth well costs nothing further. Both are cached under the same `DAILY_CACHE_TTL_SECONDS`
discipline as the daily dataset and are dropped by the same "Refresh" that reloads it, so the
two halves of a well row can never be from different loads. **There is no query per well**, and
a test asserts it.

**The whole-view summary sees every live well, not just the ones that reported.** "Explain
this view" used to describe only the day's own task rows — on 2026-09-17 that is one well and
one task, while 268 other live wells carried 2,008 unfinished tasks it had no way of knowing
about, and the summary read as though the day were about that single well. A day-scope
payload now also carries `live_well_task_activity`: how many live wells have task evidence,
how many reported on the date and how many did not, how many carry incomplete or ongoing
work, the totals behind those, and a bounded list of the wells carrying the most open work
(ranked by incomplete then ongoing count, `well_id` breaking ties so the sample — and the
evidence hash behind the cache — is stable). Attached to the whole view only: a request
narrowed to a status, WBS, activity, unit or well is explaining a slice of what was reported,
and every live well's open work is a different population than the one in scope.

**A scope with no entry for the date gets a summary of its own, not one full of zeroes.** The
usual summary block — `well_count`, five status counts, quantity totals — is all zeroes for a
well that reported nothing, which is true and useless, and the model read it out loud:
explaining well `31425`, it opened with *"there are 0 wells and 0 tasks in scope"*, describing
the shape of the payload rather than the well. There is exactly one fact to state, so the
payload now carries exactly that — `no_daily_entry` with a plain note — and drops the status
definitions and the empty task list with it. The system instruction adds that a single-well
scope must never mention a well count at all, that no figure may be introduced by naming the
block it came from, and that a task reported with `NO_ACTUAL` is still a task that was
reported — a v13 answer had conflated the two and said a well that *had* reported "did not
report any activity".

**What the AI is given, and what it is not.** A well-scoped explanation's evidence gains a
`well_task_activity` block: the counts, the four state totals, a bounded sample of the
*ongoing* tasks (the rest of the states stay as counts), the plain meaning of every figure,
and the constraints on what may not be said about them. The model explains those values; it
never derives one. The system instruction adds that a zero reported-task count means no task
was recorded that day and nothing more — not an idle well, not a failure to report — that
`last_task_date` is not a completion date, and that no task or well may be described as
delayed, late, overdue or behind schedule, because no rule in this system defines any of
those. `_PROMPT_VERSION` is bumped accordingly at each such change (12 for this block, 13 for the
day-scope overview and the empty-scope summary, 14 for the `NO_ACTUAL` and field-naming
rules), so no answer cached under an older prompt — which was never told how to read what it
is being shown — can be served for it.

**Showing the working: three drawers under every explanation.** A count an operator cannot
check is a count they have to trust, so the panel carries the working beside the prose — all
three collapsed, because the panel is for reading and the working is there for the moment
someone wants to check it:

| Drawer | What it shows |
|---|---|
| **What is sent to AI model for summary** | the evidence payload itself, byte for byte what the model was given |
| **SQL** | the queries those figures came from, as executed, with the report date listed beside each one as the bound parameter it is |
| **Proof** | every task counted as incomplete, which of them the narrower ongoing definition also counts, and why |

The proof table is one row per incomplete task — well, task code, schedule id, description
(or "Not mapped", never a substitute), whether it counts as incomplete, whether it counts as
ongoing, its actual start, its actual end, and the last date it appears in the records — with
the **reason on its own full-width line beneath it**: *"Not completed (latest record
`2026-08-12`), although an actual end of `2026-08-12` is recorded. Both are reported as they
stand; it is not counted as ongoing."* That sentence is assembled from the record's own three
columns and nothing else; it is a restatement, never an interpretation. Verified against well
`31425` on 2026-09-17: the evidence says 48 incomplete and 24 ongoing, and the proof table
lists exactly 48 rows of which exactly 24 are marked ongoing, so the arithmetic on the well's
row can be checked by hand.

The reason started life as a tenth column and was moved: at that width it was pushed off the
edge of the table, and constrained to a tenth of the width it wrapped into an unreadable
ribbon. It is the column the drawer exists for, so it gets the full width.

**The model is given none of it.** `sql_sources` and `proof` travel in the API response but
never enter the evidence payload — a test asserts the LLM call contains neither. The model's
job is to explain figures SQL already decided; handing it the query would invite it to reason
about the query instead, which is the one thing §10's boundary exists to prevent. The SQL text
is the shipped `.sql` file with its `{{include:…}}` expanded — the same string handed to the
driver — and carries no credential or connection string, because those files contain none; the
report date is listed as `? 1  report date = 2026-09-17` beside the query rather than spliced
into it, which is exactly how it is sent.

**A well with nothing reported today can still be explained.** Its daily-task summary is
genuinely empty, and the evidence says so in those words — *"no daily task was recorded …
this is not a zero quantity; it is an absence of any entry"* — rather than withholding the
totals under the multi-unit rule (§6), which would be a different and untrue reason.

**Front-page scope, and why it is a filter and not a cut.** Listing every live well with any
task history puts ~250 rows on the first screen, most of them dormant. The toolbar therefore
offers three scopes — *reporting or open work* (the default: reported on the date, or has at
least one incomplete task), *reported on this date*, and *all* — with the count on each. This
only decides which wells are listed; it never changes, hides or recalculates a figure on a
row it does show, and no well is dropped from the data, only from the first screen.

**Verified against the live `AlTasnimBI` database.** On 2026-08-01: 252 live wells carry task
evidence, 79 of them reported a task that day and 173 did not; 1,468 incomplete logical tasks
across them. Well `10239` — 59 logical tasks, 31 completed, 28 open (16 ongoing, 12
incomplete: 5 not started and 7 ended-not-completed), 5 reported that day. On 2026-09-17, well `31425` reported
nothing, had 48 open tasks — 24 incomplete and 24 ongoing — and was last seen on
`2026-08-17`; its
AI summary described exactly that and said plainly that no task being reported does not mean
work has stopped. Every well's "reported today" figure matched the day's own per-well task
count.

**A determinism bug caught by the cache, and fixed.** The first version of
`well_task_activity_detail.sql` ordered by `(well_id, task_state, task_code)`, which leaves
two tasks sharing a `task_code` under different `schedule_id`s free to come back either way
round. A sample of those rows goes into the AI evidence, whose SHA-256 is the explanation
cache's key (§10) — so the same well, explained twice, hashed differently and paid for a
second, identical LLM call. Observed exactly that way against the live database. `schedule_id`
is now the final ordering key, and a test asserts both the ordering and that two consecutive
evidence builds serialise identically.

---

## 20. DYNAMIC_DB — the schema-adaptive engine

The business meaning of this report is fixed. The database that stores it is not.
Tables get renamed, a column changes type, a mapping table is superseded — and
until now every one of those would have meant somebody rewriting SQL by hand,
after the morning brief had already broken.

This section is the layer that absorbs that. It changes **where an answer comes
from**, never **what the answer means**: `business_rules.md` and
`daily_report_rules.md` remain authoritative and untouched, and every figure is
still calculated by SQL and Python, never by a model.

**It is a separate project, not a subfolder of this one.** It lives at
`DYNAMIC_DB/`, a sibling of `backend/` and `frontend/`, with its own `.env`, its
own dependencies, its own logging, its own CLI and its own 164-test suite — it
can be introspected, fingerprinted, compiled and run entirely on its own, with
no Daily Report code loaded at all (`cd DYNAMIC_DB && python -m dynamic_db.cli
status`). It knows nothing about wells, tasks, crews, quantity status or any
other Daily Report concept: everything specific to this application — the seven
capabilities and their business rules — lives in Daily Report's own
`backend/app/capability_manifest.py`, as data the generic engine is handed, not
code the engine contains.

Daily Report talks to it through exactly two files: `app/dynamic_client.py` (the
stable interface — `sql_for`, `bootstrap_status`, `ensure_ready`, …) and the
manifest above. Nothing else in `backend/` imports `dynamic_db` directly — a
test (`test_daily_report_consumes_the_contract_without_touching_engine_internals`
in `tests/test_dynamic_db_contract.py`) enforces that boundary mechanically, not
just by convention. `ARCHITECTURE.md` at the project root is the full design
document for this split — why it exists, every module's role, the startup and
fingerprint lifecycle, the logging architecture, and how to investigate a failed
run by its run id; this section stays a summary.

### 20.1 What is dynamic, and what is deliberately not

```
DYNAMIC        the physical schema, and the SQL that reads it
DETERMINISTIC  the business rules, the safety gate, the execution, every number
REASONING      exactly two calls -- resolving concepts to objects, and judging
               whether the result means what the rules say
NARRATION      the existing explanation layer, unchanged
```

A model is asked only the questions that genuinely need judgement. Everything
decidable from a catalogue, a text or arithmetic is decided that way.

### 20.2 The pipeline

```
live schema read                      (no model)
  -> change detected vs the last snapshot          (no model)
  -> affected capabilities identified from their own dependencies   (no model)
  -> SQL authored against the CURRENT schema       (reasoning)
  -> deterministic validation: safety, schema, contract   (no model)
       \-- fails, budget left --> back to authoring
       \-- fails, budget spent -> ABANDON (recorded, with why)
  -> executed once, read-only, bounded             (no model)
       \-- fails, budget left --> back to authoring
       \-- fails, budget spent -> ABANDON
  -> semantic verification                         (reasoning)
       \-- rejected, budget left --> back to authoring
       \-- rejected, budget spent -> ABANDON
       \-- not applicable ----------> recorded, never approved
  -> verified artifact promoted                    (no model)
  -> the application serves it
```

`ABANDON` is a real, logged outcome rather than a silent stop: a compile that
ran out of attempts says so, and says which gate it failed at, so "still
running" is never confused with "gave up".

Run it from `DYNAMIC_DB/`: `python -m dynamic_db.cli status` (free), `inspect`,
`fingerprint`, `validate`, `compile [--capability X] [--force]`, `schema`,
`changes`, `artifacts`, `audit`, `run [--report-date D]` (the full bootstrap
pipeline below), `llm-config [--explain]`, `logs [--run-id ID]`. `--force`
recompiles a capability that is not stale — the deterministic check still runs
and is still logged, so a forced run is auditable as exactly that.
`GET /api/health/schema` and `GET /api/bootstrap/status` answer the staleness
and readiness questions over HTTP, from inside the running Daily Report app,
also without calling a model.

### 20.3 A normal day costs nothing

The baseline `.sql` files are not a fallback. Each is registered as its
capability's **version 1** after passing the same deterministic validator a
compiled artifact passes, so on an unchanged schema this system makes **zero
model calls**. The reasoning model exists for the day one of those files stops
fitting the database.

Two fingerprints keep it that way:

| | includes | guards |
|---|---|---|
| **structure** | tables, columns, types, nullability, keys, constraints | compiled SQL |
| **live** | the above **plus row counts and the render version** | the rendered schema text and the measured hints |

Row counts are deliberately absent from the structure fingerprint. A new day of
task data arrives every morning; if it counted, every capability would be
recompiled daily at full reasoning cost — which is exactly what compiling once
is for.

### 20.4 Only what changed is recompiled

Each artifact records the tables **and columns** its SQL actually reads,
extracted from the SQL text deterministically — never declared by the model,
because a model that reports its own dependencies is one that can under-report
them.

A schema change is intersected with those recorded dependencies:

```
changed objects  ∩  a capability's dependencies  =  ∅   ->  reuse, spend nothing
                                                 ≠  ∅   ->  recompile that one
```

Per **column**, not per table — every capability here reads the well table, so
table-level tracking would recompile all seven for any change to it.

### 20.5 Validator vs verifier

They answer different questions, and conflating them is how a system like this
goes wrong.

| | **Validator** (deterministic) | **Verifier** (reasoning) |
|---|---|---|
| asks | *is this safe and well-formed?* | *does this MEAN what the rules say?* |
| uses | SQL text + live schema | rules, schema, SQL, result summary, sample |
| decides | read-only, one statement, approved objects only, every name exists, parameters bound, required aliases present, grain resolved, no literal date, no credentials | concepts correctly resolved, exclusions honoured, grain on the business key, scale and type correct, nothing undefined quietly defined |
| on failure | rewrite, costs one SQL attempt | rewrite, costs one review attempt |
| runs | **before** the database is touched | after a real, bounded execution |

The validator is what makes the read-only guarantee a guarantee: it does not
depend on a model behaving, and a model asserting "this SQL is safe" is not a
safety mechanism.

The verifier **fails closed**. An unreadable verdict is a rejection, never an
approval — defaulting the other way lets a trailing comma turn a rejection into
an approval with nothing in the log to say so. It has a third verdict,
`NOT_APPLICABLE`, for when the database simply does not record what a capability
is about: approving would store a query that answers nothing while reading
downstream as a clean result, and rejecting would burn every attempt asking for
a fix no query can make.

### 20.6 Promotion, and when an old artifact may keep serving

A candidate is written beside the current artifact and only replaces it once
verified. If it fails, the previous one is still there, unchanged.

"Still there" is not "still usable", so that decision is deterministic and
deliberate: the stored SQL is re-validated against the **new** schema. If every
object it names still exists and its contract still holds, the change was
irrelevant to it and it keeps serving while a recompile is attempted. If not,
the capability is reported **unavailable** (HTTP 503, with the reason) rather
than answering a different question in the same shape. `DYNAMIC_SQL_MODE=strict`
refuses even the first case.

### 20.7 The reasoning model, and how its parameters are decided

DYNAMIC_DB has exactly one model role — `reasoning`, used for SQL authoring and
semantic verification — configured entirely inside `DYNAMIC_DB/.env`
(`REASONING_MODEL`, `REASONING_PROVIDER`, `REASONING_API_KEY`, …), independent
of and never falling back to `backend/.env`'s own `LLM_*` settings, which
configure a completely different thing: Daily Report's explanation layer (§10).
Two independent LLM configurations, for two independent purposes, in two
independent projects.

There is **no silent fallback**: if the reasoning system is unavailable the
compile fails and the last verified artifact keeps serving, or the capability is
reported unavailable. `ALLOW_REASONING_FALLBACK` exists for an operator who has
decided otherwise, and is off by default.

**Not every model accepts the same request shape**, and guessing wrong is worse
than not sending a parameter at all — a reasoning-family model (`o1`, `gpt-5`,
…) rejects `temperature`/`top_p`/penalty terms outright and expects
`max_completion_tokens` in place of `max_tokens`; an ordinary chat model expects
the opposite. `dynamic_db/llm_config.py` classifies the configured model into a
documented family before deciding which parameters a request may carry, and —
deliberately — leaves an unrecognised model name (`FAMILY_UNKNOWN`) unclassified
rather than guessing, so a real, working request's shape never changes based on
an assumption about a custom or internal model alias.
`python -m dynamic_db.cli llm-config --explain [--json]` prints exactly which
parameters the currently configured model will and won't be sent, and why —
useful the moment `REASONING_MODEL` changes to something new.

### 20.8 Token cost is a design constraint

* the baselines mean an unchanged schema costs **nothing**;
* only the **declared rule sections** a capability needs are sent — roughly a
  quarter of the rulebook for a typical capability, not all of it;
* the schema block is built **once** and handed byte-identically to the author
  and the verifier, in the same position, so a provider can prefix-cache the
  largest part of both prompts;
* the verifier is shown a one-object execution **summary** and a bounded row
  sample, never the result — so verifying a capability costs the same whether
  its query returns ten rows or a million;
* prompts are static; only the schema, the changes and the last rejection vary;
* every retry loop is bounded, at
  `(verify retries + 1) × (sql retries + 1)` author calls.

### 20.9 The visibility boundary

`INCLUDED_TABLES` is an explicit allowlist. Only those objects are introspected,
only they may appear in a prompt, and only they may be referenced by compiled
SQL. Empty means nothing is approved, which fails closed. A foreign key pointing
outside the boundary is not even rendered — naming an object the author is
forbidden to use only invites a wasted rewrite. Secret-looking columns are
filtered on top of that, and no credential, connection string or API key ever
reaches a prompt.

### 20.10 Nothing generic names a physical object

`dynamic_db/audit.py` scans the source for physical names, and a test fails the
build on any finding inside the prompts or the graph, or on any qualified
reference in executable code anywhere. The names it looks for come from the live
allowlist, so pointing the engine at a different database audits that database
with no edit. Its allowed-paths and strict-zone are parameters, not hardcoded
constants, so a caller can audit its own tree with its own rules — proven by a
test where Daily Report's own suite asks DYNAMIC_DB's audit to scan
`backend/app/` under Daily Report's zones, independent of the run DYNAMIC_DB
does on itself.

Physical knowledge is allowed in exactly four places: runtime introspection, the
authoritative rule documents, the baseline SQL and compiled artifacts, and test
fixtures.

### 20.11 What the expense log now records

`llm_usage_log.xlsx` gains `System`, `Node` and `Capability` columns, written at
the call site, so DYNAMIC_DB's reasoning calls and Daily Report's own
explanation calls (§10) land in the same workbook clearly labelled as what they
were for, without either project hardcoding a path into the other's file:
`dynamic_db/llm.py` never reaches into `llm_usage_tracker.py` directly — it
calls `llm.set_usage_sink(...)`, and Daily Report wires the real tracker in at
startup. A missing or unwired sink can still never break a request; DYNAMIC_DB
always logs the same event structurally either way. Older workbooks are migrated
in place and no historical row is lost.

### 20.13 Startup: the fingerprint gate, and a real bootstrap state machine

Before Daily Report serves a request, DYNAMIC_DB needs to know its artifacts
still match the live schema. `dynamic_db/bootstrap.py`'s `BootstrapOrchestrator`
runs that check through a state machine an operator (or the frontend) can watch
in real time: `STARTING → CONNECTING → INTROSPECTING → GENERATING_FINGERPRINT →
COMPARING_FINGERPRINT → (SCHEMA_CHANGED →) VALIDATING → COMPILING →
LOADING_ARTIFACTS → READY`, or `FAILED` at any step. It runs in a background
thread at FastAPI startup — the API is up immediately, but Daily Report will not
report itself `READY` until this finishes — and its status is read
thread-safely from `GET /api/bootstrap/status` mid-run.

`frontend/src/components/Bootstrap/BootstrapGate.jsx` polls that endpoint with a
real `fetch` (no simulated progress) and withholds the dashboard until it
reports `ready`. On `FAILED` it shows the run id, a timestamp and where the full
log lives, rather than a bare error banner. On an unchanged fingerprint this
whole sequence is fast and free (§20.3); on a changed one it drives exactly the
capabilities affected (§20.4) through a real compile before reporting ready.

### 20.14 Logging: one pipeline, a run id, and how to investigate a failed run later

`dynamic_db/logging_setup.py` is the one structured logging pipeline for this
project — console output plus rotating human-readable and JSONL log files under
`DYNAMIC_DB/logs/` (paths, size and retention all configurable, never
hardcoded). It is scoped to its own `dynamic_db` logger namespace with
`propagate = False`, so it can run inside the same process as Daily Report's own
logging (`app/utils/logging_config.py`) without duplicating a single line of
either project's output — verified live: before this was scoped correctly, every
Daily Report log line was appearing twice, once under each project's handlers.

Every run gets a `run_id`, stamped onto every log line for that run via a
`contextvars.ContextVar`, and a per-run JSON record (`RunRecord`) written to
disk: start/end time, duration, every state transition, errors with full
tracebacks, the fingerprint, the schema-change result, the validation result,
artifact versions touched, and the LLM configuration used (never a secret).
Given only a run id — from a frontend failure screen, a terminal log, or the
usage workbook — `python -m dynamic_db.cli logs --run-id <id>` retrieves all of
it. A dedicated redaction filter scrubs credential shapes centrally, in this one
place, rather than each call site filtering its own.

### 20.15 Verified behaviour

Exercised end to end against the live `AlTasnimBI` database, not only against
the invented fixture schema `tests/fixtures.py` uses for the offline unit
tests: introspection discovers all 9 approved tables with their measured grain
(`well.task_daily` — 35,749 distinct `task_code` of 110,184 rows;
`dbo.mapping_master` — 634 distinct `Activity_ID` of 642; `ref.crew` — 7,582
distinct `crew_id` of 8,255), every one of the seven capabilities' baseline SQL
passes the same deterministic validator a compiled artifact passes and is
registered as version 1 for zero model calls, a real structural change (a
dependency fingerprint invalidated) drives one capability through authoring,
validation, a real bounded execution against SQL Server, semantic verification
and promotion while the other six are never touched, and the superseded
artifact survives in history. A genuine `FAILED` bootstrap was also forced (bad
database credentials): the frontend showed the failure with a run id, the
terminal showed the full error, and `logs --run-id` retrieved it afterward, with
the password correctly redacted throughout.

DYNAMIC_DB's own suite (164 tests, `cd DYNAMIC_DB && python -m pytest`) covers
fingerprints, dependency extraction, the safety and contract gate, graph routing
and retry budgets, the reasoning-model parameter resolution, usage accounting,
the static audit, the bootstrap state machine and its logging — independent of,
and runnable without, the Daily Report backend. The Daily Report suite (347
tests) includes 7 contract tests that exercise DYNAMIC_DB only through
`app/dynamic_client.py`, confirming the boundary holds and that a corrupted
artifact file is rejected rather than trusted.

---

## 21. Crew match and date validation

Two daily-entry checks, computed with the same discipline as quantity status (§5):
exact, deterministic, in `app/services/validation_service.py`, never a guessed
tolerance. Neither reads or is read by §18's crew suggestion or §19's well task
activity — three independent features that happen to sit near each other in the
same evidence.

**Crew match.** `crew_match_status` (`MATCHED` / `MISMATCHED` / `NOT_COMPARABLE`)
compares `crew_code` (§2's WBS-planned crew) against `crew_type_code` (§15 — the
crew TYPE actually assigned, via `task_daily.crew_id`). These are two
independently-maintained columns from different tables that happen to share one
code space: measured, on rows where both resolve, 98.4% match exactly (22,947 of
23,314), which is what justifies comparing them at all. `task_daily.planned_crew`
— the more obviously-named "assigned crew" candidate — was measured and rejected:
it is `NULL` on every one of 110,184 rows. `NOT_COMPARABLE` covers the common case
of no crew recorded on the task at all (~69% of rows measured), and the rarer case
of no WBS crew mapped; it is never forced into a match or a mismatch. Like crew
suggestion (§18), a mismatch is never described as a fault of the crew on the task
— it states that two records disagree, nothing more.

**Date validation.** `start_date_status` and `end_date_status` (`ON_TIME` / `EARLY`
/ `LATE` / `NO_ACTUAL` / `NOT_VALIDATED`) compare `task_daily.actual_start` /
`.actual_end` against `task_daily.startDate` / `.endDate`, independently for start
and end. `task_daily` carries three candidate "planned" date pairs —
`startDate`/`endDate`, `target_start`/`target_end` and
`committed_start`/`committed_end` — and they disagree with each other on 40-60% of
rows measured, so which one counts as "the plan" was a decision, not a default:
`startDate`/`endDate` is the one used here. Being early or late is a descriptive
fact, never an error — the same doctrine `business_rules.md` §4 already applies to
well-level schedule variance. A separate data-quality flag, `INVALID_DATE_SEQUENCE`
(§7), fires when `actual_end` is recorded before `actual_start` on the same task —
a logically impossible sequence, not a variance from plan — measured on 214 of
110,184 rows (~0.19%).

Both classifications are exposed on `TaskOut` and fed to the LLM explanation
(§10) with the same conservative wording quantity status already gets: never
called an error, never a cause, never a judgement of the crew. Full detail,
including how each column pair was chosen, lives in `daily_report_rules.md` §10.

---

## 22. The front page's own AI summary, and the session cache behind it

### 22.1 What loads, in what order

`DailyMorningBrief.jsx` renders the top level in a fixed reading order, and each part
answers a different question:

| | what it answers | measured against |
|---|---|---|
| `MilestonesSummary` | what is running out of time | **today** (§16) |
| `ExplainPanel` (scope `day`) | what the day as a whole looks like | the selected report date |
| `WellList` | what each well did, and where its work stands | the selected report date |

The day's summary **opens by itself on first load**. An operator should not have to ask for
the brief the page exists to give them, and asking for it was the one step between opening
the dashboard and reading it. Closing it is respected until the report date changes — at
which point the page is describing a different day, and opens that day's summary the same
way it opened the first. A "Show AI summary of this view" button brings it back, and costs
nothing to press (§22.2).

Only the counts, and the four soonest deadlines, are shown inline at the top. On this data
the overdue backlog runs to several hundred, so listing it there would bury the brief under
it; the full list stays one click away on its own page (§16). The day strip below it was
trimmed to the selected date's own figures at the same time — it used to repeat the same
two milestone counts a few pixels under the panel that now owns them.

### 22.2 Two caches, deliberately different

| | backend (`LLMService`) | browser (`services/explainCache.js`) |
|---|---|---|
| keyed by | SHA-256 of the evidence (§10) | the explain request's fields |
| decides | whether a **model call** happens | whether an **HTTP request** happens |
| lives in | process memory | a module-level `Map` |
| survives a restart | **no**, by design (§10) | **no**, by design |
| cleared by Refresh | **yes**, for that date | **yes** |

The browser cache exists because the front page now asks for the day's summary by itself
and every row can ask for its own: without it, drilling into a well and pressing Back would
re-request the day summary, and reopening a row would re-request that well. With it, both
come straight back — no spinner, no request — because the operator has already read them on
this screen. Verified live: drilling into a well and returning restores the summary with
**zero** further `POST /api/daily/explain` calls.

**It is deliberately not `localStorage` or `sessionStorage`.** The cache must not outlive
the page. A reload, or reopening the project, is exactly when an operator expects freshly
generated text rather than yesterday's sentence about a well whose records have since moved
on — so a plain in-memory `Map`, emptied by the page itself, is the whole mechanism. There
is nothing to expire, invalidate or clean up, and no way for it to survive a refresh by
accident. Pressing **Refresh** clears it explicitly as well, because `refresh=true` already
purges the backend's cached explanations for that date (§8) and keeping a browser copy of
that same superseded text would directly contradict it.

**An unavailable answer is never cached, only a successful one** — the same rule the backend
applies to its own cache, and for the same reason. A provider outage arrives as a perfectly
good HTTP 200 carrying `available: false`; caching that would leave the panel repeating
"unavailable" for the rest of the session, long after the provider recovered. This was found
by running the real page against an unreachable provider, not in review.

### 22.3 The inside pages are unchanged

Opening a well still opens `WellDetail.jsx`, and its AI summary is still requested per
scope, with the three collapsed drawers beside it: **what is sent to the AI model**, the
**SQL** those figures came from as it is actually executed, and the **proof** rows behind
the counts (§10, §19). The front page's changes stop at the front page.

### 22.4 Crew ids are shown, not only crew types

Wherever a crew is named, its id is now shown with it:

* **In the explanation panel**, a `crew_suggestion` (§18) renders as a small deterministic
  card beside the model's prose — the suggested (or worth-consulting) crew's id, the crew
  currently recorded on the task and *its* id, and the supporting history. The card is built
  from the same SQL-computed evidence the model is given; it selects fields and derives
  none. The prose is the model's wording of that evidence, and the card is the evidence, so
  the id is present whichever one the operator reads.
* **On the well detail page**, each task shows `Crew ID (assigned)` beside the WBS crew code,
  and the crew chain repeats it (`Crew ID 8263`). A crew type name does not identify one
  crew — several crew instances share a type — and the id is what an operator needs in order
  to actually go and find them.
* **In the model's own wording**, `SYSTEM_INSTRUCTION` now requires the crew id the first
  time any crew is mentioned, in backticks like every other literal value, and still forbids
  inventing one: when `current_crew.crew_id` is null the model must say no crew is recorded
  rather than name one.

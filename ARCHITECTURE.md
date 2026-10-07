# Architecture: DYNAMIC_DB and DAILY_REPORT

This document explains the separation between the two projects in this
repository, why it is drawn where it is, and how to run, extend and debug
each side.

```
NGXP/PROJECTS/DAILY_REPORT_2/          (this repo)
├── DYNAMIC_DB/                        independent schema-adaptive engine
│   ├── dynamic_db/                    the importable package
│   ├── tests/                         its own, offline-first test suite
│   ├── .env / .env.example
│   ├── requirements.txt
│   └── logs/, .cache/                 gitignored, created at runtime
├── backend/                           Daily Report's FastAPI application
│   ├── app/
│   │   ├── dynamic_client.py          <-- THE stable interface (read this first)
│   │   ├── capability_manifest.py     <-- Daily Report's content, fed to the engine
│   │   ├── _dynamic_db_path.py        puts DYNAMIC_DB on sys.path
│   │   └── ...                        unchanged business logic
│   ├── business_rules.md / daily_report_rules.md   authoritative rule text
│   └── sql/*.sql                      baseline queries, Daily Report's own
└── frontend/                          React UI, incl. the bootstrap gate
```

A monorepo, not two repositories: both projects stay under one `git` history
and one branch, but DYNAMIC_DB has its own virtual environment, its own
`.env`, its own tests, and can be developed, tested and run with **no
reference to Daily Report at all**. Renaming `backend/`/`frontend/` was
deliberately **not** done — every existing path, `.claude/launch.json` entry
and piece of tooling stays valid, which is what "Daily Report must remain
functional throughout" actually requires; the architectural boundary that
matters is DYNAMIC_DB being extracted out, not which folder Daily Report's
own code happens to sit in.

---

## 1. What DYNAMIC_DB is

A schema-adaptive SQL compilation engine. Given a live SQL Server database and
a set of registered **capabilities** (business questions, described as data —
see §2), it:

- introspects the schema and measures its shape (grain, declared keys, numeric
  scale);
- fingerprints the structure, separately from the data;
- detects what changed since the last run;
- authors SQL for affected capabilities using a reasoning model, resolving
  business concepts against the *current* schema;
- validates that SQL deterministically (no model involved);
- executes it, bounded and read-only;
- verifies its meaning semantically (a second, different reasoning call);
- promotes a verified artifact into service.

It has **no knowledge of Daily Report, wells, tasks, WBS or crews**. Point it
at a different database with a different capability manifest and none of its
own code changes — verified by `dynamic_db/audit.py`, which fails a build if a
physical name ever leaks into its generic prompts or graph logic.

## 2. What Daily Report is

The existing FastAPI + React application: daily task calculations, well/
task/activity/WBS/crew reporting, the report APIs, the UI, the LLM
explanation layer, crew suggestions, caching. All of this is **unchanged
business logic** — the refactor moved *where SQL comes from*, never *what a
report means*. `business_rules.md` and `daily_report_rules.md` remain
authoritative and untouched.

Daily Report is DYNAMIC_DB's first, and so far only, consumer. It supplies
DYNAMIC_DB its content — seven capabilities (`DAILY_SUMMARY`, `DAILY_DETAILS`,
`WELL_ACTIVITY`, `WELL_DETAIL`, `MILESTONES`, `CREW_SUGGESTION`,
`ACTIVITY_DATES`) — via `backend/app/capability_manifest.py`, and consumes
compiled SQL back through `backend/app/dynamic_client.py`.

## 3. Why they are separate

Before this refactor, the schema-adaptive layer lived inside
`backend/app/dynamic/` and quietly depended on Daily Report's own settings
module, its own database client, and its own SQL-loading utility. That made
it *look* separable without actually *being* separable: it could not be
imported, tested, or run without Daily Report's whole configuration and
directory layout coming along. Inspection (see the PR/commit history) found:

- the compile engine (prompts, validation, the graph, fingerprinting) was
  already 100% generic — verified by grepping for physical names, finding
  none;
- the *content* — which capabilities exist, which rule sections they need,
  which baseline SQL seeds them — was Daily-Report-specific data masquerading
  as engine code;
- the coupling points were few and precise: one settings import, one DB
  client import, one SQL-loader import, and a same-repo file reach-out for
  usage tracking.

Separating cleanly means an engine that can be pointed at a *different*
database for a *different* application with zero code changes, tested and
debugged independently of the thing built on top of it, and upgraded without
redeploying the whole Daily Report application.

## 4. Startup lifecycle

```
FastAPI startup event (main.py)
      │
      ▼
dynamic_client.start_bootstrap_background()   -- returns immediately
      │                                            (does NOT block startup)
      ▼
BootstrapOrchestrator.run()  [background thread]
      │
      ├─ CONNECTING            real DB connectivity check
      ├─ INTROSPECTING         read the live schema (cached when unchanged)
      ├─ GENERATING_FINGERPRINT
      ├─ COMPARING_FINGERPRINT  against the last recorded snapshot
      ├─ VALIDATING            is each capability's current artifact stale?
      ├─ COMPILING             (skipped entirely when nothing is affected)
      ├─ LOADING_ARTIFACTS
      └─ READY  (or FAILED, from any step, with a full error record)
```

`GET /api/bootstrap/status` reports this live, from a thread-safe snapshot —
correct even while a run is still in progress. The frontend polls it and
withholds the report UI until `ready: true`; on `FAILED` it shows the error,
the run ID, the timestamp and where to find the full log (§15).

**Why background, not blocking.** FastAPI's `app` object is shared by Daily
Report's whole existing test suite via `TestClient`. Blocking every test's
startup on a multi-second, real-database bootstrap would break or drastically
slow hundreds of passing tests for no benefit they need. The real gate this
architecture calls for — the UI not rendering until DYNAMIC_DB is ready — is
enforced by the frontend polling `/api/bootstrap/status`, not by blocking the
backend process. The existing test suite additionally marks the background
thread "already started" in its one autouse fixture, so no test ever opens a
real database connection or spends a real model call by accident.

## 5. Fingerprint lifecycle

Two fingerprints, computed together but guarding different things:

| | includes | guards |
|---|---|---|
| **structure** | tables, columns, types, nullability, keys, constraints, the allowlist itself | compiled SQL artifacts |
| **live** | the above **plus row counts and the render version** | the rendered schema text shown to the model |

Row counts are deliberately excluded from the structure fingerprint: a new
day of task data must never trigger a recompile. First run has no baseline to
compare against and is reported as such, not as "everything changed."
Subsequent runs compare against the snapshot recorded at the end of the
*previous* run (`dynamic_db.introspect.store_previous_snapshot`), advanced
only once a run actually completes — a run that crashed mid-way must still
see the same change next time.

Per-capability staleness is decided from each artifact's own **recorded
dependencies** (the exact tables and columns its SQL reads, extracted
deterministically from the SQL text — never declared by the model), not from
the whole-database fingerprint. A change to one capability's own columns
recompiles that one capability; the other six are never touched. This is
tested directly (`DYNAMIC_DB/tests/test_graph.py`,
`backend/tests/test_dynamic_db_contract.py::test_a_column_change_marks_only_the_capabilities_that_read_that_column`).

## 6. Artifact lifecycle

```
capability registered (Capability object, with baseline_sql as TEXT)
      │
      ▼
ensure_seeded()  --  the baseline validated by the SAME deterministic
      │               validator a compiled artifact passes, and promoted
      │               as version 1 -- ZERO model calls on an unchanged schema
      ▼
[schema changes under it]
      │
      ▼
candidate authored → validated → executed (real DB) → verified
      │                                                    │
      │                                          rejected  │ approved
      │                                             │       │
      │                                        (retry,      ▼
      │                                       bounded)   PROMOTED as v2,
      │                                                  v1 kept in history
      ▼
if the candidate never verifies: the STORED artifact is re-validated against
the NEW schema. Still valid → keeps serving (a deliberate decision, logged as
such). No longer valid → capability reported unavailable (503), never a
plausible wrong answer.
```

Artifacts live under `DYNAMIC_DB/.cache/<database-identity>/artifacts/` —
one JSON file per capability, current + candidate + bounded history.

## 7. Logging architecture

**One pipeline per project**, not one for files and an unrelated one for the
console:

- `dynamic_db.logging_setup.configure()` attaches a console handler, a
  rotating human-readable file handler and a rotating JSONL handler to the
  **`dynamic_db` logger namespace** (not the root logger), with
  `propagate=False`. Every module in the package logs through
  `logging.getLogger(__name__)`, which is always a child of `dynamic_db`, so
  all of it is captured — while Daily Report's own root-logger configuration
  is completely undisturbed, and Daily Report's own log lines are never
  duplicated through DYNAMIC_DB's formatters.
- A `run_id` is stamped onto every record via a `contextvars`-based filter
  (`run_context(...)`), so no call site anywhere in the package has to pass
  one explicitly.
- Secret redaction is centralised in one `logging.Filter`
  (`_RedactionFilter`), applied to every handler. Nothing scatters manual
  `if "password" in text` checks through the codebase.
- Every run additionally gets one **per-run JSON record**
  (`DYNAMIC_DB/logs/runs/<run_id>.json`) with start/end time, duration,
  every state transition and its duration, the fingerprint, the schema-change
  result, and the error (if any) — the answer to "given a run_id, what
  happened" without grepping a rotating log that may have already rotated
  past it. Retention is configurable (`LOG_RETENTION_DAYS`) and pruned via
  `dynamic_db.logging_setup.prune_run_records()`.

Daily Report's own logging (`app/utils/logging_config.py`) is untouched.

## 8. Planner conditions — and why one was not added

Section 14 of the brief asks for a Planner only when genuinely needed. This
compile pipeline is **fully deterministic**: introspect → detect change →
decide affected capabilities → author → validate → execute → verify →
promote is the same fixed order on every single run, with no discovered fact
that could make it skip a step or choose a different one. The only "planning"
this system genuinely does — deciding compile order so a capability that
borrows another's probe parameters is never compiled first
(`dynamic_db.capabilities.compile_order`) — is a plain topological sort over
already-known dependencies, needing no model and no graph node of its own.

A Planner was therefore **not added**. Adding one merely because the project
uses LangGraph would be exactly the over-engineering §24 warns against: it
would add a reasoning-model call and a failure mode to a pipeline that is
already correct, deterministic and free.

## 9. LLM configuration

DYNAMIC_DB has exactly **one** model role (`SYSTEM_REASONING`) — it is not a
narration service, and has no "fast" model of its own. Daily Report's
existing explanation layer keeps its own, completely separate model
configuration (`LLM_MODEL` etc. in `backend/.env`), unaffected by any of this.

`dynamic_db/llm_config.py` classifies the configured reasoning model into a
documented OpenAI parameter family — `chat` (`gpt-4*`, `gpt-3.5*`) or
`reasoning` (`o1`/`o3`/`o4*`, `gpt-5-*`) — and resolves, for every
OpenAI-documented Chat Completions parameter, whether it is supported,
its currently-configured value, its valid range, and what a zero value means
for it. **An unrecognised model name is classified `unknown`, never guessed
into a family** — this matters concretely: the project's own configured
model name (`gpt-5.6-luna`, a decimal-versioned custom alias no real OpenAI
release uses) is deliberately left `unknown` rather than pattern-matched into
`reasoning`, because guessing wrong would have silently started omitting
`temperature` and the penalty terms from a request a real, currently-working
endpoint might still expect. Only exact, documented names get the enriched,
family-aware payload shape; an unrecognised name gets **exactly the request
shape this client always sent**, unchanged.

Zero-defaulting follows the brief's own carve-outs: `temperature`,
`presence_penalty` and `frequency_penalty` default to `0` (legal, neutral
values); `max_tokens`/`n`/`top_p` are **not** forced to an illegal zero (a
0-token ceiling produces no completion at all) and report the true minimum or
provider default instead; `seed` is left unset by design, because `0` is a
legal seed but not a *neutral* one.

Explore it: `python -m dynamic_db.cli llm-config` and
`python -m dynamic_db.cli llm-config --explain [--json]`.

## 10. CLI commands

One CLI, one technology (`argparse`, matching this project's existing style —
no second command framework introduced):

```
python -m dynamic_db.cli status                  what is compiled, and is it stale
python -m dynamic_db.cli inspect [--json]         tables/columns/relationships found
python -m dynamic_db.cli fingerprint              structure + live fingerprint only
python -m dynamic_db.cli schema [--tables ...]    the live schema as the agents see it
python -m dynamic_db.cli changes                  what moved since the last run
python -m dynamic_db.cli validate                 are current artifacts still valid
python -m dynamic_db.cli seed                     register baseline SQL, no model
python -m dynamic_db.cli compile [--capability X] [--force]
python -m dynamic_db.cli run [--report-date D]    the FULL bootstrap pipeline
python -m dynamic_db.cli artifacts [--capability X] [--sql]
python -m dynamic_db.cli audit                    physical names in generic code
python -m dynamic_db.cli llm-config [--explain] [--json]
python -m dynamic_db.cli logs [--run-id ID] [--tail N]
python -m dynamic_db.cli refresh                  drop the cached schema description
```

Every command except `compile` and `run` is free — no model call, most not
even a database round trip beyond cheap metadata reads.

## 11. Failure behaviour

If DYNAMIC_DB fails (cannot connect, cannot introspect, an unhandled error):

- `state: "FAILED"` with `ready: false` and a full, redacted error record —
  never a silent `READY`;
- the frontend shows the failure screen (run ID, timestamp, error message,
  where to find the full log) instead of the dashboard;
- the terminal shows the failure live, via the standard logging pipeline;
- the persistent per-run record (`DYNAMIC_DB/logs/runs/<run_id>.json`)
  survives the process and is retrievable later by run ID.

A stale artifact that still validates against the new schema *may* keep
serving — a deliberate, logged decision (`DYNAMIC_SQL_MODE=auto`), never a
default. `DYNAMIC_SQL_MODE=strict` refuses even that and reports the
capability unavailable instead.

## 12. Configuration

Two independent `.env` files, each with its own `.env.example`:

- `DYNAMIC_DB/.env` — database connection, the schema allowlist, compile
  behaviour, the reasoning model, logging.
- `backend/.env` — Daily Report's own API/UI/explanation-layer settings, plus
  its own copy of the database connection (both projects talk to the same
  physical SQL Server, as two independent processes each holding their own
  connection configuration).

No credential, model name, table name, path, timeout or retry count is
hardcoded in either project's Python source; every one is read from its own
environment. `dynamic_db/audit.py` also flags a hardcoded date literal in
executable code as a build-breaking finding.

## 13. How Daily Report consumes DYNAMIC_DB

Through exactly two modules, and nothing else:

- **`backend/app/capability_manifest.py`** — Daily Report's content. Defines
  the seven capabilities as data (concepts, required output columns, rule
  section numbers, baseline SQL text already loaded from `backend/sql/`) and
  registers them into DYNAMIC_DB's engine via
  `dynamic_db.capabilities.register()`.
- **`backend/app/dynamic_client.py`** — the stable interface. Exposes
  `sql_for(capability_id, report_date=...)`, `CapabilityUnavailable`,
  `bootstrap_status()`, `start_bootstrap_background()`, `compile_now()`,
  `detailed_status()`. Every repository imports from here — never from
  `dynamic_db.*` directly. This is mechanically enforced:
  `test_daily_report_consumes_the_contract_without_touching_engine_internals`
  in `backend/tests/test_dynamic_db_contract.py` fails the build if any other
  file in `backend/app/` imports `dynamic_db` directly.

`backend/app/_dynamic_db_path.py` is the one place `../DYNAMIC_DB` is put on
`sys.path` — imported, for that side effect only, by both of the above.

## 14. How to run the system

**DYNAMIC_DB alone**, independently of Daily Report:

```bash
cd DYNAMIC_DB
cp .env.example .env   # fill in DB_* and REASONING_*
pip install -r requirements.txt
python -m dynamic_db.cli run
```

**The whole stack**, exactly as before this refactor:

```bash
python run.py
```

(`run.py`'s job is unchanged: start the FastAPI backend and the Vite
frontend. DYNAMIC_DB's bootstrap now runs automatically, in the background,
the moment the backend starts.)

## 15. How to investigate a failed run

Given a `run_id` (from the frontend's failure screen, from a log line, or
from `python -m dynamic_db.cli logs`):

```bash
python -m dynamic_db.cli logs --run-id <run_id>
```

returns the complete record: start/end time, total duration, every state
transition with its own duration, the fingerprint, the schema-change result,
and the full error (type, message) if it failed — with any credential or
connection detail already redacted. The rotating logs
(`DYNAMIC_DB/logs/dynamic_db/dynamic_db.log` and `.jsonl`) hold the same
information in time order, for "what else was happening around this time"
rather than "what happened in this one run."

---

## Testing

- `DYNAMIC_DB/tests/` — 170 tests, entirely offline except a handful marked
  `database` (introspection against the real schema). Covers fingerprints
  (first run, unchanged, changed, deterministic, row-count-is-not-a-change),
  the deterministic safety/contract gate, graph routing and bounded retries,
  model routing and the LLM parameter table, the bootstrap gate (failure,
  success, thread-safety of live status, secret redaction), and the static
  audit.
- `backend/tests/test_dynamic_db_contract.py` — 7 tests against the **real**
  database, through the **real** `app.dynamic_client` interface: a normal day
  costs zero model calls, a column change recompiles only the capability that
  reads it, a real recompile goes all the way through authoring → validation
  → execution → verification → promotion, a rejected candidate never
  disturbs the known-good artifact, a corrupted artifact file is rejected
  rather than trusted, and no file outside the two designated modules imports
  `dynamic_db` directly.
- The rest of `backend/tests/` (316 tests) is the pre-existing Daily Report
  suite, entirely unchanged in what it asserts, passing unmodified against
  the new wiring.

Total: 493 tests, all passing, zero removed.

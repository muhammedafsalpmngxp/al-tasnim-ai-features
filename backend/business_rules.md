# BUSINESS RULES — PDO / AL TASNIM WELL PROJECT

Authoritative business interpretation. Follow it exactly. Do not infer, modify, or invent a
definition. If a question needs a rule that is not stated below, say the rule is **not yet
defined** — do not assume one.

**This file is general and organisational** — the PDO/Al Tasnim relationship, well lifecycle
dates, milestone deadlines, schedule variance, PMS weightage — and applies to any tool that
touches a well, not only the Daily Morning Brief. Rules specific to classifying and
presenting one day's `task_daily` entries — quantity status, UOM, progress, activity/WBS/crew
mapping, data quality — live in the sibling file `daily_report_rules.md` instead, so they can
change independently of the rules here.

## 1. Parties and scope

| Term | Meaning |
|---|---|
| **PDO** | Petroleum Development Oman — the client. Provides the pegging sheet and the FLAF; performs drilling. |
| **Al Tasnim** | The contractor working for PDO. Performs Location Construction, Flowline Construction and hook-up. |
| **Well** | The central entity. Everything below is per well. |

Each well has **two construction projects**: **Location Construction** and **Flowline
Construction**. They are separate — never merge or substitute one for the other.

## 2. Column dictionary

### Well dates — all on `well.well_master`

Use these exact names; never a synonym.

| Business term | Column | Kind |
|---|---|---|
| Expected / master rig-on date | `ex_rig_on_date` | expected |
| Actual rig-on date | `rig_on_date` | actual |
| Expected / predicted rig-off date | `ex_rig_off_date` | expected |
| Actual rig-off date | `rig_off_date` | actual |
| Pegging sheet date | `pegged_date` | actual |
| FLAF date | `flaf_issue_date` | actual |
| Hook-up completion = **well completion date** | `eng_completion_date` | actual |

`ex_rig_on_date` is the **master date** the whole schedule is measured against: all construction
must finish in time for the rig to come on the well.

### Employee nationality — `ref.employee.nationality_type`

| Value | Meaning |
|---|---|
| `'National'` | Omani employee |
| `'Expat'` | non-Omani employee |
| `NULL` | not recorded — **not** an Expat |

Exact, case-sensitive strings: no `'Omani'`, `'Local'` or numeric code exists. NULL is a real third
group, so an Omani-vs-non-Omani answer must report all three or say how many are unrecorded — never
treat the remainder of a `'National'` count as Expat.

## 3. Milestone deadlines

Every deadline is derived from a date in §2. For a milestone that **has** an actual date column,
"missed" = the actual date is later than the deadline, or the actual date is still NULL once the
deadline has passed. The two construction milestones have no actual date — see the rig rule below.

| Milestone | Owner | Actual date recorded in | Deadline |
|---|---|---|---|
| Pegging sheet issued | PDO | `pegged_date` | `ex_rig_on_date - 60 days` |
| FLAF issued | PDO | `flaf_issue_date` | `ex_rig_on_date - 90 days` |
| Location Construction complete | Al Tasnim | *computed, not stored* | `ex_rig_on_date - 1 day` |
| Flowline Construction complete | Al Tasnim | *computed, not stored* | `ex_rig_on_date - 1 day` |
| Hook-up complete | Al Tasnim | `eng_completion_date` | `rig_off_date + 2 days` |

**Construction deadlines are COMPUTED from `ex_rig_on_date` — never read from a stored column.**
Derive them as `ex_rig_on_date - 1 day`. Do **NOT** use `loc_start_date` or `loc_finish_date` as a
Location Construction completion date; they are not the approved source for this rule.

**Construction missed its deadline** — there is no actual completion date, so judge it from the
rig: the deadline has passed and the rig has still not come on the well.

```
missed  ⇔  CAST(GETDATE() AS date) > DATEADD(day, -1, ex_rig_on_date)
           AND rig_on_date IS NULL
```

If `rig_on_date` is populated the rig came on, and the construction deadline is **not** treated as
missed. This rule therefore identifies wells still waiting for a rig past their deadline; it does
not detect a construction delay on a well whose rig has already arrived.

**Hook-up deadline — planned vs actual.** Before the rig is off, the planned deadline is
`ex_rig_off_date + 2 days`. Once `rig_off_date` is populated, the actual deadline is
`rig_off_date + 2 days`, and **the actual date takes precedence**.

## 4. Schedule variance — being EARLY is not an anomaly

An expected date and an actual date differing is **normal**. A difference is never, on its own, a
data error, a suspicious value, or an anomaly to flag. Read the **direction**:

| Comparison | Meaning | Report it as |
|---|---|---|
| actual **earlier than** expected | the work finished sooner than planned | **ahead of schedule — accelerated.** A GOOD outcome. |
| actual **equal to** expected | on the planned date | on schedule |
| actual **later than** expected | the work finished after the planned date | behind schedule — delayed |

This applies to **both** rig dates:

* `rig_on_date` earlier than `ex_rig_on_date` → construction finished early and the rig came on
  ahead of the master date. The well is **accelerating**, not anomalous.
* `rig_off_date` earlier than `ex_rig_off_date` → drilling finished early. Again ahead of schedule,
  not a wrong date.

One signed measure, so the sign always carries the meaning:

```
schedule_variance_days = DATEDIFF(day, ex_rig_on_date, rig_on_date)
    negative → AHEAD of schedule (accelerated)
    zero     → on schedule
    positive → BEHIND schedule (delayed)
```

(Use `ex_rig_off_date` / `rig_off_date` for the rig-off variance.)

⚠ NEVER describe an early actual date as a delay, a variance problem, a data-quality issue or an
anomaly, and never take its absolute value and call it "days of delay". Only a **later** actual
date is a delay. When a well is early, say so plainly as good news.

This section is about actual-vs-expected variance only. It does not change the milestone deadline
rules in §3.

## 5. Delay consequences — ownership matters

| Delay | Consequence |
|---|---|
| **Location Construction** late | Can cause a well/rig delay, and may lead to a **penalty** for Al Tasnim. |
| **Flowline Construction** late | Can cause a well/rig delay, but per the business rule this is **not a due well for Al Tasnim**, because the originating issue is from PDO. |

Never report a delay as Al Tasnim's without applying this distinction.

## 6. Lifecycle order

```
PDO issues pegging sheet  → Al Tasnim: Location Construction ┐
PDO issues FLAF           → Al Tasnim: Flowline Construction ┘
  → both complete before ex_rig_on_date
  → PDO rig-on (rig_on_date)
  → PDO drilling
  → rig-off (rig_off_date)
  → well cleaned, Christmas Tree fitted        ← "Christmas Tree" = the pipe/motor assembly
  → handover to Al Tasnim
  → Al Tasnim hook-up (eng_completion_date)
  → WELL COMPLETED
```

Drilling runs from actual rig-on to actual rig-off and is **PDO's** activity, not Al Tasnim's.

## 7. Well completion

A well is **completed** when Al Tasnim's hook-up is complete. Therefore
`eng_completion_date` **is** the well completion date, and:

```
completed  ⇔  eng_completion_date IS NOT NULL
```

A hook-up *deadline* and a *completed well* are different things — do not conflate them.

## 8. Project → WBS → Activity, and PMS weightage

Hierarchy: **Project → WBS / Activity Group → Activities**. Both Location Construction and
Flowline Construction can have their own WBS/activity structures.

```
Project
├── WBS 1  ── Activity 1, Activity 2, Activity 3
└── WBS 2  ── Activity 4, Activity 5
```

Weightage rules:

* Each WBS/activity group carries its own **PMS percentage (weightage)**. Different WBS groups can
  have different weightages — e.g. WBS 1 = 30%, WBS 2 = 20%, WBS 3 = 50%.
* Activities **within the same WBS have equal weightage**. For a WBS holding `N` activities, each
  activity's share inside that WBS is `1 / N`.
* An activity's contribution to the overall project PMS is therefore:

  ```
  activity contribution = parent WBS PMS weight × (1 / N)
  ```

* **Do not** assume activities in different WBS groups carry the same overall PMS weight — their
  contribution depends on the weight of their parent WBS.

## 9. Never interchange these pairs

| | vs | |
|---|---|---|
| Expected / master date | ⟷ | Actual date |
| `ex_rig_on_date` | ⟷ | `rig_on_date` |
| `ex_rig_off_date` | ⟷ | `rig_off_date` |
| PDO responsibility | ⟷ | Al Tasnim responsibility |
| Location Construction | ⟷ | Flowline Construction |
| Project-level PMS weightage | ⟷ | Activity-level equal weighting |
| Hook-up deadline | ⟷ | Well completion |

`ex_rig_off_date` is a planning figure. Never report it as the actual rig-off date.

## 10. Not yet defined

Answer what you can and state plainly that the rule is undefined — never invent one:

* Whether Location Construction and Flowline Construction have an **actual completion date**
  anywhere. No column is approved as that date, so never substitute one to fill the gap —
  on-time/missed is judged by the rig rule in §3 instead.

## 11. Strict instructions

* These rules are authoritative for every question about PDO, Al Tasnim, wells, Location
  Construction, Flowline Construction, pegging, FLAF, rig-on, rig-off, hook-up, well completion,
  WBS, activities and PMS.
* Do not infer, modify, or invent a business definition.
* If the database holds a value that conflicts with a business rule, **report the database value**
  and explain the business-rule interpretation separately. Never silently change a database result.

/* ============================================================================
   crew_suggestion.sql  --  CREW SUGGESTION EVIDENCE (advisory, read-only)

   Purpose
     For ONE target task (well_id, task_code, as of report_date), deterministically
     resolve everything the task AI summary needs to describe an advisory crew
     suggestion -- and nothing else. This file computes facts only; it never
     ranks by anything other than the fixed, documented rule below, and it
     never writes to the database.

   What this returns, in one row (or zero, if the target task cannot be found):
     - the target task's latest logical state as of report_date (completed,
       progress, actual_start/actual_end, current crew_id)
     - the resolved activity_id / activity_code / WBS, via the SAME mapping
       chain as daily_tasks.sql / daily_report_rules.md section 2
     - crew_suggestion_eligible + a suppression reason (human-readable) and a
       suppression CODE (NULL / 'IN_PROGRESS' / 'COMPLETED', machine-checkable),
       decided here (never by the LLM) per daily_report_rules.md "Crew
       suggestion" section
     - the current crew's personnel (crew_type, instance code, supervisor), if
       task_daily.crew_id is recorded
     - at most one CANDIDATE crew: the top-ranked crew that has completed this
       same activity on another well (the historical well need NOT be
       completed -- task-level completion is the evidence), is not showing an
       unfinished current task, and has at least one completed historical
       record; with full supporting statistics for "why this crew". Attached
       whenever the target task is not completed -- covering both a stalled,
       eligible task (a replacement candidate) and one already in progress (a
       purely informational "this crew has relevant experience, worth asking
       for feedback" mention, never a replacement). The service layer decides
       which framing applies; never attached at all once the task is
       completed, since there is nothing left to add at that point.

   Contract
     - SELECT only. No INSERT/UPDATE/DELETE/MERGE/ALTER/DROP/TRUNCATE.
     - Parameters, bound ONCE via the Params CTE below (never repeated), in
       this order:
         ?  -- report date  (date)
         ?  -- well_id      (int)
         ?  -- task_code    (nvarchar)
     - well.task_daily.well_id is varchar and can hold non-numeric junk (see
       daily_tasks.sql); every comparison goes through TRY_CONVERT(int, ...),
       never a bare comparison against well_master.well_id (int).
     - No SELECT DISTINCT shares a SELECT list with a windowed function
       (SQL Server rejects that combination in this project's experience);
       where a windowed PERCENTILE_CONT needs collapsing to one row per
       partition, a ROW_NUMBER() = 1 filter is used instead of DISTINCT.

   Advisory only
     Nothing below changes task_daily.crew_id, creates an assignment, or
     writes anything. "Suggested crew" is evidence for a human to weigh, nothing
     more -- see daily_report_rules.md "Crew suggestion" section 3.
   ============================================================================ */

WITH Params AS (
    /* Every parameter is bound exactly once here and referenced by name
       everywhere below via CROSS JOIN Params -- never repeated as another
       literal ? placeholder, so the parameter list can never drift out of
       sync with the query text as this file is edited. */
    SELECT
        CAST(? AS DATE)          AS report_date,
        CAST(? AS INT)           AS well_id,
        CAST(? AS NVARCHAR(100)) AS task_code
),

/* =========================================================================
   Activity mapping -- identical logic to daily_tasks.sql's activity_mapping
   / activity_detail CTEs (same source tables, same de-duplication rule, same
   dbo.mapping_master -> dbo.activity_master_csv chain documented in
   daily_report_rules.md section 2). Duplicated here rather than pulled in via
   an include directive, because daily_tasks.sql's remaining CTEs are scoped
   to a single ActionOn day (one report date's grain resolution); this query
   needs the task's history across many dates, which is a different shape
   entirely. The RULE is identical; only the file that expresses it differs.
   ========================================================================= */

activity_mapping AS (
    SELECT activity_id, activity_code, activity_uom
    FROM (
        SELECT CAST(mm.Activity_ID AS nvarchar(50))  AS activity_id,
               mm.New_Activity_Code                  AS activity_code,
               mm.UOM                                AS activity_uom,
               ROW_NUMBER() OVER (
                   PARTITION BY CAST(mm.Activity_ID AS nvarchar(50))
                   ORDER BY CASE WHEN mm.New_Activity_Code IS NULL THEN 1 ELSE 0 END,
                            mm.New_Activity_Code
               ) AS rn
        FROM dbo.mapping_master AS mm
    ) AS m
    WHERE m.rn = 1
),

activity_detail AS (
    SELECT activity_code, activity_description, activity_group_description, crew_code
    FROM (
        SELECT amc.activity_code,
               amc.activity_description,
               amc.activity_group_description,
               amc.crew_code,
               ROW_NUMBER() OVER (
                   PARTITION BY amc.activity_code
                   ORDER BY CASE WHEN amc.activity_group_description IS NULL THEN 1 ELSE 0 END,
                            amc.sl_no
               ) AS rn
        FROM dbo.activity_master_csv AS amc
        WHERE amc.activity_code IS NOT NULL
    ) AS d
    WHERE d.rn = 1
),

/* Personnel for ONE crew instance -- identical shape to daily_tasks.sql's
   crew_personnel CTE. Joined twice below (current crew, suggested crew). */
CrewPersonnel AS (
    SELECT
        rc.crew_id,
        rct.crew_type_name,
        rc.code        AS crew_instance_code,
        sup.emp_name   AS crew_supervisor
    FROM ref.crew AS rc
    LEFT JOIN ref.crew_type AS rct ON rct.crew_type_id = rc.crew_type_id
    LEFT JOIN ref.employee AS sup ON sup.id = rc.supervisor_id
),

/* =========================================================================
   Target task: latest logical state, as of report_date, for the ONE task
   the caller is asking about.
   ========================================================================= */

TargetRows AS (
    SELECT
        td.id,
        td.ActionOn,
        td.schedule_id,
        td.task_code,
        td.completed,
        td.progress,
        td.actual_start,
        td.actual_end,
        td.crew_id,
        td.updated_at,
        p.well_id,
        ROW_NUMBER() OVER (
            PARTITION BY p.well_id, td.schedule_id, td.task_code
            ORDER BY td.ActionOn DESC, td.updated_at DESC, td.id DESC
        ) AS grain_rank
    FROM well.task_daily AS td
    CROSS JOIN Params AS p
    WHERE TRY_CONVERT(INT, td.well_id) = p.well_id
      AND td.task_code = p.task_code
      AND td.ActionOn <= p.report_date
),

TargetLogical AS (
    /* Usually exactly one row. If the task was ever rescheduled (a second
       schedule_id for the same well_id/task_code), grain_rank = 1 keeps one
       row per schedule_id -- collapse that to a single overall latest state
       below rather than assume schedule_id is stable. */
    SELECT
        tr.*,
        ROW_NUMBER() OVER (
            ORDER BY tr.ActionOn DESC, tr.updated_at DESC, tr.id DESC
        ) AS overall_rank
    FROM TargetRows tr
    WHERE tr.grain_rank = 1
),

Target AS (
    SELECT
        tl.well_id,
        tl.task_code,
        tl.schedule_id,
        tl.ActionOn,
        tl.completed,
        tl.progress,
        tl.actual_start,
        tl.actual_end,
        tl.crew_id,
        /* activity_id is the text before the FIRST '-' -- same rule as
           daily_report_rules.md section 2, applied identically here. */
        LEFT(tl.task_code, NULLIF(CHARINDEX('-', tl.task_code), 0) - 1) AS activity_id
    FROM TargetLogical tl
    WHERE tl.overall_rank = 1
),

TargetMapped AS (
    SELECT
        t.*,
        am.activity_code,
        ad.activity_group_description AS wbs,
        ad.crew_code                  AS wbs_crew_code
    FROM Target t
    LEFT JOIN activity_mapping am ON am.activity_id = t.activity_id
    LEFT JOIN activity_detail ad ON ad.activity_code = am.activity_code
),

TargetWithWell AS (
    SELECT
        t.*,
        CASE WHEN wm.eng_completion_date IS NULL THEN 'INCOMPLETE_WELL' ELSE 'COMPLETED_WELL' END AS well_status
    FROM TargetMapped t
    LEFT JOIN well.well_master wm ON wm.well_id = t.well_id
),

/* Eligibility is decided HERE, deterministically -- never by the LLM.
   A task already completed, or already showing positive recorded progress,
   is not a candidate for a crew suggestion (daily_report_rules.md, "Crew
   suggestion" section). task_daily.progress's unit is not defined anywhere
   in this project (daily_report_rules.md section 7); it is used here only as
   a > 0 / not > 0 signal, never described as a percentage downstream. */
TargetEligibility AS (
    SELECT
        t.*,
        CASE
            WHEN t.completed = 1 THEN 0
            WHEN t.progress IS NOT NULL AND t.progress > 0 THEN 0
            ELSE 1
        END AS crew_suggestion_eligible,
        CASE
            WHEN t.completed = 1 THEN 'Task already completed.'
            WHEN t.progress IS NOT NULL AND t.progress > 0 THEN 'Task is already in progress; crew suggestion suppressed.'
            ELSE NULL
        END AS crew_suggestion_suppression_reason,
        /* A machine-checkable reason code alongside the human-readable
           sentence above -- the service layer branches on this, never on the
           sentence text. NULL when eligible (not completed, not progressing).
           'IN_PROGRESS' still gets a ranked historical crew attached below
           (for an informational "ask this crew for feedback" mention, never
           a replacement suggestion); 'COMPLETED' never does. */
        CASE
            WHEN t.completed = 1 THEN 'COMPLETED'
            WHEN t.progress IS NOT NULL AND t.progress > 0 THEN 'IN_PROGRESS'
            ELSE NULL
        END AS crew_suggestion_suppression_code
    FROM TargetWithWell t
),

/* =========================================================================
   Historical evidence: crews that completed this SAME activity on ANY OTHER
   well, using only records available by report_date. The historical well
   itself need not be completed -- task-level completion is the evidence
   (daily_report_rules.md, "Crew suggestion" section).
   ========================================================================= */

HistoricalRaw AS (
    SELECT
        td.id,
        td.ActionOn,
        td.schedule_id,
        td.task_code,
        td.crew_id,
        td.actual_start,
        td.actual_end,
        TRY_CONVERT(INT, td.well_id) AS well_id,
        ROW_NUMBER() OVER (
            PARTITION BY TRY_CONVERT(INT, td.well_id), td.schedule_id, td.task_code
            ORDER BY td.ActionOn DESC, td.updated_at DESC, td.id DESC
        ) AS grain_rank
    FROM well.task_daily AS td
    CROSS JOIN Params AS p
    WHERE td.completed = 1
      AND td.ActionOn <= p.report_date
      AND TRY_CONVERT(INT, td.well_id) IS NOT NULL
      AND TRY_CONVERT(INT, td.well_id) <> p.well_id
),

HistoricalLogical AS (
    /* Latest logical state of each OTHER task -- never raw snapshot rows,
       so several daily rows for the same historical task are never counted
       as several completions. */
    SELECT
        hr.*,
        LEFT(hr.task_code, NULLIF(CHARINDEX('-', hr.task_code), 0) - 1) AS activity_id
    FROM HistoricalRaw hr
    WHERE hr.grain_rank = 1
),

HistoricalActivity AS (
    SELECT
        h.*,
        am.activity_code
    FROM HistoricalLogical h
    LEFT JOIN activity_mapping am ON am.activity_id = h.activity_id
),

HistoricalForActivity AS (
    /* Restricted to the target's own resolved activity_code. An unmapped
       target (activity_code IS NULL) yields no historical evidence here --
       this query never guesses at an activity. */
    SELECT h.*
    FROM HistoricalActivity h
    CROSS JOIN (SELECT TOP (1) activity_code FROM TargetEligibility) te
    WHERE te.activity_code IS NOT NULL
      AND h.activity_code = te.activity_code
),

HistoricalWithWell AS (
    SELECT
        h.*,
        CASE WHEN wm.eng_completion_date IS NULL THEN 1 ELSE 0 END AS historical_well_incomplete,
        CASE
            WHEN h.actual_start IS NOT NULL AND h.actual_end IS NOT NULL
            THEN DATEDIFF(DAY, h.actual_start, h.actual_end)
            ELSE NULL
        END AS completion_days
    FROM HistoricalForActivity h
    LEFT JOIN well.well_master wm ON wm.well_id = h.well_id
),

CrewHistory AS (
    SELECT
        hw.crew_id,
        COUNT(*)                                              AS historical_completed_task_count,
        COUNT(DISTINCT hw.well_id)                            AS distinct_completed_well_count,
        SUM(hw.historical_well_incomplete)                     AS completed_on_incomplete_well_count,
        SUM(CASE WHEN hw.historical_well_incomplete = 0 THEN 1 ELSE 0 END)
                                                                AS completed_on_completed_well_count,
        AVG(CAST(hw.completion_days AS DECIMAL(18, 4)))        AS average_completion_days,
        MIN(hw.completion_days)                                AS shortest_completion_days,
        MAX(hw.completion_days)                                AS longest_completion_days,
        MAX(hw.ActionOn)                                       AS most_recent_success_date
    FROM HistoricalWithWell hw
    WHERE hw.crew_id IS NOT NULL
    GROUP BY hw.crew_id
),

/* Typical (median) completion duration per crew. PERCENTILE_CONT ... OVER
   yields the same value on every row of its partition; ROW_NUMBER() = 1
   collapses that to one row per crew_id WITHOUT combining DISTINCT and a
   windowed function in the same SELECT list. */
CrewMedianRaw AS (
    SELECT
        hw.crew_id,
        PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY hw.completion_days)
            OVER (PARTITION BY hw.crew_id) AS typical_completion_days,
        ROW_NUMBER() OVER (PARTITION BY hw.crew_id ORDER BY (SELECT NULL)) AS rn
    FROM HistoricalWithWell hw
    WHERE hw.crew_id IS NOT NULL
      AND hw.completion_days IS NOT NULL
),
CrewMedian AS (
    SELECT crew_id, typical_completion_days
    FROM CrewMedianRaw
    WHERE rn = 1
),

/* =========================================================================
   Derived availability -- a conservative, V1, task-level signal ONLY.
   NEVER an authoritative physical-availability status (no such table exists
   -- daily_report_rules.md, "Crew suggestion" section). ref.employee.emp_status
   is deliberately not read anywhere in this file.
   ========================================================================= */

LatestCrewTasks AS (
    SELECT
        td.crew_id,
        td.completed,
        td.actual_end,
        TRY_CONVERT(INT, td.well_id) AS well_id,
        ROW_NUMBER() OVER (
            PARTITION BY TRY_CONVERT(INT, td.well_id), td.schedule_id, td.task_code
            ORDER BY td.ActionOn DESC, td.updated_at DESC, td.id DESC
        ) AS grain_rank
    FROM well.task_daily AS td
    CROSS JOIN Params AS p
    WHERE td.ActionOn <= p.report_date
      AND td.crew_id IS NOT NULL
),

BusyCrews AS (
    SELECT DISTINCT lct.crew_id
    FROM LatestCrewTasks lct
    INNER JOIN well.well_master wm ON wm.well_id = lct.well_id
    WHERE lct.grain_rank = 1
      AND lct.completed = 0
      AND lct.actual_end IS NULL
      AND wm.eng_completion_date IS NULL
),

/* =========================================================================
   Candidates: hard-filter out busy crews, then rank deterministically
   (daily_report_rules.md, "Crew suggestion" section -- fixed rule, never an
   ML score).
   ========================================================================= */

Candidates AS (
    SELECT
        ch.crew_id,
        ch.historical_completed_task_count,
        ch.distinct_completed_well_count,
        ch.completed_on_incomplete_well_count,
        ch.completed_on_completed_well_count,
        ch.average_completion_days,
        cm.typical_completion_days,
        ch.shortest_completion_days,
        ch.longest_completion_days,
        ch.most_recent_success_date,
        CASE
            WHEN ch.historical_completed_task_count >= 5 THEN 'STRONG_HISTORY'
            WHEN ch.historical_completed_task_count >= 2 THEN 'LIMITED_HISTORY'
            WHEN ch.historical_completed_task_count = 1 THEN 'SINGLE_HISTORY'
            ELSE 'NO_HISTORY'
        END AS evidence_strength,
        CASE WHEN bc.crew_id IS NULL THEN 'NO_CURRENT_UNFINISHED_TASK' ELSE 'BUSY' END AS derived_availability
    FROM CrewHistory ch
    LEFT JOIN CrewMedian cm ON cm.crew_id = ch.crew_id
    LEFT JOIN BusyCrews bc ON bc.crew_id = ch.crew_id
    WHERE ch.historical_completed_task_count >= 1
),

AvailableCandidates AS (
    SELECT * FROM Candidates WHERE derived_availability = 'NO_CURRENT_UNFINISHED_TASK'
),

RankedCandidates AS (
    SELECT
        ac.*,
        ROW_NUMBER() OVER (
            ORDER BY
                ac.historical_completed_task_count DESC,
                ac.distinct_completed_well_count DESC,
                ac.typical_completion_days ASC,
                ac.most_recent_success_date DESC,
                ac.crew_id ASC
        ) AS suggestion_rank
    FROM AvailableCandidates ac
)

/* =========================================================================
   Final result -- one row (or none, if the target task itself could not be
   found for this well_id/task_code/report_date).
   ========================================================================= */

SELECT
    te.well_id                              AS target_well_id,
    te.task_code                             AS target_task_code,
    te.schedule_id                           AS target_schedule_id,
    te.ActionOn                              AS target_action_date,
    te.activity_id,
    te.activity_code,
    te.wbs,
    te.wbs_crew_code,
    te.completed                             AS target_completed,
    te.progress                              AS target_progress,
    te.actual_start                          AS target_actual_start,
    te.actual_end                            AS target_actual_end,
    te.well_status,
    te.crew_suggestion_eligible,
    te.crew_suggestion_suppression_reason,
    te.crew_suggestion_suppression_code,

    te.crew_id                               AS current_crew_id,
    cur_cp.crew_type_name                    AS current_crew_type,
    cur_cp.crew_instance_code                AS current_crew_instance_code,
    cur_cp.crew_supervisor                   AS current_crew_supervisor,

    /* "Candidate", not "suggested": the same ranked crew is reused for two
       different framings downstream -- a replacement suggestion when the
       target task is stalled (eligible = 1), or a purely informational
       "ask this crew for feedback" mention when it is merely in progress
       (crew_suggestion_suppression_code = 'IN_PROGRESS'). Never attached at
       all once the task is completed. The service layer (never the LLM)
       decides which framing applies. */
    rc.crew_id                               AS candidate_crew_id,
    sug_cp.crew_type_name                    AS candidate_crew_type,
    sug_cp.crew_instance_code                AS candidate_crew_instance_code,
    sug_cp.crew_supervisor                   AS candidate_crew_supervisor,

    rc.historical_completed_task_count,
    rc.distinct_completed_well_count,
    rc.completed_on_incomplete_well_count,
    rc.completed_on_completed_well_count,
    rc.typical_completion_days,
    rc.average_completion_days,
    rc.shortest_completion_days,
    rc.longest_completion_days,
    rc.most_recent_success_date,
    rc.evidence_strength,
    rc.derived_availability,

    /* Lets the service explain *why* no crew was suggested, without another
       query: zero history for this activity vs. history that exists but every
       such crew is currently showing an unfinished task. */
    (SELECT COUNT(*) FROM Candidates)          AS historical_crew_candidate_count,
    (SELECT COUNT(*) FROM AvailableCandidates) AS available_crew_candidate_count

FROM TargetEligibility te
LEFT JOIN RankedCandidates rc
       ON ISNULL(te.completed, 0) = 0
      AND rc.suggestion_rank = 1
LEFT JOIN CrewPersonnel cur_cp ON cur_cp.crew_id = te.crew_id
LEFT JOIN CrewPersonnel sug_cp ON sug_cp.crew_id = rc.crew_id;

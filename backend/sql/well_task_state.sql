/* ============================================================================
   well_task_state.sql  --  LATEST LOGICAL TASK STATE PER WELL (base evidence)

   Purpose
     Resolve, for every LIVE well, the latest logical state of each of its
     tasks AS OF a report date. This is the base evidence behind the front
     page's task-activity figures (incomplete tasks, ongoing tasks, last task
     date) and behind the well-scoped AI summary's task_activity block.

     It is the well-lifetime counterpart of daily_tasks.sql: that file resolves
     one row per logical task on ONE ActionOn date; this one resolves one row
     per logical task across every date up to and including the report date.
     Both use the same well universe, the same TRY_CONVERT protection and the
     same "latest row wins" ranking discipline; they answer different
     questions, so they are separate files rather than one file with a flag.

   This file is included verbatim by well_task_activity.sql and
   well_task_activity_detail.sql through the {{include:...}} directive handled
   by app/utils/sql_loader.py, so the two can never disagree about what a
   well's task state is.

   Contract
     - SELECT only. No INSERT/UPDATE/DELETE/MERGE/ALTER/DROP/TRUNCATE.
     - Parameter 1 (?) : report date (date). Bound ONCE, in the params CTE
       below, and referenced by name everywhere else -- including by the files
       that include this one -- so the parameter list can never drift.
     - Emits raw state evidence only. It counts nothing and classifies no
       quantity; the counts are aggregated in well_task_activity.sql and in
       app/services/well_activity_service.py.

   Well universe
     well.well_master with eng_completion_date IS NULL is the AUTHORITATIVE
     universe (business_rules.md section 7). A task row never creates a well:
     the INNER JOIN below runs from well_master to task_daily, so a
     task_daily row whose well_id does not resolve to a live well simply has
     nowhere to attach.

     well.task_daily.well_id is varchar and holds non-numeric junk on a
     minority of rows ('0000F', '0000I', '0000J', blank) -- see
     daily_tasks.sql for the full history. Every comparison goes through
     TRY_CONVERT(int, td.well_id), which yields NULL rather than raising
     SQLSTATE 22018; the INNER JOIN then excludes it, exactly as the
     well_id <= 1 rule already excludes invalid task records.

   Logical task grain and "latest state"
     A task is (well_id, schedule_id, task_code). Its state as of the report
     date is the row that ranks first under:
         ActionOn   DESC   -- the most recent day this task was reported on
         updated_at DESC   -- then the most recently updated row
         id         DESC   -- final tie-break
     restricted to ActionOn <= report_date, so a row dated after the report
     date can never influence an older report date's answer. This is the same
     ranking sql/crew_suggestion.sql already uses for a crew's latest task
     state (daily_report_rules.md section 8, rule 9).

   Task state classification -- deterministic, and never from progress
     task_daily.progress is NOT read anywhere in this file. Its unit is
     undefined (daily_report_rules.md section 7): observed values run from
     -0.05 to 66.7, so it is not a percentage and cannot say whether a task
     is finished or under way. The state comes from the task's own recorded
     state columns instead:

       COMPLETED            completed = 1
       ENDED_NOT_COMPLETED  not completed, but an actual_end is recorded
       ONGOING              not completed, actual_start recorded, no actual_end
       NOT_STARTED          not completed, no actual_start, no actual_end

     The four are mutually exclusive and cover every row, so a count over them
     can never double-count or lose a task. ONGOING is deliberately the
     narrow, three-part definition -- completed = 0 AND actual_start IS NOT
     NULL AND actual_end IS NULL -- because "no actual_end" on its own also
     matches a task that has never started, and planned startDate/endDate are
     a schedule, not proof that work is physically under way, so neither is
     used here.
   ============================================================================ */

WITH params AS (
    /* The report date is bound exactly once, here, and referenced as
       p.report_date everywhere below -- including in the files that include
       this one, which add no ? placeholder of their own. */
    SELECT CAST(? AS date) AS report_date
),

live_wells AS (
    /* A live well is defined ONLY by eng_completion_date IS NULL.
       status_id / progress / flowline_const_status_id are NOT the definition. */
    SELECT wm.well_id
    FROM well.well_master AS wm
    WHERE wm.eng_completion_date IS NULL
),

well_task_rows AS (
    SELECT td.id,
           td.ActionOn,
           td.schedule_id,
           td.task_code,
           TRY_CONVERT(int, td.well_id) AS well_id,
           td.completed,
           td.actual_start,
           td.actual_end,
           td.updated_at
    FROM well.task_daily AS td
    CROSS JOIN params AS p
    INNER JOIN live_wells AS lw
            ON lw.well_id = TRY_CONVERT(int, td.well_id)
    WHERE td.ActionOn <= p.report_date        -- never a future row
      AND TRY_CONVERT(int, td.well_id) > 1    -- invalid task records excluded
),

well_task_ranked AS (
    SELECT r.*,
           ROW_NUMBER() OVER (
               PARTITION BY r.well_id, r.schedule_id, r.task_code
               ORDER BY r.ActionOn DESC, r.updated_at DESC, r.id DESC
           ) AS state_rank
    FROM well_task_rows AS r
),

logical_task_state AS (
    SELECT s.well_id,
           s.schedule_id,
           s.task_code,
           /* The latest date this task appeared in the daily records, on or
              before the report date. It is an ACTIVITY date, not a proof of
              physical completion -- see well_task_activity.sql. */
           s.ActionOn      AS latest_action_on,
           s.completed,
           s.actual_start,
           s.actual_end,
           CASE
               WHEN s.completed = 1              THEN 'COMPLETED'
               WHEN s.actual_end IS NOT NULL     THEN 'ENDED_NOT_COMPLETED'
               WHEN s.actual_start IS NOT NULL   THEN 'ONGOING'
               ELSE 'NOT_STARTED'
           END AS task_state
    FROM well_task_ranked AS s
    WHERE s.state_rank = 1
)

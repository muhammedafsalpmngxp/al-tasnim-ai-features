/* ============================================================================
   well_task_activity.sql  --  PER-WELL TASK ACTIVITY (front-page metrics)

   One row per LIVE well that has task evidence as of the report date, with
   the task-state counts and the last task date the front page shows on the
   well's own row.

   The counts are deliberately DISJOINT where the front page shows them side
   by side:

       open_task_count  =  incomplete_task_count  +  ongoing_task_count
       logical_task_count = open_task_count + completed_task_count

   "Incomplete" therefore means open but not ongoing -- a task with no
   recorded actual start, or one with an actual end recorded that was never
   marked completed. An earlier version had incomplete include the ongoing
   tasks, so the two numbers on a well's row overlapped and could not be
   added: a well reading "48 incomplete, 24 ongoing" had 48 open tasks, not
   72, and nothing on the row said so.

   Every figure here is aggregated in SQL: React receives counts, never rows
   to count for itself, and the LLM receives the same counts, never a list to
   derive them from.

   A well with no task_daily row at or before the report date returns no row
   at all -- there is no task activity to report for it, and inventing a row
   of zeroes would claim the well was measured when it was not. The daily
   dataset still lists any well that reported a task on the date itself, so
   nothing reachable today can drop off the front page.

   last_task_date
     MAX of each task's latest ActionOn, which is the same as the maximum
     ActionOn over all of the well's rows on or before the report date,
     because the latest row per logical task is selected by ActionOn DESC
     first. It means: the latest date on which this well appeared in the
     task-daily data. It is NOT a physical completion date and is never
     labelled one -- no column in this schema is approved as a construction
     actual completion date (business_rules.md section 10).

   today_reported_task_count is deliberately NOT computed here. The count of
   logical tasks reported ON the selected date is already resolved, at the
   daily grain, by sql/daily_tasks.sql (one ranked row per (well_id,
   schedule_id, task_code, ActionOn)); app/services/well_activity_service.py
   reads it from that same already-loaded dataset rather than re-deriving it
   with a second, subtly different rule here. One rule, one place.

   Contract
     - SELECT only.
     - Parameter 1 (?) : report date (date) -- bound in well_task_state.sql's
       params CTE, never repeated here.
   ============================================================================ */

{{include:well_task_state}}

SELECT s.well_id,
       COUNT(*)                                                                 AS logical_task_count,
       SUM(CASE WHEN s.task_state =  'COMPLETED' THEN 1 ELSE 0 END)             AS completed_task_count,
       /* Every task not recorded as completed. The two figures below split
          this in two without overlapping, so open = incomplete + ongoing and
          the arithmetic on a well's row adds up as it reads. */
       SUM(CASE WHEN s.task_state <> 'COMPLETED' THEN 1 ELSE 0 END)             AS open_task_count,
       /* Open, but NOT ongoing: no actual start recorded, or an actual end
          recorded without completion. An ongoing task is never counted here
          as well -- counting it twice would make the two figures overlap and
          stop them summing to the total beside them. */
       SUM(CASE WHEN s.task_state IN ('NOT_STARTED', 'ENDED_NOT_COMPLETED')
                THEN 1 ELSE 0 END)                                              AS incomplete_task_count,
       SUM(CASE WHEN s.task_state =  'ONGOING' THEN 1 ELSE 0 END)               AS ongoing_task_count,
       SUM(CASE WHEN s.task_state =  'NOT_STARTED' THEN 1 ELSE 0 END)           AS not_started_task_count,
       SUM(CASE WHEN s.task_state =  'ENDED_NOT_COMPLETED' THEN 1 ELSE 0 END)   AS ended_not_completed_task_count,
       MAX(s.latest_action_on)                                                  AS last_task_date
FROM logical_task_state AS s
GROUP BY s.well_id
ORDER BY s.well_id;

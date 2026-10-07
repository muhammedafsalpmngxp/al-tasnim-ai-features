/* ============================================================================
   daily_activity_dates.sql  --  RECENT DATES THAT CARRY DAILY ENTRIES

   Purpose
     The date picker's list: the most recent report dates on which a live well
     actually reported an actual quantity, newest first, with how many raw rows
     and how many wells each holds.

   Why this is a capability rather than a string inside a repository
     It reads the same physical objects every other capability does, so it goes
     stale for exactly the same reasons and must be re-compiled by exactly the
     same pipeline. A query living as a literal inside Python would be the one
     place in this application a schema change could still break silently.

   Business rules applied
     - A live well is defined ONLY by the well-completion rule
       (business_rules.md section 7). status_id / progress /
       flowline_const_status_id are NOT the definition.
     - Task rows with well_id <= 1, or with a non-numeric well_id, are excluded
       (daily_report_rules.md section 1). well.task_daily.well_id is varchar
       and holds non-numeric junk on a minority of rows; a bare comparison
       against the int well_master.well_id raises SQLSTATE 22018 and takes the
       whole report down, while TRY_CONVERT yields NULL and the INNER JOIN
       excludes it exactly as the well_id <= 1 rule already does.
     - "Carries a daily entry" means an actual quantity is present in the daily
       payload, tested for validity BEFORE it is read.

   Contract
     - SELECT only.
     - Parameter 1 (?) : how many dates to return (int), always bound.
   ============================================================================ */

SELECT TOP (?) td.ActionOn                                  AS report_date,
       COUNT(*)                                             AS row_count,
       COUNT(DISTINCT TRY_CONVERT(int, td.well_id))         AS well_count
FROM well.task_daily AS td
INNER JOIN well.well_master AS wm
        ON wm.well_id = TRY_CONVERT(int, td.well_id)
       AND wm.eng_completion_date IS NULL
WHERE TRY_CONVERT(int, td.well_id) > 1
  AND td.daily_data IS NOT NULL
  AND ISJSON(td.daily_data) = 1
  AND JSON_VALUE(td.daily_data, '$.actual_quantity') IS NOT NULL
GROUP BY td.ActionOn
ORDER BY td.ActionOn DESC;

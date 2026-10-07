/* ============================================================================
   well_task_activity_detail.sql  --  THE TASKS BEHIND THE FRONT-PAGE COUNTS

   One row per INCOMPLETE logical task (any state other than COMPLETED), for
   every live well, as of the report date -- the rows the front page's
   Incomplete / Ongoing figures are counts of, and the rows the well-scoped AI
   evidence lists its ongoing tasks from.

   Why only the incomplete ones: this query exists to make a count auditable,
   and every count it backs is a count of not-completed tasks. A completed
   task is already represented in completed_task_count on the well's own row
   (well_task_activity.sql) and has nothing further to show here.

   Loaded lazily, once per report date, for EVERY well at once -- never one
   query per well. Expanding a second well's detail re-uses the same cached
   result (app/services/well_activity_service.py).

   The mapping chain is the same one daily_tasks.sql / daily_report_rules.md
   section 2 define -- task_code -> activity_id -> mapping_master ->
   activity_master_csv -- resolved with LEFT JOINs at every hop so an unmapped
   task stays visible rather than disappearing from its own well's list.

   Contract
     - SELECT only.
     - Parameter 1 (?) : report date (date) -- bound in well_task_state.sql's
       params CTE, never repeated here.
   ============================================================================ */

{{include:well_task_state}}

, activity_mapping AS (
    /* dbo.mapping_master, de-duplicated to one row per activity_id, exactly
       as in daily_tasks.sql. Activity_ID is stored as TEXT and must be CAST
       to nvarchar before comparing. */
    SELECT activity_id, activity_code
    FROM (
        SELECT CAST(mm.Activity_ID AS nvarchar(50))  AS activity_id,
               mm.New_Activity_Code                  AS activity_code,
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
    /* WBS is ONLY activity_group_description. Crew is not read here: this
       view is about task state, and crew evidence already has its own places
       (daily task detail, crew suggestion). */
    SELECT activity_code, activity_description, activity_group_description
    FROM (
        SELECT amc.activity_code,
               amc.activity_description,
               amc.activity_group_description,
               ROW_NUMBER() OVER (
                   PARTITION BY amc.activity_code
                   ORDER BY CASE WHEN amc.activity_group_description IS NULL THEN 1 ELSE 0 END,
                            amc.sl_no
               ) AS rn
        FROM dbo.activity_master_csv AS amc
        WHERE amc.activity_code IS NOT NULL
    ) AS d
    WHERE d.rn = 1
)

SELECT s.well_id,
       s.schedule_id,
       s.task_code,
       s.task_state,
       s.latest_action_on,
       s.actual_start,
       s.actual_end,
       s.completed,
       LEFT(s.task_code, NULLIF(CHARINDEX('-', s.task_code), 0) - 1) AS activity_id,
       am.activity_code,
       ad.activity_description,
       ad.activity_group_description AS wbs
FROM logical_task_state AS s
LEFT JOIN activity_mapping AS am
       ON am.activity_id = LEFT(s.task_code, NULLIF(CHARINDEX('-', s.task_code), 0) - 1)
LEFT JOIN activity_detail AS ad
       ON ad.activity_code = am.activity_code
WHERE s.task_state <> 'COMPLETED'
/* schedule_id is part of the ordering, not decoration: one task_code can
   appear under two schedule_ids on the same well, and without this last key
   SQL Server is free to return that pair either way round. A sample of these
   rows goes into the AI evidence, whose SHA-256 is the explanation cache's
   key -- an unstable row order would hash differently on every request and
   quietly turn every cache hit into a fresh, paid-for LLM call. Caught
   exactly that way: the same well, explained twice, missed its own cache. */
ORDER BY s.well_id, s.task_state, s.task_code, s.schedule_id;

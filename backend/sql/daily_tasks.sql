/* ============================================================================
   daily_tasks.sql  --  BASE DAILY EVIDENCE (row-grain resolution)

   Purpose
     Return exactly ONE authoritative row per logical daily task
     (well_id, schedule_id, task_code, ActionOn) for a single report date,
     restricted to LIVE wells, together with the mapping chain and the
     data-quality flags needed downstream.

   This file is the ONLY place where the daily row-grain strategy lives
   (see README "Daily row-grain resolution"). It is included verbatim by
   daily_detail.sql and daily_summary.sql through the include directive handled
   by app/utils/sql_loader.py, so the datasets can never diverge.

<<<<<<< HEAD
=======
   Also emits, as raw evidence only (crew_match_status / start_date_status /
   end_date_status classification is owned by validation_service.py, same as
   quantity_status):
     - crew_type_code: the crew TYPE actually assigned to the task (via
       task_daily.crew_id -> ref.crew -> ref.crew_type), compared against
       crew_code above by validation_service.classify_crew_match.
     - planned_start / planned_end (task_daily.startDate / .endDate) and
       actual_start / actual_end, compared by validation_service.classify_date.
       See daily_report_rules.md "Crew match" and "Date validation" sections.

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
   Contract
     - SELECT only. No INSERT/UPDATE/DELETE/MERGE/ALTER/DROP/TRUNCATE.
     - Parameter 1 (?) : report date (date)
     - Emits raw evidence only. It does NOT classify quantity status;
       classification is owned by app/services/validation_service.py so the
       rule exists exactly once in the codebase.

   Row-grain strategy (deterministic, documented, isolated)
     1. Partition raw rows by (well_id, schedule_id, task_code, ActionOn).
     2. Order within the partition by:
          is_actual_entry DESC   -- an actual-entry row outranks a planning snapshot
          updated_at      DESC   -- then the most recently updated row
          id              DESC   -- then the highest id (final tie-break)
     3. Keep grain_rank = 1.
     4. Carry group_row_count and group_actual_entry_count forward so duplicate
        / multi-snapshot conditions stay VISIBLE instead of silently collapsed.
        group_actual_entry_count > 1 is surfaced as the DUPLICATE_ACTUAL_ENTRY
        data-quality condition.

     Rationale: DISTINCT would merge genuinely different planning snapshots,
     MAX(id) alone could prefer a planning snapshot over the actual entry, and
     SUM over every row would double count. Selecting one ranked representative
     per logical task avoids all three.
   ============================================================================ */

WITH live_wells AS (
    /* A live well is defined ONLY by eng_completion_date IS NULL.
       status_id / progress / flowline_const_status_id are NOT the definition. */
    SELECT wm.well_id
    FROM well.well_master AS wm
    WHERE wm.eng_completion_date IS NULL
),

activity_mapping AS (
    /* dbo.mapping_master is the current source, NOT dbo.activity_master_mapping.
       The old table is superseded and resolves only a minority of activity_ids
       seen in task_daily (measured: 278/931 = ~30% overall, and 0/130 WBS
       resolutions on a sample date -- its activity_code values no longer line
       up with activity_master_csv at all). mapping_master.New_Activity_Code
       plays activity_master_mapping.activity_code's former role feeding
       activity_master_csv below (measured: 432/931 activities, 93/130 WBS
       resolutions on the same sample date).

       mapping_master.Activity_ID is stored as TEXT; CAST to nvarchar before
       comparing -- SQL Server cannot compare TEXT with = directly.

       mapping_master has no equivalent of the old table's project_type,
       composition_code or class_b_ptw columns, and its own New_Crew_code is
       NOT used here: crew is ONLY activity_master_csv.crew_code (see
       activity_detail below), never backfilled from another table.

       mapping_master.UOM is connected here as a REFERENCE value only -- the
       unit of measure the activity master expects, distinct from uom_code
       (the unit actually recorded on this daily row, from ref.uom via
       task_daily.uom_id, which stays the sole authoritative UOM per the
       business rule). It is carried through as activity_uom for traceability:
       measured, task_daily.uom_id is NULL/unmapped on a large share of rows
       (394 of 740 on a sample date) where the activity master's UOM is often
       still available, so this fills a real evidence gap without redefining
       what "the UOM" is. It is never used to fill in a missing uom_code, to
       group tasks, or to compute a quantity -- doing any of those would be a
       conversion/substitution rule this project does not have.

       De-duplicated to one row per activity_id, same fan-out reason as
       before: several activity_ids repeat in mapping_master (e.g. ENG1130,
       ENG1120 each appear 4 times). Rows carrying a real New_Activity_Code
       are preferred. */
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
    /* De-duplicated to one row per activity_code, for the same fan-out reason.
       WBS  is ONLY activity_group_description.
       Crew is ONLY crew_code. */
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

crew_personnel AS (
    /* Personnel evidence for the SPECIFIC crew instance assigned to a task --
       distinct from activity_master_csv.crew_code (the business-rule WBS crew
       label used in activity_detail above). task_daily.crew_id / crew_type_id
       resolve cleanly to ref.crew / ref.crew_type (measured: 100% match rate
       against the last 30 days of task_daily rows that carry them), giving the
       real supervisor and team behind the task rather than just a code.

       Never used to redefine WBS crew: crew_code above stays the sole crew per
       business rule; these columns are additive "who actually worked it"
       evidence, surfaced the same way activity_uom is -- alongside, never in
<<<<<<< HEAD
       place of, the authoritative field. */
    SELECT rc.crew_id,
           rct.crew_type_name,
=======
       place of, the authoritative field.

       crew_type_code is the ONE exception: ref.crew_type.crew_type_code and
       activity_master_csv.crew_code are the SAME code space (measured: on rows
       that carry a resolvable crew, 22,947 of 23,314 -- 98.4% -- match exactly,
       byte for byte, no hyphen/case normalisation needed to get that rate).
       That is deliberately used for exactly one comparison -- crew_match_status,
       daily_report_rules.md "Crew match" section -- and for nothing else; it
       still never redefines what "the WBS crew" is. task_daily.planned_crew was
       investigated as a more direct "assigned crew" candidate and rejected: it
       is NULL on all 110,184 rows measured, i.e. never populated. */
    SELECT rc.crew_id,
           rct.crew_type_name,
           rct.crew_type_code,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
           rc.code                                  AS crew_instance_code,
           sup.emp_name                              AS crew_supervisor,
           (
               SELECT STRING_AGG(emp.emp_name, ', ')
               FROM bridge.crew_employee AS ce
               INNER JOIN ref.employee AS emp ON emp.id = ce.employee_id
               WHERE ce.crew_id = rc.crew_id
           )                                          AS crew_employee_names
    FROM ref.crew AS rc
    LEFT JOIN ref.crew_type AS rct ON rct.crew_type_id = rc.crew_type_id
    LEFT JOIN ref.employee AS sup ON sup.id = rc.supervisor_id
),

raw_daily AS (
    /* well.task_daily.well_id is stored as varchar (a schema drift from the
       int column it used to be -- well.well_master.well_id is still int).
       A minority of rows carry non-numeric junk in it (observed: '0000F',
       '0000I', '0000J', NULL), which a bare comparison/JOIN against an int
       would throw SQLSTATE 22018 on ("Conversion failed... converting the
       varchar value '0000F' to data type int"), taking the whole report down
       with it. TRY_CONVERT never throws: a non-numeric well_id becomes NULL,
       which the INNER JOIN below naturally excludes -- the same outcome the
       well_id <= 1 rule already gives invalid task records, extended to this
       new class of invalid value rather than crashing on it. */
    SELECT td.id,
           td.ActionOn,
           td.task_code,
           td.schedule_id,
           TRY_CONVERT(int, td.well_id) AS well_id,
           td.planned,
           td.progress,
           td.uom_id,
           td.crew_id,
           td.crew_type_id,
<<<<<<< HEAD
=======
           td.startDate                AS planned_start,
           td.endDate                  AS planned_end,
           td.actual_start,
           td.actual_end,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
           td.daily_completed          AS daily_completed_col,
           td.daily_ph_name            AS ph_name_col,
           td.updated_at,
           /* JSON validity must be tested before any JSON_VALUE extraction. */
           CASE WHEN td.daily_data IS NOT NULL AND ISJSON(td.daily_data) = 1
                THEN 1 ELSE 0 END      AS daily_data_json_valid,
           CASE WHEN td.daily_data IS NOT NULL AND ISJSON(td.daily_data) = 0
                THEN 1 ELSE 0 END      AS daily_data_json_invalid,
           CASE WHEN td.daily_data IS NOT NULL AND ISJSON(td.daily_data) = 1
                THEN JSON_VALUE(td.daily_data, '$.actual_quantity') END
                                       AS actual_quantity_raw,
           CASE WHEN td.daily_data IS NOT NULL AND ISJSON(td.daily_data) = 1
                THEN TRY_CONVERT(DECIMAL(18, 4),
                                 JSON_VALUE(td.daily_data, '$.actual_quantity')) END
                                       AS actual_quantity,
           CASE WHEN td.daily_data IS NOT NULL AND ISJSON(td.daily_data) = 1
                THEN JSON_VALUE(td.daily_data, '$.ph_name') END
                                       AS ph_name_json,
           CASE WHEN td.daily_data IS NOT NULL AND ISJSON(td.daily_data) = 1
                THEN JSON_VALUE(td.daily_data, '$.completed') END
                                       AS daily_completed_json
    FROM well.task_daily AS td
    INNER JOIN live_wells AS lw
            ON lw.well_id = TRY_CONVERT(int, td.well_id)
    WHERE td.ActionOn = ?                         -- report date, always parameterised
      AND TRY_CONVERT(int, td.well_id) > 1        -- invalid task records excluded
),

ranked_daily AS (
    SELECT rd.*,
           CASE WHEN rd.actual_quantity IS NOT NULL THEN 1 ELSE 0 END AS is_actual_entry,
           /* actual_quantity present in the JSON but not convertible to a number */
           CASE WHEN rd.actual_quantity_raw IS NOT NULL
                     AND rd.actual_quantity IS NULL
                THEN 1 ELSE 0 END AS actual_quantity_unparseable
    FROM raw_daily AS rd
),

resolved_daily AS (
    SELECT rk.*,
           COUNT(*) OVER (
               PARTITION BY rk.well_id, rk.schedule_id, rk.task_code, rk.ActionOn
           ) AS group_row_count,
           SUM(rk.is_actual_entry) OVER (
               PARTITION BY rk.well_id, rk.schedule_id, rk.task_code, rk.ActionOn
           ) AS group_actual_entry_count,
           ROW_NUMBER() OVER (
               PARTITION BY rk.well_id, rk.schedule_id, rk.task_code, rk.ActionOn
               ORDER BY rk.is_actual_entry DESC, rk.updated_at DESC, rk.id DESC
           ) AS grain_rank
    FROM ranked_daily AS rk
),

daily_tasks AS (
    SELECT r.id                                AS task_daily_id,
           r.well_id,
           r.ActionOn                          AS action_on,
           r.schedule_id,
           r.task_code,
           /* activity_id is the text before the FIRST '-', extracted NULL-safely. */
           LEFT(r.task_code, NULLIF(CHARINDEX('-', r.task_code), 0) - 1) AS activity_id,
           am.activity_code,
           am.activity_uom,
           ad.activity_description,
           ad.activity_group_description       AS wbs,
           ad.crew_code,
           r.uom_id,
           u.uom_code,
           r.crew_id,
           r.crew_type_id,
           cp.crew_type_name,
<<<<<<< HEAD
           cp.crew_instance_code,
           cp.crew_supervisor,
           cp.crew_employee_names,
=======
           cp.crew_type_code,
           cp.crew_instance_code,
           cp.crew_supervisor,
           cp.crew_employee_names,
           r.planned_start,
           r.planned_end,
           r.actual_start,
           r.actual_end,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
           r.planned,
           r.progress,
           r.actual_quantity,
           r.actual_quantity_raw,
           /* the daily entry's own completed flag; JSON first, stored column as fallback */
           COALESCE(CASE WHEN r.daily_completed_json = 'true'  THEN CAST(1 AS bit)
                         WHEN r.daily_completed_json = 'false' THEN CAST(0 AS bit) END,
                    r.daily_completed_col)     AS daily_completed,
           COALESCE(NULLIF(LTRIM(RTRIM(r.ph_name_json)), ''),
                    NULLIF(LTRIM(RTRIM(r.ph_name_col)), '')) AS ph_name,
           r.is_actual_entry,
           r.daily_data_json_valid,
           r.daily_data_json_invalid,
           r.actual_quantity_unparseable,
           r.group_row_count,
           r.group_actual_entry_count
    FROM resolved_daily AS r
    /* LEFT JOIN throughout: unmapped work must stay visible, never dropped. */
    LEFT JOIN activity_mapping AS am
           ON am.activity_id = LEFT(r.task_code, NULLIF(CHARINDEX('-', r.task_code), 0) - 1)
    LEFT JOIN activity_detail AS ad
           ON ad.activity_code = am.activity_code
    LEFT JOIN ref.uom AS u
           ON u.uom_id = r.uom_id
    LEFT JOIN crew_personnel AS cp
           ON cp.crew_id = r.crew_id
    WHERE r.grain_rank = 1
)

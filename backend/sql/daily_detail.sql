/* ============================================================================
   daily_detail.sql  --  DETAIL DATASET (Dataset B)

   One row per logical daily task on the report date, for live wells only.
   Feeds: drill-down tables, well detail, Excel export and the deterministic
   evidence sent to the LLM.

   Quantity status and mapping status are NOT computed here. They are derived
   once, in Python (app/services/validation_service.py), from the fields below
   so the summary and the detail can never disagree.

   Contract
     - SELECT only.
     - Parameter 1 (?) : report date (date)
   ============================================================================ */

{{include:daily_tasks}}

SELECT dt.task_daily_id,
       dt.well_id,
       dt.action_on,
       dt.schedule_id,
       dt.task_code,
       dt.activity_id,
       dt.activity_code,
       dt.activity_description,
       dt.wbs,
       dt.crew_code,
       dt.uom_id,
       dt.uom_code,
       dt.activity_uom,
       dt.crew_id,
       dt.crew_type_id,
       dt.crew_type_name,
<<<<<<< HEAD
       dt.crew_instance_code,
       dt.crew_supervisor,
       dt.crew_employee_names,
=======
       dt.crew_type_code,
       dt.crew_instance_code,
       dt.crew_supervisor,
       dt.crew_employee_names,
       dt.planned_start,
       dt.planned_end,
       dt.actual_start,
       dt.actual_end,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
       dt.planned,
       dt.progress,
       dt.actual_quantity,
       dt.actual_quantity_raw,
       dt.daily_completed,
       dt.ph_name,
       dt.is_actual_entry,
       dt.daily_data_json_valid,
       dt.daily_data_json_invalid,
       dt.actual_quantity_unparseable,
       dt.group_row_count,
       dt.group_actual_entry_count
FROM daily_tasks AS dt
ORDER BY dt.uom_code, dt.wbs, dt.activity_code, dt.well_id, dt.task_code;

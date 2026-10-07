/* ============================================================================
   daily_summary.sql  --  DAY-LEVEL COUNTERS AND DATA-QUALITY TALLIES

   Returns a single row describing the report date as a whole, measured over
   the RAW (pre-grain-resolution) task_daily rows for live wells.

   These figures cannot be derived from the resolved detail dataset, because
   resolution is exactly what collapses the duplicate planning snapshots. They
   are what makes the resolution auditable: the dashboard can state how many
   raw rows existed, how many logical tasks they resolved to, and how many rows
   were set aside -- without ever hiding them.

   The grouped UOM / WBS / Activity summary (Dataset A) is NOT built here. It is
   aggregated in Python from the very same resolved rows the detail dataset
   returns, so every summary number is traceable to the rows behind it.

   Contract
     - SELECT only.
     - Parameter 1 (?) : report date (date)
   ============================================================================ */

{{include:daily_tasks}}

, raw_counters AS (
    SELECT COUNT(*)                                  AS raw_row_count,
           COUNT(DISTINCT rk.well_id)                AS raw_well_count,
           SUM(CAST(rk.is_actual_entry AS int))      AS raw_actual_entry_count,
           SUM(rk.daily_data_json_invalid)           AS invalid_json_row_count,
           SUM(rk.actual_quantity_unparseable)       AS unparseable_actual_row_count
    FROM ranked_daily AS rk
),

grain_counters AS (
    SELECT COUNT(*)                                                            AS logical_task_count,
           SUM(CASE WHEN g.group_row_count > 1 THEN 1 ELSE 0 END)              AS multi_row_task_count,
           SUM(CASE WHEN g.group_actual_entry_count > 1 THEN 1 ELSE 0 END)     AS duplicate_actual_task_count
    FROM (
        SELECT r.group_row_count, r.group_actual_entry_count
        FROM resolved_daily AS r
        WHERE r.grain_rank = 1
    ) AS g
)

SELECT rc.raw_row_count,
       rc.raw_well_count,
       rc.raw_actual_entry_count,
       rc.invalid_json_row_count,
       rc.unparseable_actual_row_count,
       gc.logical_task_count,
       gc.multi_row_task_count,
       gc.duplicate_actual_task_count,
       rc.raw_row_count - gc.logical_task_count AS superseded_row_count
FROM raw_counters AS rc
CROSS JOIN grain_counters AS gc;

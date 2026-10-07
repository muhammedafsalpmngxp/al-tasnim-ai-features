/*
  Oman Petroleum Field Map -- one row per well in well.well_master.

  Well identity:  well.well_master.well_id is authoritative. drilling_sequence
                  only supplies the field name; its well_id is used solely to
                  find that name.

  Well status:    decided here, from well.well_master.eng_completion_date and
                  nothing else (business_rules.md section 7):
                      NULL      -> INCOMPLETE  (live)
                      NOT NULL  -> COMPLETED

  Field:          dsq.drilling_sequence holds many planning snapshots per well
                  (weekly loads). Each well takes the field of its LATEST
                  snapshot that names one -- newest week_number, then newest
                  load_timestamp, then highest id -- so a well is assigned to
                  exactly one field and weekly snapshots never inflate a count.
                  field_key is the trimmed, upper-cased name used for matching;
                  field is the stored value, unchanged.

  Category and    read from that SAME snapshot, through its declared foreign
  function:       keys (dsq.drilling_sequence.well_category_id ->
                  well.well_category, .well_function_id -> well.well_function).
                  well.well_master carries neither, and well_type_id is a
                  different thing (a well-type name), never a category.

  Completion      well.well_master has no completion-type column. The only
  type:           declared relationship is well.well_progress
                  .well_completion_type_id -> well.well_completion_type. That
                  table repeats a well many times (per week and per load), so
                  each well takes its LATEST row that names a type -- newest
                  week_number, then highest progress_id. Descriptions are shown
                  exactly as stored; no two are merged.

  Rig:            well.well_master.rig_id -> well.rig.rig_no. NULL = unassigned.

  Well location:  well.well_master.well_location_id -> well.well_location, a
                  free-text description shown as detail only -- never parsed
                  into a coordinate.

  Every lookup is a LEFT JOIN: an id with no description comes back with a
  NULL description and its id kept, so nothing is silently dropped.

  A well with no field in any snapshot is still returned, with a NULL field,
  so coverage can be reported without dropping it.

  Both well_id columns are int today; TRY_CONVERT keeps the join safe if
  drilling_sequence.well_id is ever migrated to text, as task_daily.well_id
  was (daily_report_rules.md section 1).

  No parameters: one set-based read of every well. Filtering by field,
  category, function, completion type, rig or status happens afterwards over
  these rows, so no request value ever reaches this statement.
*/
WITH latest_field AS (
    SELECT
        TRY_CONVERT(int, ds.well_id) AS well_id,
        LTRIM(RTRIM(ds.field))       AS field,
        ds.well_category_id,
        ds.well_function_id,
        ROW_NUMBER() OVER (
            PARTITION BY TRY_CONVERT(int, ds.well_id)
            ORDER BY ds.week_number DESC, ds.load_timestamp DESC, ds.id DESC
        ) AS rn
    FROM dsq.drilling_sequence ds
    WHERE ds.field IS NOT NULL
      AND LTRIM(RTRIM(ds.field)) <> ''
      AND TRY_CONVERT(int, ds.well_id) IS NOT NULL
),
latest_completion AS (
    SELECT
        wp.well_id,
        wp.well_completion_type_id,
        ROW_NUMBER() OVER (
            PARTITION BY wp.well_id
            ORDER BY wp.week_number DESC, wp.progress_id DESC
        ) AS rn
    FROM well.well_progress wp
    WHERE wp.well_completion_type_id IS NOT NULL
      AND wp.well_id IS NOT NULL
)
SELECT
    wm.well_id,
    CASE WHEN wm.eng_completion_date IS NULL THEN 'INCOMPLETE' ELSE 'COMPLETED' END AS status,
    lf.field,
    UPPER(lf.field) AS field_key,
    lf.well_category_id,
    wc.well_category,
    lf.well_function_id,
    wf.function_name AS well_function,
    lc.well_completion_type_id,
    wct.completion_type,
    wm.rig_id,
    r.rig_no,
    wm.well_location_id,
    wl.well_location
FROM well.well_master wm
LEFT JOIN latest_field lf
       ON lf.well_id = wm.well_id
      AND lf.rn = 1
LEFT JOIN well.well_category wc
       ON wc.well_category_id = lf.well_category_id
LEFT JOIN well.well_function wf
       ON wf.well_function_id = lf.well_function_id
LEFT JOIN latest_completion lc
       ON lc.well_id = wm.well_id
      AND lc.rn = 1
LEFT JOIN well.well_completion_type wct
       ON wct.well_completion_type_id = lc.well_completion_type_id
LEFT JOIN well.rig r
       ON r.rig_id = wm.rig_id
LEFT JOIN well.well_location wl
       ON wl.well_location_id = wm.well_location_id
ORDER BY wm.well_id;

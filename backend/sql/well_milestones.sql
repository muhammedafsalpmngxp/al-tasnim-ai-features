/* ============================================================================
   well_milestones.sql  --  OUTSTANDING LIFECYCLE MILESTONES (raw evidence)

   Purpose
     For every LIVE well (well.well_master.eng_completion_date IS NULL), return
     one row per milestone that has not yet been reached (its actual date column
     is still NULL) and whose deadline is computable (its source date is not
     NULL). This is raw evidence only -- it does NOT decide what counts as
     "near" a deadline; that windowing is a presentation threshold owned by
     app/services/milestone_service.py, exactly the way DETAIL_VIEW_TASK_THRESHOLD
     is owned by settings rather than by SQL.

   Column dictionary (business_rules.md section 2 and 4) -- exact names, no
   synonyms:
     Pegging sheet   : well.well_master.pegged_date       , deadline ex_rig_on_date - 60 days
     FLAF             : well.well_master.flaf_issue_date   , deadline ex_rig_on_date - 90 days
     Rig-on           : well.well_master.rig_on_date       , deadline ex_rig_on_date        (master date)
     Rig-off          : well.well_master.rig_off_date      , deadline ex_rig_off_date

   Contract
     - SELECT only.
     - No parameters.
     - Emits well_id, milestone, deadline_date. Every other well_master column
       needed for the detail view (all six dates) is carried through so the
       caller never has to re-query per well.
   ============================================================================ */

WITH live_wells AS (
    SELECT wm.well_id,
           wm.pegged_date,
           wm.flaf_issue_date,
           wm.ex_rig_on_date,
           wm.rig_on_date,
           wm.ex_rig_off_date,
           wm.rig_off_date
    FROM well.well_master AS wm
    WHERE wm.eng_completion_date IS NULL
),

milestones AS (
    SELECT well_id, 'PEGGING' AS milestone, pegged_date AS actual_date,
           DATEADD(day, -60, ex_rig_on_date) AS deadline_date
    FROM live_wells
    UNION ALL
    SELECT well_id, 'FLAF', flaf_issue_date,
           DATEADD(day, -90, ex_rig_on_date)
    FROM live_wells
    UNION ALL
    SELECT well_id, 'RIG_ON', rig_on_date,
           ex_rig_on_date
    FROM live_wells
    UNION ALL
    SELECT well_id, 'RIG_OFF', rig_off_date,
           ex_rig_off_date
    FROM live_wells
)

SELECT m.well_id,
       m.milestone,
       m.deadline_date,
       lw.pegged_date,
       lw.flaf_issue_date,
       lw.ex_rig_on_date,
       lw.rig_on_date,
       lw.ex_rig_off_date,
       lw.rig_off_date
FROM milestones AS m
INNER JOIN live_wells AS lw ON lw.well_id = m.well_id
WHERE m.actual_date IS NULL          -- not yet reached
  AND m.deadline_date IS NOT NULL    -- deadline is computable

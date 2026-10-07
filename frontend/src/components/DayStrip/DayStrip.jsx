/**
<<<<<<< HEAD
 * The whole day, in one line: tasks today, upcoming milestones, overdue.
 *
 * This replaces the earlier grid of per-status stat tiles plus a separate
 * milestone banner. Both were true summaries, but together they pushed the
 * well list below the fold and repeated the status detail that each well row
 * already shows. Milestones are a live well's lifecycle deadlines, evaluated
 * against today regardless of the selected report date -- clicking either
 * figure opens the same Upcoming Milestones page as before.
 */
export default function DayStrip({ totals, milestones, onOpenMilestones }) {
  const milestonesReady = !milestones.loading && !milestones.error && milestones.data
  const upcomingCount = milestonesReady ? milestones.data.upcoming.length : null
  const overdueCount = milestonesReady ? milestones.data.overdue_count : null

  return (
    <div className="day-strip">
      <span className="day-strip__item">
        <b className="num">{totals.task_count}</b> task{totals.task_count === 1 ? '' : 's'} today
      </span>

      {milestonesReady ? (
        <>
          <button
            type="button"
            className="day-strip__item day-strip__item--link day-strip__item--upcoming"
            onClick={onOpenMilestones}
            title="Live wells within the priority window of a pegging, FLAF, rig-on or rig-off deadline"
          >
            <b>{upcomingCount}</b> upcoming milestone{upcomingCount === 1 ? '' : 's'}
          </button>
          {overdueCount ? (
            <button
              type="button"
              className="day-strip__item day-strip__item--link day-strip__item--overdue"
              onClick={onOpenMilestones}
              title="Live wells past a pegging, FLAF, rig-on or rig-off deadline they have not yet reached"
            >
              <b>{overdueCount}</b> overdue
            </button>
          ) : null}
        </>
      ) : null}
=======
 * The selected report date, in one line: what was actually reported on it.
 *
 * Deliberately narrow. Lifecycle deadlines used to be repeated here as well,
 * but they are measured against TODAY rather than against the selected date,
 * and they now have their own panel at the top of the page (MilestonesSummary)
 * where they are read first. Showing the same two counts again a few pixels
 * below that panel said nothing new and invited the reader to wonder whether
 * the second pair meant something different.
 *
 * What is left here is the one figure that genuinely belongs to the selected
 * date rather than to today.
 */
export default function DayStrip({ totals }) {
  return (
    <div className="day-strip">
      <span className="day-strip__item">
        <b className="num">{totals.task_count}</b> task{totals.task_count === 1 ? '' : 's'}{' '}
        reported on this date
      </span>
      <span className="day-strip__item">
        across <b className="num">{totals.well_count}</b> well
        {totals.well_count === 1 ? '' : 's'}
      </span>
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    </div>
  )
}

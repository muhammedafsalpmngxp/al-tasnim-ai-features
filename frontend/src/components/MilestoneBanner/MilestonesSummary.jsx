import { Spinner } from '../common'

/**
 * The first thing on the front page: which live wells are running out of time.
 *
 * Milestones are lifecycle deadlines (pegging, FLAF, rig-on, rig-off) measured
 * against TODAY, not against the selected report date -- a deadline does not
 * move because an operator is looking back at last Tuesday's entries. That is
 * why this sits above the day's own figures rather than inside them.
 *
 * Only the soonest few are listed inline; the full list, including the overdue
 * backlog, stays one click away on its own page. On this data the overdue count
 * runs into the hundreds, so listing it here would bury everything under it --
 * the count is the useful part at this level, the detail is not.
 *
 * Every figure comes from `/api/daily/milestones`, already computed and already
 * sorted by the backend. This component counts nothing and re-sorts nothing.
 */
export default function MilestonesSummary({ resource, onOpenMilestones, limit = 4 }) {
  if (resource.loading) {
    return (
      <section className="milestones-summary milestones-summary--quiet">
        <Spinner /> Loading well lifecycle deadlines…
      </section>
    )
  }

  // A milestone outage must never take the day's own report down with it: the
  // deadlines are additive context, and the brief below is unaffected.
  if (resource.error || !resource.data) {
    return (
      <section className="milestones-summary milestones-summary--quiet">
        Upcoming milestones could not be loaded
        {resource.error?.message ? ` (${resource.error.message})` : ''}. The daily brief
        below is unaffected.
      </section>
    )
  }

  const {
    upcoming = [],
    overdue_count: overdueCount = 0,
    window_days: windowDays,
  } = resource.data

  const soonest = upcoming.slice(0, limit)

  return (
    <section className="milestones-summary" aria-label="Well lifecycle deadlines">
      <div className="milestones-summary__head">
        <div className="milestones-summary__counts">
          <button
            type="button"
            className="milestone-stat milestone-stat--upcoming"
            onClick={onOpenMilestones}
            title={`Live wells within ${windowDays} days of a pegging, FLAF, rig-on or rig-off deadline`}
          >
            <span className="milestone-stat__value">{upcoming.length}</span>
            <span className="milestone-stat__label">
              upcoming milestone{upcoming.length === 1 ? '' : 's'}
            </span>
          </button>

          <button
            type="button"
            className={`milestone-stat${overdueCount ? ' milestone-stat--overdue' : ''}`}
            onClick={onOpenMilestones}
            title="Live wells past a deadline they have not yet reached"
          >
            <span className="milestone-stat__value">{overdueCount}</span>
            <span className="milestone-stat__label">overdue</span>
          </button>
        </div>

        <button type="button" className="btn btn--ghost" onClick={onOpenMilestones}>
          View all milestones
        </button>
      </div>

      {soonest.length ? (
        <ul className="milestones-summary__list">
          {soonest.map((alert) => (
            <li key={`${alert.well_id}-${alert.milestone}`} className="milestones-summary__item">
              <span className="milestones-summary__well">Well {alert.well_id}</span>
              <span className="milestones-summary__label">{alert.milestone_label}</span>
              <span className="milestones-summary__when num">{dueText(alert)}</span>
              <span className="milestones-summary__date">{alert.deadline_date}</span>
            </li>
          ))}
          {upcoming.length > soonest.length ? (
            <li className="milestones-summary__more">
              <button type="button" className="linklike" onClick={onOpenMilestones}>
                + {upcoming.length - soonest.length} more inside the {windowDays}-day window
              </button>
            </li>
          ) : null}
        </ul>
      ) : (
        <p className="milestones-summary__empty">
          No live well is inside the {windowDays}-day priority window.
        </p>
      )}
    </section>
  )
}

/** Restates the backend's own signed day count. Nothing is recomputed here. */
function dueText(alert) {
  if (alert.overdue) {
    const days = Math.abs(alert.days_remaining)
    return `${days} day${days === 1 ? '' : 's'} overdue`
  }
  if (alert.days_remaining === 0) return 'due today'
  return `in ${alert.days_remaining} day${alert.days_remaining === 1 ? '' : 's'}`
}

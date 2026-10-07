import { useState } from 'react'
import { Field, LoadingBlock, ErrorState } from '../common'

/**
 * The full "priority window" list -- reached by drilling into it from the
 * compact MilestoneBanner summary on the main dashboard, never shown inline
 * there. Upcoming milestones (within the configured window) are listed
 * openly; overdue ones sit behind a collapsed toggle, because on this data
 * the overdue backlog runs far larger than the upcoming list and would
 * otherwise bury it.
 */
export default function MilestonesPage({ resource }) {
  const [showOverdue, setShowOverdue] = useState(false)
  const [openKey, setOpenKey] = useState(null)

  if (resource.loading) return <LoadingBlock message="Loading upcoming milestones…" />
  if (resource.error) return <ErrorState error={resource.error} onRetry={resource.reload} />
  if (!resource.data) return null

  const { upcoming, overdue, overdue_count: overdueCount, window_days: windowDays } = resource.data

  return (
    <div className="milestones-page">
      <p className="milestones-page__intro">
        Live wells within {windowDays} day{windowDays === 1 ? '' : 's'} of a pegging, FLAF,
        rig-on or rig-off deadline, soonest first.
      </p>

      {upcoming.length === 0 ? (
        <p className="milestone-banner__empty">No well is inside the {windowDays}-day priority window.</p>
      ) : (
        <ul className="milestone-list">
          {upcoming.map((alert) => (
            <MilestoneRow
              key={rowKey(alert)}
              alert={alert}
              isOpen={openKey === rowKey(alert)}
              onToggle={() => setOpenKey((k) => (k === rowKey(alert) ? null : rowKey(alert)))}
            />
          ))}
        </ul>
      )}

      {overdueCount > 0 ? (
        <div className="milestone-banner__overdue">
          <button
            type="button"
            className="linklike milestone-banner__overdue-toggle"
            onClick={() => setShowOverdue((value) => !value)}
          >
            {showOverdue ? '▾' : '▸'} <span className="num">{overdueCount}</span> already overdue
            (past its deadline, still not reached)
          </button>
          {showOverdue ? (
            <ul className="milestone-list milestone-list--overdue">
              {overdue.map((alert) => (
                <MilestoneRow
                  key={rowKey(alert)}
                  alert={alert}
                  isOpen={openKey === rowKey(alert)}
                  onToggle={() => setOpenKey((k) => (k === rowKey(alert) ? null : rowKey(alert)))}
                />
              ))}
              {overdue.length < overdueCount ? (
                <li className="milestone-list__more">
                  + {overdueCount - overdue.length} more overdue, not listed
                </li>
              ) : null}
            </ul>
          ) : null}
        </div>
      ) : null}
    </div>
  )
}

function rowKey(alert) {
  return `${alert.well_id}-${alert.milestone}`
}

function daysText(alert) {
  if (alert.overdue) {
    const days = Math.abs(alert.days_remaining)
    return `${days} day${days === 1 ? '' : 's'} overdue`
  }
  if (alert.days_remaining === 0) return 'due today'
  return `in ${alert.days_remaining} day${alert.days_remaining === 1 ? '' : 's'}`
}

function MilestoneRow({ alert, isOpen, onToggle }) {
  return (
    <li className={`milestone-row${alert.overdue ? ' milestone-row--overdue' : ''}`}>
      <button type="button" className="milestone-row__summary" onClick={onToggle} aria-expanded={isOpen}>
        <span className="milestone-row__well">Well {alert.well_id}</span>
        <span className="milestone-row__label">{alert.milestone_label}</span>
        <span
          className={`num milestone-row__days${alert.overdue ? ' milestone-row__days--overdue' : ''}`}
        >
          {daysText(alert)}
        </span>
        <span className="milestone-row__chevron">{isOpen ? '▾' : '▸'}</span>
      </button>

      {isOpen ? (
        <div className="milestone-row__detail">
          <div className="field-grid">
            <Field label="Deadline for this milestone" value={alert.deadline_date} />
            <Field label="Pegging sheet date" value={alert.pegged_date} missingText="Not yet issued" />
            <Field label="FLAF date" value={alert.flaf_issue_date} missingText="Not yet issued" />
            <Field label="Expected rig-on" value={alert.ex_rig_on_date} missingText="Not recorded" />
            <Field label="Actual rig-on" value={alert.rig_on_date} missingText="Not yet on" />
            <Field label="Expected rig-off" value={alert.ex_rig_off_date} missingText="Not recorded" />
            <Field label="Actual rig-off" value={alert.rig_off_date} missingText="Not yet off" />
          </div>
        </div>
      ) : null}
    </li>
  )
}

import { useEffect, useMemo, useState } from 'react'
import ExplainPanel from '../ExplainPanel/ExplainPanel'
import StatusCount from '../StatusCount/StatusCount'
import api from '../../services/api'
import { EmptyState, Num, Spinner, orderedStatuses, statusDescription } from '../common'

/**
<<<<<<< HEAD
 * The main dashboard view: one compact row per live well.
=======
 * The main dashboard view: one table row per live well.
 *
 * A real table, not a list of cards: every well is measured by the same eight
 * figures, and a column an operator can read straight down is what makes two
 * wells comparable at a glance. The last cell of each row is that one well's
 * own AI summary, which expands underneath the row it belongs to.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
 *
 * Earlier versions organised the day by validation status, then by work
 * category, then by activity, before ever reaching a well. That hierarchy
 * carried real information, but it took several clicks to answer the question
 * an operator actually asks first thing: *what did each well do, and how did
 * it come out?* This view answers that in one screen -- one row per well, main
 * points only -- and pushes everything else one click away:
 *
 * * a well's own work is summarised, not listed -- the busiest activity plus
 *   how many others, never every task inline. A well with many tasks is not
 *   handled specially; it simply always drills to its own detail page, the
 *   same click as any other well.
 * * each row also carries that well's task activity as of the selected date --
 *   its open tasks and the two disjoint halves that make them up (incomplete
 *   and ongoing, which add up to the total), the tasks reported on the date
 *   itself, and the last date the well appears in the records. Those come from
 *   `/api/daily/well-activity`, aggregated in SQL over the well's whole task
 *   history up to the date, and each one expands in place to show the tasks
 *   behind it rather than opening another page.
<<<<<<< HEAD
 * * a click on the row opens that well's full detail (`WellDetail`), where
 *   every task is its own panel.
 * * "AI summary" is a second, independent control per row: it expands an
 *   explanation of that well's day in place, without leaving the list.
=======
 * * a click on the well id opens that well's full detail (`WellDetail`), where
 *   every task is its own panel.
 * * "AI summary" is the last cell of every row: an independent control that
 *   expands an explanation of that one well's day in place, without leaving
 *   the list. It is scoped to that well alone and never to the table.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
 *
 * Because the well universe is `well.well_master` and not one date's task
 * rows, a well stays on this page on a day it reported nothing -- which is
 * exactly when "0 reported today, last seen three weeks ago" is worth seeing.
 *
 * Every figure here is still backend-computed -- this component sums nothing
 * and reclassifies nothing. It only decides how much of what the backend
 * already returned belongs on the front page.
 */
export default function WellList({ resource, activityResource, reportDate, onSelectWell }) {
  const [query, setQuery] = useState('')
  const [scope, setScope] = useState('active')

  const wells = resource.data?.wells || []
  const tasks = resource.data?.tasks || []
  const activity = activityResource?.data?.wells || []

  const workByWell = useMemo(() => summariseWorkByWell(tasks), [tasks])
  const tasksByWell = useMemo(() => groupTasksByWell(tasks), [tasks])

  /**
   * One row per well, from the two backend lists that describe it: the day's
   * per-well rollup (status counts, task count for the date) and the well's
   * task activity (incomplete/ongoing/last seen). This is a lookup by well id,
   * not a calculation -- neither list's figures are combined into a new one.
   */
  const rows = useMemo(() => merge(wells, activity), [wells, activity])

  const scopes = useMemo(
    () => ({
      active: rows.filter((row) => isActive(row)),
      reported: rows.filter((row) => (row.activity?.today_reported_task_count || row.task_count) > 0),
      all: rows,
    }),
    [rows],
  )

  const sorted = useMemo(() => {
    const selected = scopes[scope] || rows
    return [...selected].sort(
      (a, b) =>
        Number(hasReported(b)) - Number(hasReported(a)) ||
        b.task_count - a.task_count ||
        (b.activity?.open_task_count || 0) - (a.activity?.open_task_count || 0) ||
        (b.activity?.ongoing_task_count || 0) - (a.activity?.ongoing_task_count || 0) ||
        a.well_id - b.well_id,
    )
  }, [scopes, scope, rows])

  const filtered = useMemo(() => {
    const needle = query.trim()
    if (!needle) return sorted
    return sorted.filter((well) => String(well.well_id).includes(needle))
  }, [sorted, query])

  if (!rows.length) {
    if (activityResource?.loading) {
      return (
        <div className="well-list__note">
          <Spinner /> Loading task activity for each well…
        </div>
      )
    }
    return (
      <EmptyState title="No daily task records for this date">
        No live well has a task dated {reportDate}. Select another date, or check
        that the daily entries for this date have been submitted.
      </EmptyState>
    )
  }

  return (
    <section>
      <div className="well-list__toolbar">
        <input
          type="text"
          className="well-list__search"
          placeholder="Find a well by ID…"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          aria-label="Find a well by ID"
        />
        {/*
          Which wells the page lists, never what any of them says. Every option
          shows the same backend figures for whichever wells it includes; none
          of them recalculates or hides a number on a row it does show.
        */}
        <div className="well-scope" role="group" aria-label="Which wells to list">
          <ScopeButton
            scope="active"
            current={scope}
            onSelect={setScope}
            count={scopes.active.length}
            label="Reporting or open work"
            title="Wells that reported a task on this date, or still have open tasks of any kind."
          />
          <ScopeButton
            scope="reported"
            current={scope}
            onSelect={setScope}
            count={scopes.reported.length}
            label="Reported on this date"
            title="Only the wells that reported at least one task on the selected date."
          />
          <ScopeButton
            scope="all"
            current={scope}
            onSelect={setScope}
            count={scopes.all.length}
            label="All"
            title="Every live well with any task record on or before the selected date."
          />
        </div>
        <span className="well-list__count">
          {filtered.length} of {sorted.length} well{sorted.length === 1 ? '' : 's'}
        </span>
      </div>

      {!tasks.length ? (
        <div className="well-list__note">
          No live well reported a daily task on {reportDate}. The wells below are listed
          for their task activity up to that date.
        </div>
      ) : null}
      {activityResource?.loading ? (
        <div className="well-list__note">
          <Spinner /> Loading task activity for each well…
        </div>
      ) : null}
      {activityResource?.error ? (
        <div className="well-list__note well-list__note--error">
          Task-activity figures could not be loaded ({activityResource.error.message}). The
          day's own task counts below are unaffected.
        </div>
      ) : null}

      {/*
        Keyed by reportDate: each WellRow owns its own "is the AI summary
        open" state locally, and that state must not survive a date change
        -- an open summary describes one specific date, and would otherwise
        keep sitting open (and stale-looking) after the operator has moved
        on to a different one. Changing this key forces React to unmount
        and recreate every row on a date change, resetting that state the
        same way path-reset already closes the top-level explain panel.
        The expanded task-activity detail resets with it, for the same reason.
      */}
<<<<<<< HEAD
      <div className="well-list" key={reportDate}>
        {filtered.map((well) => (
          <WellRow
            key={well.well_id}
            well={well}
            work={workByWell.get(well.well_id)}
            todaysTasks={tasksByWell.get(well.well_id)}
            reportDate={reportDate}
            onSelectWell={onSelectWell}
          />
        ))}
        {filtered.length === 0 ? (
          <div className="notice">
            <div className="notice__body">
              {query ? `No well matches “${query}”.` : 'No well matches this filter.'}
            </div>
          </div>
        ) : null}
=======
      <div className="well-table-wrap">
        <table className="well-table" key={reportDate}>
          <thead>
            <tr>
              <th scope="col">Well</th>
              <th scope="col">Activity</th>
              <th scope="col" className="well-table__num">Tasks</th>
              <th scope="col">Validation status</th>
              {/*
                Open is the total; incomplete and ongoing are its two halves
                and do not overlap, so they add up to it. Each is a control
                that expands the tasks behind it, in this same row group.
              */}
              <th scope="col" className="well-table__num" title="Every task not recorded as completed. Incomplete + Ongoing add up to this.">
                Open
              </th>
              <th scope="col" className="well-table__num" title="Open tasks that are not ongoing: no recorded actual start, or an actual end recorded without completion.">
                Incomplete
              </th>
              <th scope="col" className="well-table__num" title="Open tasks with a recorded actual start and no recorded actual end.">
                Ongoing
              </th>
              <th scope="col" className="well-table__num" title="Logical tasks this well reported on the selected report date.">
                Reported today
              </th>
              <th scope="col" title="The latest date this well appears in the task-daily records, on or before the selected date. Not a completion date.">
                Last task date
              </th>
              <th scope="col" className="well-table__ai">AI summary</th>
            </tr>
          </thead>

          {filtered.map((well) => (
            <WellRow
              key={well.well_id}
              well={well}
              work={workByWell.get(well.well_id)}
              todaysTasks={tasksByWell.get(well.well_id)}
              reportDate={reportDate}
              onSelectWell={onSelectWell}
            />
          ))}

          {filtered.length === 0 ? (
            <tbody>
              <tr>
                <td colSpan={WELL_TABLE_COLUMNS} className="well-table__empty">
                  {query ? `No well matches “${query}”.` : 'No well matches this filter.'}
                </td>
              </tr>
            </tbody>
          ) : null}
        </table>
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
      </div>
    </section>
  )
}

<<<<<<< HEAD
=======
/**
 * How many columns a full-width row inside the table has to span. Declared
 * once so an expanded detail row can never drift out of step with the header
 * above it.
 */
const WELL_TABLE_COLUMNS = 10

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
function ScopeButton({ scope, current, onSelect, count, label, title }) {
  const active = scope === current
  return (
    <button
      type="button"
      className={`well-scope__btn${active ? ' well-scope__btn--on' : ''}`}
      onClick={() => onSelect(scope)}
      aria-pressed={active}
      title={title}
    >
      {label} <Num>{count}</Num>
    </button>
  )
}

<<<<<<< HEAD
=======
/**
 * One well, as its own `<tbody>`.
 *
 * A row group rather than a single `<tr>` because everything this row can
 * expand -- the tasks behind one of its figures, and its own AI summary --
 * belongs to this well and must stay inside it. Putting those in the same
 * `<tbody>` is what guarantees an expanded panel can never be read as
 * belonging to the well above or below it, however the table is styled.
 */
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
function WellRow({ well, work, todaysTasks, reportDate, onSelectWell }) {
  const [explaining, setExplaining] = useState(false)
  const [expanded, setExpanded] = useState(null)
  const activeStatuses = orderedStatuses(well.status_counts).filter(
    (status) => well.status_counts[status],
  )
  const activity = well.activity
<<<<<<< HEAD

  const openWell = () => onSelectWell(well.well_id)
  const openWellOnKey = (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault()
      openWell()
    }
  }

=======
  const todayCount = activity?.today_reported_task_count ?? well.task_count ?? 0

  const openWell = () => onSelectWell(well.well_id)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  const toggle = (key) => setExpanded((current) => (current === key ? null : key))

  // The AI request object is memoised on the values that identify it, so the
  // panel's effect fires once per well/date rather than on every re-render of
  // the list around it -- a re-rendered object literal would otherwise look
  // like a new request and ask for the same explanation again.
  const explainRequest = useMemo(
    () => ({ report_date: reportDate, scope: 'well', well_id: well.well_id }),
    [reportDate, well.well_id],
  )

  return (
<<<<<<< HEAD
    <article className="well-row">
      <div className="well-row__line">
        {/*
          A <div> here, not a <button>: StatusCount below renders its own
          <button>, and a button can never contain another button. role and
          tabIndex keep the row a single keyboard- and screen-reader-reachable
          control, same as clicking anywhere on it with a mouse.
        */}
        <div
          className="well-row__main"
          role="button"
          tabIndex={0}
          onClick={openWell}
          onKeyDown={openWellOnKey}
          title={`Open well ${well.well_id}'s daily tasks`}
        >
          <span className="well-row__id">Well {well.well_id}</span>

          <span className="well-row__work">
            {well.task_count ? (
              <>
                {work?.label || <span className="missing">Activity not mapped</span>}
                {work && work.distinctCount > 1 ? (
                  <span className="well-row__work-extra"> +{work.distinctCount - 1} more</span>
                ) : null}
              </>
            ) : (
              <span className="missing">No task reported on this date</span>
            )}
          </span>

          {well.task_count ? (
            <span className="well-row__figure">
              <Num>{well.task_count}</Num> task{well.task_count === 1 ? '' : 's'}
            </span>
          ) : null}

          <span className="well-row__statuses">
            {activeStatuses.map((status) => (
=======
    <tbody className={`well-tbody${explaining ? ' well-tbody--explaining' : ''}`}>
      <tr className="well-table__row">
        <th scope="row" className="well-table__well">
          <button
            type="button"
            className="well-table__well-btn"
            onClick={openWell}
            title={`Open well ${well.well_id}'s daily tasks`}
          >
            Well {well.well_id}
          </button>
        </th>

        <td className="well-table__activity">
          {well.task_count ? (
            <>
              {work?.label || <span className="missing">Activity not mapped</span>}
              {work && work.distinctCount > 1 ? (
                <span className="well-row__work-extra"> +{work.distinctCount - 1} more</span>
              ) : null}
            </>
          ) : (
            <span className="missing">No task reported on this date</span>
          )}
        </td>

        <td className="well-table__num">
          {well.task_count ? <Num>{well.task_count}</Num> : <span className="missing">—</span>}
        </td>

        <td className="well-table__statuses">
          {activeStatuses.length ? (
            activeStatuses.map((status) => (
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
              <StatusCount
                key={status}
                status={status}
                count={well.status_counts[status]}
                small
                title={statusDescription(status)}
              />
<<<<<<< HEAD
            ))}
          </span>
        </div>

        <button
          type="button"
          className="btn btn--ghost well-row__ai-toggle"
          onClick={() => setExplaining((value) => !value)}
        >
          {explaining ? 'Hide AI summary' : 'AI summary'}
        </button>
      </div>

      {activity ? (
        <TaskActivityStrip
          activity={activity}
          expanded={expanded}
          onToggle={toggle}
          todaysTaskCount={well.task_count}
        />
      ) : null}

      {expanded ? (
        <TaskActivityDetail
          wellId={well.well_id}
          reportDate={reportDate}
          metric={expanded}
          todaysTasks={todaysTasks}
          onClose={() => setExpanded(null)}
        />
      ) : null}

      {explaining ? (
        <ExplainPanel request={explainRequest} onClose={() => setExplaining(false)} />
      ) : null}
    </article>
=======
            ))
          ) : (
            <span className="missing">—</span>
          )}
        </td>

        <MetricCell
          metric="open"
          label="Open"
          count={activity?.open_task_count}
          expanded={expanded}
          onToggle={toggle}
          available={Boolean(activity)}
          emphasis
        />
        <MetricCell
          metric="incomplete"
          label="Incomplete"
          count={activity?.incomplete_task_count}
          expanded={expanded}
          onToggle={toggle}
          available={Boolean(activity)}
        />
        <MetricCell
          metric="ongoing"
          label="Ongoing"
          count={activity?.ongoing_task_count}
          expanded={expanded}
          onToggle={toggle}
          available={Boolean(activity)}
        />
        <MetricCell
          metric="today"
          label="Reported today"
          count={todayCount}
          expanded={expanded}
          onToggle={toggle}
          available={Boolean(activity) || Boolean(well.task_count)}
        />

        <td className="well-table__date">
          {activity?.last_task_date ? (
            <Num>{activity.last_task_date}</Num>
          ) : (
            <span className="missing">Not recorded</span>
          )}
        </td>

        {/*
          The last cell of every row: this one well's own AI summary, and
          nothing else's. It expands underneath this same row group, so the
          answer is always read next to the row it describes.
        */}
        <td className="well-table__ai">
          <button
            type="button"
            className="btn btn--ghost well-row__ai-toggle"
            onClick={() => setExplaining((value) => !value)}
            aria-expanded={explaining}
          >
            {explaining ? 'Hide AI summary' : 'AI summary'}
          </button>
        </td>
      </tr>

      {expanded ? (
        <tr className="well-table__expansion">
          <td colSpan={WELL_TABLE_COLUMNS}>
            <TaskActivityDetail
              wellId={well.well_id}
              reportDate={reportDate}
              metric={expanded}
              todaysTasks={todaysTasks}
              onClose={() => setExpanded(null)}
            />
          </td>
        </tr>
      ) : null}

      {explaining ? (
        <tr className="well-table__expansion">
          <td colSpan={WELL_TABLE_COLUMNS}>
            <ExplainPanel request={explainRequest} onClose={() => setExplaining(false)} />
          </td>
        </tr>
      ) : null}
    </tbody>
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  )
}

/**
<<<<<<< HEAD
 * The well's task activity as of the selected date: four backend figures, each
 * one a control that expands the tasks behind it in place. The strip stays on
 * the well's own card, so an expanded list can never be read as belonging to
 * another well.
 */
function TaskActivityStrip({ activity, expanded, onToggle, todaysTaskCount }) {
  const todayCount = activity.today_reported_task_count ?? todaysTaskCount ?? 0
  return (
    <div className="task-activity">
      {/*
        Open is the total, and the two figures after it are its two halves --
        they do not overlap, so they add up to it. An earlier version counted
        every open task as "incomplete" and the ongoing ones again alongside,
        so a row reading "48 incomplete, 24 ongoing" described 48 open tasks
        rather than 72, with nothing on the row saying which.
      */}
      <ActivityMetric
        metric="open"
        label="Open"
        count={activity.open_task_count}
        expanded={expanded}
        onToggle={onToggle}
        title="Every task not recorded as completed. The incomplete and ongoing figures beside this add up to it."
        emphasis
      />
      <ActivityMetric
        metric="incomplete"
        label="Incomplete"
        count={activity.incomplete_task_count}
        expanded={expanded}
        onToggle={onToggle}
        title="Open tasks that are not ongoing: no recorded actual start, or an actual end recorded without completion."
      />
      <ActivityMetric
        metric="ongoing"
        label="Ongoing"
        count={activity.ongoing_task_count}
        expanded={expanded}
        onToggle={onToggle}
        title="Open tasks with a recorded actual start and no recorded actual end."
      />
      <ActivityMetric
        metric="today"
        label="Reported today"
        count={todayCount}
        expanded={expanded}
        onToggle={onToggle}
        title="Logical tasks this well reported on the selected report date."
      />
      <span className="task-activity__date" title="The latest date this well appears in the task-daily records, on or before the selected date. Not a completion date.">
        Last task date{' '}
        {activity.last_task_date ? (
          <Num>{activity.last_task_date}</Num>
        ) : (
          <span className="missing">Not recorded</span>
        )}
      </span>
    </div>
  )
}

function ActivityMetric({ metric, label, count, expanded, onToggle, title, emphasis }) {
  const open = expanded === metric
  const disabled = !count
  return (
    <button
      type="button"
      className={[
        'task-metric',
        open ? 'task-metric--open' : '',
        emphasis ? 'task-metric--total' : '',
      ]
        .filter(Boolean)
        .join(' ')}
      onClick={disabled ? undefined : () => onToggle(metric)}
      disabled={disabled}
      aria-expanded={open}
      title={title}
    >
      <Num>{count ?? 0}</Num>
      <span className="task-metric__label">{label}</span>
    </button>
=======
 * One task-activity figure, as a table cell that expands the tasks behind it.
 *
 * A figure of zero is shown but cannot be expanded -- there is nothing behind
 * it to list, and a control that opens an empty panel is worse than a plain
 * number. `available` is false only when the task-activity call itself failed
 * or has not arrived: the cell then says so rather than printing a zero the
 * backend never reported.
 */
function MetricCell({ metric, label, count, expanded, onToggle, available, emphasis }) {
  if (!available) {
    return (
      <td className="well-table__num">
        <span className="missing">—</span>
      </td>
    )
  }

  const open = expanded === metric
  const disabled = !count

  return (
    <td className="well-table__num">
      <button
        type="button"
        className={[
          'task-metric',
          // In the table the column header already names the figure, so the
          // per-cell label is hidden by CSS rather than removed -- it is what
          // the row still reads as when the table collapses to one card per
          // well on a narrow screen.
          'task-metric--cell',
          open ? 'task-metric--open' : '',
          emphasis ? 'task-metric--total' : '',
        ]
          .filter(Boolean)
          .join(' ')}
        onClick={disabled ? undefined : () => onToggle(metric)}
        disabled={disabled}
        aria-expanded={open}
        aria-label={`${label}: ${count ?? 0}`}
      >
        <Num>{count ?? 0}</Num>
        <span className="task-metric__label">{label}</span>
      </button>
    </td>
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  )
}

/**
 * The tasks behind one of the row's figures, shown inside that same row.
 *
 * The incomplete/ongoing lists come from the backend, which resolves every
 * well's tasks in one query and serves each well from that same result -- so
 * expanding a second, third and fourth well costs no extra database work.
 * "Reported today" lists the day's own tasks the page already holds.
 */
function TaskActivityDetail({ wellId, reportDate, metric, todaysTasks, onClose }) {
  const [state, setState] = useState({ loading: metric !== 'today', tasks: null, error: null })

  useEffect(() => {
    if (metric === 'today') {
      setState({ loading: false, tasks: null, error: null })
      return undefined
    }
    const controller = new AbortController()
    let active = true
    setState({ loading: true, tasks: null, error: null })
    api
      .wellActivity({ date: reportDate, wellId }, controller.signal)
      .then((result) => active && setState({ loading: false, tasks: result.tasks || [], error: null }))
      .catch((error) => {
        if (!active || error.name === 'AbortError') return
        setState({ loading: false, tasks: null, error })
      })
    return () => {
      active = false
      controller.abort()
    }
  }, [wellId, reportDate, metric])

  const title =
    metric === 'ongoing'
      ? `Ongoing tasks on well ${wellId}`
      : metric === 'incomplete'
        ? `Incomplete tasks on well ${wellId} (not ongoing)`
        : metric === 'open'
          ? `Open tasks on well ${wellId}`
          : `Tasks reported by well ${wellId} on ${reportDate}`

  // The backend returns every open task; each figure selects its own share of
  // them, and "open" is the whole set -- the same split the counts show.
  const rows =
    metric === 'today'
      ? todaysTasks || []
      : (state.tasks || []).filter((task) => {
          if (metric === 'ongoing') return task.task_state === 'ONGOING'
          if (metric === 'incomplete') return task.task_state !== 'ONGOING'
          return true
        })

  return (
    <div className="task-detail">
      <div className="task-detail__head">
        <span className="task-detail__title">{title}</span>
        <button type="button" className="btn btn--ghost task-detail__close" onClick={onClose}>
          Close
        </button>
      </div>

      {state.loading ? (
        <div className="task-detail__note">
          <Spinner /> Loading this well's tasks…
        </div>
      ) : null}

      {state.error ? (
        <div className="task-detail__note task-detail__note--error">{state.error.message}</div>
      ) : null}

      {!state.loading && !state.error ? (
        <table className="task-detail__table">
          <thead>
            <tr>
              <th scope="col">Task</th>
              <th scope="col">Activity</th>
              {metric === 'today' ? (
                <th scope="col">Quantity status</th>
              ) : (
                <>
                  {/* One task_code can appear under two schedule ids and be
                      two different tasks -- without this column the pair
                      reads as a duplicated row. */}
                  <th scope="col">Schedule</th>
                  <th scope="col">State</th>
                  <th scope="col">Actual start</th>
                  <th scope="col">Actual end</th>
                  <th scope="col">Last task date</th>
                </>
              )}
            </tr>
          </thead>
          <tbody>
            {rows.map((task, index) => (
              <tr key={`${task.task_code}-${task.schedule_id ?? task.task_daily_id ?? index}`}>
                <td className="task-detail__code">
                  {task.task_code || <span className="missing">Not recorded</span>}
                </td>
                <td>
                  {task.activity_description || task.activity_code || (
                    <span className="missing">Not mapped</span>
                  )}
                </td>
                {metric === 'today' ? (
                  <td>{task.quantity_status}</td>
                ) : (
                  <>
                    <td>{task.schedule_id ?? <span className="missing">—</span>}</td>
                    <td>
                      <span className={`task-state task-state--${task.task_state}`}>
                        {TASK_STATE_LABELS[task.task_state] || task.task_state}
                      </span>
                    </td>
                    <td>{task.actual_start || <span className="missing">—</span>}</td>
                    <td>{task.actual_end || <span className="missing">—</span>}</td>
                    <td>{task.last_task_date || <span className="missing">—</span>}</td>
                  </>
                )}
              </tr>
            ))}
            {rows.length === 0 ? (
              <tr>
                <td colSpan={metric === 'today' ? 3 : 7} className="task-detail__empty">
                  No task to show here.
                </td>
              </tr>
            ) : null}
          </tbody>
        </table>
      ) : null}
    </div>
  )
}

/** Human labels for the backend's task states. The code stays authoritative. */
const TASK_STATE_LABELS = {
  COMPLETED: 'Completed',
  ONGOING: 'Ongoing',
  NOT_STARTED: 'Not started',
  ENDED_NOT_COMPLETED: 'Ended, not completed',
}

/** True when the well reported a task on the selected date. */
function hasReported(row) {
  return (row.activity?.today_reported_task_count || row.task_count) > 0
}

/**
 * A well belongs on the default list when it is either reporting or still
 * carrying unfinished work. "Unfinished" here is every open task -- incomplete
 * and ongoing together -- not just the incomplete half, or a well whose whole
 * open workload happens to be under way would fall off the list. A well that
 * did neither is still one click away under "All"; it is never dropped, only
 * moved off the first screen.
 */
function isActive(row) {
  return hasReported(row) || (row.activity?.open_task_count || 0) > 0
}

/** Joins the day's per-well rollup and the well's task activity by well id. */
function merge(dailyWells, activityWells) {
  const byId = new Map()
  for (const well of activityWells) {
    byId.set(well.well_id, {
      well_id: well.well_id,
      task_count: 0,
      status_counts: {},
      activity: well,
    })
  }
  for (const well of dailyWells) {
    const existing = byId.get(well.well_id)
    if (existing) {
      byId.set(well.well_id, { ...existing, ...well, activity: existing.activity })
    } else {
      byId.set(well.well_id, { ...well, activity: null })
    }
  }
  return [...byId.values()]
}

/** The day's tasks, grouped by well, for the "reported today" expansion. */
function groupTasksByWell(tasks) {
  const byWell = new Map()
  for (const task of tasks) {
    if (!byWell.has(task.well_id)) byWell.set(task.well_id, [])
    byWell.get(task.well_id).push(task)
  }
  return byWell
}

/**
 * One representative work label per well: the activity most of its tasks
 * belong to, plus how many other distinct activities it also ran. This is a
 * display summary only -- it never feeds back into a count or a total, and it
 * never hides a task, only how much of its description appears on the front
 * page. The full set is always one click away on the well's own detail page.
 */
function summariseWorkByWell(tasks) {
  const byWell = new Map()

  for (const task of tasks) {
    const label = task.activity_description || 'Activity not mapped'
    let entry = byWell.get(task.well_id)
    if (!entry) {
      entry = { counts: new Map(), order: [] }
      byWell.set(task.well_id, entry)
    }
    if (!entry.counts.has(label)) {
      entry.counts.set(label, 0)
      entry.order.push(label)
    }
    entry.counts.set(label, entry.counts.get(label) + 1)
  }

  const result = new Map()
  for (const [wellId, entry] of byWell) {
    let top = entry.order[0]
    for (const label of entry.order) {
      if (entry.counts.get(label) > entry.counts.get(top)) top = label
    }
    result.set(wellId, { label: top, distinctCount: entry.order.length })
  }
  return result
}

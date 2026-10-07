import { useState } from 'react'
import ExplainPanel from '../ExplainPanel/ExplainPanel'
import { Field, QuantityTrio, statusLabel } from '../common'

/**
 * Full detail for one well on the report date.
 *
 * Each task the well ran that day is shown as its own panel -- unrelated tasks
 * are never merged into a single figure.
 */
export default function WellDetail({ result }) {
  const { well_id: wellId, report_date: reportDate, task_count: taskCount, tasks } = result

<<<<<<< HEAD
=======
  // A live well that reported nothing on the date: the backend sends its open
  // work as of the date instead, which is what the front page listed it for.
  if (!taskCount && result.activity) {
    return (
      <OpenWorkOnly
        wellId={wellId}
        reportDate={reportDate}
        activity={result.activity}
        openTasks={result.open_tasks || []}
      />
    )
  }

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  return (
    <>
      <p style={{ color: 'var(--text-muted)', marginTop: 0 }}>
        Well {wellId} · {reportDate} · {taskCount} daily task{taskCount === 1 ? '' : 's'}
        {taskCount > 1 ? ' — each task is listed separately below.' : ''}
      </p>

      {tasks.map((task) => (
        <TaskDetailPanel key={task.task_daily_id} task={task} reportDate={reportDate} />
      ))}
    </>
  )
}

/**
<<<<<<< HEAD
=======
 * A well with no daily task on the report date, shown by the open work it
 * still carries. Every figure is the backend's, exactly as the front page's
 * row shows it; nothing is estimated for the missing day.
 */
function OpenWorkOnly({ wellId, reportDate, activity, openTasks }) {
  return (
    <section className="detail-panel">
      <div className="detail-panel__head">
        <h3 className="detail-panel__title">No daily task reported on {reportDate}</h3>
      </div>
      <p style={{ color: 'var(--text-muted)', marginTop: 0 }}>
        Well {wellId} is still a live well with open work. Its task activity as of {reportDate}
        is below.
      </p>

      <div className="field-grid">
        <Field label="Open tasks" value={activity.open_task_count} mono />
        <Field label="Incomplete (not ongoing)" value={activity.incomplete_task_count} mono />
        <Field label="Ongoing" value={activity.ongoing_task_count} mono />
        <Field label="Completed" value={activity.completed_task_count} mono />
        <Field
          label="Last task date"
          value={activity.last_task_date}
          missingText="Not recorded"
        />
      </div>

      {openTasks.length ? (
        <div className="task-detail" style={{ marginTop: 14 }}>
          <div className="task-detail__head">
            <span className="task-detail__title">Open tasks on well {wellId}</span>
          </div>
          <table className="task-detail__table">
            <thead>
              <tr>
                <th scope="col">Task</th>
                <th scope="col">Activity</th>
                <th scope="col">Schedule</th>
                <th scope="col">State</th>
                <th scope="col">Actual start</th>
                <th scope="col">Actual end</th>
                <th scope="col">Last task date</th>
              </tr>
            </thead>
            <tbody>
              {openTasks.map((task, index) => (
                <tr key={`${task.task_code}-${task.schedule_id ?? index}`}>
                  <td className="task-detail__code">
                    {task.task_code || <span className="missing">Not recorded</span>}
                  </td>
                  <td>
                    {task.activity_description || task.activity_code || (
                      <span className="missing">Not mapped</span>
                    )}
                  </td>
                  <td>{task.schedule_id ?? <span className="missing">—</span>}</td>
                  <td>
                    <span className={`task-state task-state--${task.task_state}`}>
                      {TASK_STATE_LABELS[task.task_state] || task.task_state}
                    </span>
                  </td>
                  <td>{task.actual_start || <span className="missing">—</span>}</td>
                  <td>{task.actual_end || <span className="missing">—</span>}</td>
                  <td>{task.last_task_date || <span className="missing">—</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  )
}

/** Human labels for the backend's task states. The code stays authoritative. */
const TASK_STATE_LABELS = {
  COMPLETED: 'Completed',
  ONGOING: 'Ongoing',
  NOT_STARTED: 'Not started',
  ENDED_NOT_COMPLETED: 'Ended, not completed',
}

/**
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
 * One task's detail panel, including its own AI explanation.
 *
 * The explanation is scoped to this one task, so it renders inside this same
 * panel rather than at the top of the page -- the answer belongs next to the
 * task it describes, not detached from it.
 */
function TaskDetailPanel({ task, reportDate }) {
  const [explaining, setExplaining] = useState(false)

  return (
    <section className="detail-panel">
      <div className="detail-panel__head">
        <h3 className="detail-panel__title">
          {task.activity_description || (
            <span className="missing">Activity description not mapped</span>
          )}
        </h3>
        <div style={{ display: 'flex', gap: 9, alignItems: 'center' }}>
          <span className={`status-pill status-pill--small status-pill--${task.quantity_status}`}>
            {statusLabel(task.quantity_status)}
          </span>
          <button type="button" className="btn" onClick={() => setExplaining((value) => !value)}>
            {explaining ? 'Hide explanation' : 'Explain this task'}
          </button>
        </div>
      </div>

      <div className="field-grid">
        <Field label="Well ID" value={task.well_id} />
        <Field label="Date" value={task.action_on} />
        <Field label="Task Code" value={task.task_code} mono missingText="Not recorded" />
        <Field label="Activity ID" value={task.activity_id} mono missingText="Not derivable" />
        <Field label="Activity Code" value={task.activity_code} mono missingText="Not mapped" />
        <Field label="WBS" value={task.wbs} missingText="Not mapped" />
<<<<<<< HEAD
        <Field label="Crew" value={task.crew_code} missingText="Not mapped" />
=======
        <Field label="Crew (WBS)" value={task.crew_code} missingText="Not mapped" />
        {/*
          The crew actually recorded on this task, by id. Distinct from the
          WBS crew code above: that one says which crew the activity mapping
          calls for, this one says which crew instance the record names. An
          operator chasing up a task needs the id, not only the type name.
        */}
        <Field
          label="Crew ID (assigned)"
          value={task.crew_id}
          missingText="No crew recorded on this task"
        />
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        <Field
          label="UOM"
          value={task.uom_code}
          missingText={
            task.activity_uom
              ? `Not recorded on this task (activity master: ${task.activity_uom})`
              : 'Not recorded'
          }
        />
        {task.activity_uom ? (
          <Field label="Activity Master UOM" value={task.activity_uom} missingText="Not recorded" />
        ) : null}
        <Field
          label="Daily Completed"
          value={
            task.daily_completed === null || task.daily_completed === undefined
              ? null
              : task.daily_completed
                ? 'Yes'
                : 'No'
          }
          missingText="Not recorded"
        />
        <Field label="PH Name" value={task.ph_name} missingText="Not recorded" />
        <Field label="Mapping Status" value={task.mapping_status} />
        <Field label="Quantity Status" value={statusLabel(task.quantity_status)} />
        <Field label="Schedule ID" value={task.schedule_id} missingText="Not recorded" />
      </div>

      <QuantityTrio
        planned={task.planned}
        actual={task.actual_quantity}
        progress={task.progress}
        uom={task.uom_code}
      />

      <CrewPersonnel task={task} />

      {task.data_quality_flags?.length ? (
        <div className="dq" style={{ marginTop: 14, marginBottom: 0 }}>
          <div className="dq__title">Data quality on this task</div>
          <div className="dq__list">
            {task.data_quality_flags.map((flag) => (
              <span className="flag-chip" key={flag}>
                {flag}
              </span>
            ))}
          </div>
          <div className="dq__note">
            These conditions are reported separately and do not change the planned
            or actual quantities above.
            {task.group_row_count > 1
              ? ` This task was recorded on ${task.group_row_count} rows; one was selected as authoritative.`
              : ''}
          </div>
        </div>
      ) : null}

      {explaining ? (
        <ExplainPanel
          request={{
            report_date: reportDate,
            scope: 'task',
            well_id: task.well_id,
            task_daily_id: task.task_daily_id,
          }}
          onClose={() => setExplaining(false)}
        />
      ) : null}
    </section>
  )
}

/**
 * Personnel behind the task: Well -> WBS -> Activity -> Task (already shown
 * in the fields above) -> Crew -> Supervisor -> Employees.
 *
 * Resolved from the specific crew instance assigned to this task
 * (task_daily.crew_id), NOT activity_master_csv.crew_code above -- that field
 * stays the sole business-rule WBS crew. This is additive "who actually
 * worked it" evidence, absent whenever the task carries no crew_id or the
 * crew instance has no supervisor/roster on file.
 */
export function CrewPersonnel({ task }) {
<<<<<<< HEAD
  const hasAnyPersonnel =
    task.crew_type_name || task.crew_supervisor || (task.crew_employees && task.crew_employees.length)
=======
  const hasCrewId = task.crew_id !== null && task.crew_id !== undefined
  const hasAnyPersonnel =
    hasCrewId ||
    task.crew_type_name ||
    task.crew_supervisor ||
    (task.crew_employees && task.crew_employees.length)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  if (!hasAnyPersonnel) return null

  return (
    <div className="crew-chain">
<<<<<<< HEAD
      <div className="crew-chain__title">Crew behind this task</div>
=======
      <div className="crew-chain__title">
        Crew behind this task
        {hasCrewId ? <span className="crew-chain__id">Crew ID {task.crew_id}</span> : null}
      </div>
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
      <div className="crew-chain__path">
        <span className="crew-chain__well">Well {task.well_id}</span>
        <span className="crew-chain__sep">›</span>
        <span>{task.wbs || 'Not mapped'}</span>
        <span className="crew-chain__sep">›</span>
        <span>{task.activity_code || 'unmapped'}</span>
        <span className="crew-chain__sep">›</span>
<<<<<<< HEAD
=======
        <span>
          {hasCrewId ? `Crew ${task.crew_id}` : <span className="missing">Crew not recorded</span>}
        </span>
        <span className="crew-chain__sep">›</span>
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        <span>{task.crew_type_name || <span className="missing">Crew type not recorded</span>}</span>
        <span className="crew-chain__sep">›</span>
        <span className="crew-chain__supervisor">
          {task.crew_supervisor || <span className="missing">Supervisor not recorded</span>}
        </span>
      </div>
      <dl className="kv" style={{ marginTop: 10 }}>
<<<<<<< HEAD
=======
        <dt>Crew ID</dt>
        <dd>
          {hasCrewId ? (
            <span className="num">{task.crew_id}</span>
          ) : (
            <span className="missing">Not recorded</span>
          )}
        </dd>
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        <dt>Supervisor</dt>
        <dd>{task.crew_supervisor || <span className="missing">Not recorded</span>}</dd>
        <dt>Employees</dt>
        <dd>
          {task.crew_employees && task.crew_employees.length ? (
            <ul className="crew-chain__employees">
              {task.crew_employees.map((name) => (
                <li key={name}>{name}</li>
              ))}
            </ul>
          ) : (
            <span className="missing">No roster on file</span>
          )}
        </dd>
      </dl>
    </div>
  )
}

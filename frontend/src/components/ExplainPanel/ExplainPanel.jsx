import { useEffect, useState } from 'react'
import api from '../../services/api'
<<<<<<< HEAD
=======
import {
  explainKey,
  getCachedExplanation,
  setCachedExplanation,
} from '../../services/explainCache'
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
import { Spinner, statusLabel } from '../common'
import MarkdownLite from './MarkdownLite'

/**
 * The Explain / Elaborate panel.
 *
 * The request names a scope; the backend rebuilds the deterministic evidence
 * for it and asks the LLM to describe it. When the LLM is unavailable the panel
 * says so plainly -- it never shows an invented explanation, and the rest of
 * the dashboard is unaffected.
 */
/**
 * One in-flight explanation per identical request, shared by every caller.
 *
 * This is the client-side half of the backend's per-(date, evidence) lock, and
 * it exists for a bug that lock cannot fix from its side. React's development
 * StrictMode mounts each component twice, so opening a panel fired the request
 * twice: the first was aborted by the cleanup, the second arrived after the
 * backend had already generated and cached the answer -- and came back marked
 * as cached. The very first time an operator opened a well's summary, the
 * panel therefore said "reused". The answer was honest about what the second
 * request got; the second request was the problem.
 *
 * Sharing the promise means the remount joins the request already running
 * rather than starting another, so the response the panel shows is the one
 * that was actually generated for it. A genuinely later request -- reopening
 * the panel, another well, another date -- finds nothing in flight and asks
 * again, and is then correctly told the answer was cached.
 */
const inFlight = new Map()

function requestExplanation(key) {
  const existing = inFlight.get(key)
  if (existing) return existing
  // Deliberately not abortable: aborting never stopped the backend from
  // finishing and caching the work anyway, and it is what made a fresh
  // answer look reused. A superseded response is discarded by the caller
  // instead -- see the `active` flag below.
  const pending = api.explain(JSON.parse(key)).finally(() => inFlight.delete(key))
  inFlight.set(key, pending)
  return pending
}

export default function ExplainPanel({ request, onClose }) {
<<<<<<< HEAD
  const [state, setState] = useState({ loading: true, result: null, error: null })

  // Keyed on the values that identify the request rather than on the object
  // itself: a caller that rebuilds an equivalent object on every render would
  // otherwise re-fire this effect, asking for the same explanation again.
  const requestKey = request ? JSON.stringify(request) : null

  useEffect(() => {
    if (!requestKey) return undefined
    let active = true

    setState({ loading: true, result: null, error: null })
    requestExplanation(requestKey)
      .then((result) => active && setState({ loading: false, result, error: null }))
      .catch((error) => {
        if (!active || error.name === 'AbortError') return
=======
  // Keyed on the values that identify the request rather than on the object
  // itself: a caller that rebuilds an equivalent object on every render would
  // otherwise re-fire this effect, asking for the same explanation again.
  const requestKey = explainKey(request)

  // Seeded from the session cache during the very first render, not in an
  // effect afterwards: an explanation the operator has already read on this
  // page must come straight back when they navigate to it again -- no spinner,
  // no request. Closing a well and reopening it, or drilling in and pressing
  // Back, is exactly this case. The cache lives only as long as the page does
  // (see services/explainCache.js).
  const [state, setState] = useState(() => initialState(requestKey))

  useEffect(() => {
    if (!requestKey) return undefined

    const cached = getCachedExplanation(requestKey)
    if (cached) {
      setState({ loading: false, result: cached, error: null })
      return undefined
    }

    let active = true
    setState({ loading: true, result: null, error: null })
    requestExplanation(requestKey)
      .then((result) => {
        // Only a successful explanation is kept -- the same rule the backend
        // applies to its own cache. A provider outage comes back as a
        // perfectly good HTTP 200 carrying `available: false`, and caching
        // that would leave the panel repeating "unavailable" for the rest of
        // the session, long after the provider recovered, with no way to
        // retry short of a full refresh.
        if (result?.available) setCachedExplanation(requestKey, result)
        if (active) setState({ loading: false, result, error: null })
      })
      .catch((error) => {
        if (!active || error.name === 'AbortError') return
        // A thrown failure is likewise never cached: the next open should try
        // again rather than replay an outage the operator has since had fixed.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        setState({ loading: false, result: null, error })
      })

    return () => {
      active = false
    }
  }, [requestKey])

  if (!request) return null

  const { loading, result, error } = state

  // Colour says whether this text was just generated or reused, instead of a
  // "cached" label. A reused answer is green: nothing underneath it has
  // changed since it was written, which is the reassuring case and the one
  // worth marking. A fresh generation gets the panel's ordinary styling --
  // it is the normal thing to happen, not a status to flag, and colouring it
  // would imply something had gone wrong or been spent. Neither loading nor
  // an unavailable result carries a tone: there is nothing generated or
  // reused yet to signal.
  //
  // `cached` is read from the response exactly as the backend reports it
  // (ExplainResponse.cached), never inferred from timing or from having asked
  // before: only the backend knows whether its own evidence hash hit the
  // cache, and a first generation must never be able to look like a reuse.
  const cached = Boolean(result?.available && result.cached)

  return (
    <section
      className={cached ? 'explain explain--cached' : 'explain'}
      title={
        result?.available
          ? cached
            ? 'Reused from an earlier identical request -- nothing about this selection has changed, so no new AI call was made.'
<<<<<<< HEAD
            : 'Generated by the AI just now for this exact selection.'
=======
            : 'Generated by the AI for this exact selection.'
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
          : undefined
      }
    >
      <div className="explain__head">
        <h3 className="explain__title">AI explanation — {describeScope(request)}</h3>
        <button type="button" className="btn btn--ghost" onClick={onClose}>
          Close
        </button>
      </div>

      {loading ? (
        <div className="explain__body" style={{ color: 'var(--text-muted)' }}>
          <Spinner /> Generating an explanation from the deterministic evidence…
        </div>
      ) : null}

      {error ? (
        <Unavailable message={error.message} />
      ) : null}

      {result && !result.available ? <Unavailable message={result.error} /> : null}

      {result?.available ? (
        <div className="explain__body">
          <MarkdownLite text={result.explanation} />
        </div>
      ) : null}

<<<<<<< HEAD
=======
      <AgentVerdict result={result} />

      {/*
        The crew suggestion, stated deterministically beside the prose rather
        than only inside it. The prose is the model's wording of this same
        evidence; this card is the evidence itself -- above all the crew id,
        which is the one thing an operator needs in order to actually go and
        find the crew, and the one thing a sentence can most easily leave out.
        Rendered from result.evidence.crew_suggestion, which SQL computed; this
        component selects fields and never derives one.
      */}
      <CrewSuggestionCard suggestion={result?.evidence?.crew_suggestion} />

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
      {/*
        Three ways to check the same answer, each collapsed so the panel stays
        skimmable:

        * the evidence, which is exactly what the model was given;
        * the SQL those figures came from, as it is actually executed;
        * the individual task records behind the counts, with the reason each
          one is counted.

        The model never sees the last two. It is handed finished figures, and
        showing it the query would only invite it to reason about SQL instead
        of explaining what the query already decided.
      */}
<<<<<<< HEAD
=======
      <AgentSteps result={result} />

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
      {result?.evidence && Object.keys(result.evidence).length ? (
        <details className="explain__drawer">
          <summary className="explain__drawer-summary">
            What is sent to AI model for summary
          </summary>
          {result.evidence_withheld_from_model?.length ? (
            <div className="explain__note">
              Returned below for audit but deliberately not sent to the model:{' '}
              {result.evidence_withheld_from_model.join(', ')} — there was nothing in it
              for the model to describe.
            </div>
          ) : null}
          <pre className="explain__pre">{JSON.stringify(result.evidence, null, 2)}</pre>
        </details>
      ) : null}

      {result?.sql_sources?.length ? (
        <details className="explain__drawer">
          <summary className="explain__drawer-summary">SQL</summary>
          <div className="explain__note">
            The queries these figures came from, as executed. The report date travels as a
            bound parameter and is never spliced into the text, so it is listed beside each
            query rather than shown inside it.
          </div>
          {result.sql_sources.map((source) => (
            <div key={source.file} className="explain__sql">
              <div className="explain__sql-head">
                {source.label}
                <span className="explain__sql-file">{source.file}</span>
              </div>
              {source.parameters?.length ? (
                <div className="explain__sql-params">{source.parameters.join(' · ')}</div>
              ) : null}
              <pre className="explain__pre">{source.sql}</pre>
            </div>
          ))}
        </details>
      ) : null}

      {result?.proof?.length ? (
        <details className="explain__drawer">
          <summary className="explain__drawer-summary">
            Proof — the {result.proof.length} open task
            {result.proof.length === 1 ? '' : 's'} behind these counts
          </summary>
          <div className="explain__note">
            Every task not recorded as completed, and which of the row's two figures it
            counts toward — the two do not overlap, so they add up to the total. The reason
            restates what that task's own record says; it is not an interpretation of it.{' '}
            {result.proof_note || ''}
          </div>
          <div className="explain__table-wrap">
            <table className="proof-table">
              <thead>
                <tr>
                  <th scope="col">Well</th>
                  <th scope="col">Task</th>
                  <th scope="col">Schedule</th>
                  <th scope="col" className="proof-table__description">Description</th>
                  <th scope="col">Incomplete</th>
                  <th scope="col">Ongoing</th>
                  <th scope="col">Actual start</th>
                  <th scope="col">Actual end</th>
                  <th scope="col">Last task date</th>
                </tr>
              </thead>
              <tbody>
                {result.proof.flatMap((row, index) => [
                  <tr key={`${row.task_code}-${row.schedule_id ?? index}`}>
                    <td>{row.well_id}</td>
                    <td className="proof-table__code">
                      {row.task_code || <span className="missing">Not recorded</span>}
                    </td>
                    <td>{row.schedule_id ?? <span className="missing">—</span>}</td>
                    <td className="proof-table__description">
                      {row.description || row.activity_code || (
                        <span className="missing">Not mapped</span>
                      )}
                    </td>
                    <td className="proof-table__flag">{row.counts_as_incomplete ? 'Yes' : 'No'}</td>
                    <td
                      className={
                        row.counts_as_ongoing
                          ? 'proof-table__flag task-state--ONGOING'
                          : 'proof-table__flag'
                      }
                    >
                      {row.counts_as_ongoing ? 'Yes' : 'No'}
                    </td>
                    <td>{row.actual_start || <span className="missing">—</span>}</td>
                    <td>{row.actual_end || <span className="missing">—</span>}</td>
                    <td>{row.last_task_date || <span className="missing">—</span>}</td>
                  </tr>,
                  /*
                    The reason gets the full width of the table rather than a
                    tenth of it: it is the column an operator opened this for,
                    and squeezed into a narrow cell beside nine others it
                    wrapped into an unreadable ribbon.
                  */
                  <tr key={`${row.task_code}-${row.schedule_id ?? index}-why`} className="proof-table__why">
                    <td colSpan={9}>{row.reason}</td>
                  </tr>,
                ])}
              </tbody>
            </table>
          </div>
        </details>
      ) : null}
    </section>
  )
}

<<<<<<< HEAD
=======
/**
 * What the panel shows for a task's crew suggestion, straight from the
 * deterministic evidence.
 *
 * Exactly one of `suggested_crew` (a stalled task's possible alternative) and
 * `consult_crew` (an in-progress task's "who has done this before" mention) is
 * ever present -- the backend decides which, never this component and never
 * the model. Both carry the crew id, and the crew currently recorded on the
 * task carries its own, so the two can be told apart at a glance instead of
 * having to be matched up by crew type name.
 */
export function CrewSuggestionCard({ suggestion }) {
  if (!suggestion) return null

  const crew = suggestion.suggested_crew || suggestion.consult_crew
  const current = suggestion.current_crew
  const currentRecorded = current?.recorded && current?.crew_id !== null && current?.crew_id !== undefined

  // Nothing to show: no crew either way, and no reason worth stating.
  if (!crew && !currentRecorded && !suggestion.no_suggestion_reason) return null

  const isConsult = !suggestion.suggested_crew && Boolean(suggestion.consult_crew)

  return (
    <div className="crew-suggestion">
      <div className="crew-suggestion__title">
        {crew
          ? isConsult
            ? 'Crew with experience of this activity'
            : 'Possible alternative crew'
          : 'Crew'}
      </div>

      <div className="crew-suggestion__grid">
        <div className="crew-suggestion__cell">
          <div className="crew-suggestion__label">Crew currently on this task</div>
          <div className="crew-suggestion__value">
            {currentRecorded ? (
              <>
                <span className="crew-suggestion__id">Crew ID {current.crew_id}</span>
                {current.crew_type ? <span> · {current.crew_type}</span> : null}
                {current.supervisor ? <span> · {current.supervisor}</span> : null}
              </>
            ) : (
              <span className="missing">No crew recorded on this task record</span>
            )}
          </div>
        </div>

        {crew ? (
          <div className="crew-suggestion__cell">
            <div className="crew-suggestion__label">
              {isConsult ? 'Worth asking for input' : 'Suggested crew'}
            </div>
            <div className="crew-suggestion__value">
              <span className="crew-suggestion__id crew-suggestion__id--suggested">
                Crew ID {crew.crew_id}
              </span>
              {crew.crew_type ? <span> · {crew.crew_type}</span> : null}
              {crew.supervisor ? <span> · {crew.supervisor}</span> : null}
            </div>
          </div>
        ) : null}
      </div>

      {crew ? (
        <div className="crew-suggestion__why">
          Completed this activity <span className="num">{crew.historical_completed_task_count}</span>{' '}
          time{crew.historical_completed_task_count === 1 ? '' : 's'} on{' '}
          <span className="num">{crew.distinct_completed_well_count}</span> distinct well
          {crew.distinct_completed_well_count === 1 ? '' : 's'}
          {crew.typical_completion_days !== null && crew.typical_completion_days !== undefined ? (
            <>
              , typically in <span className="num">{crew.typical_completion_days}</span> day
              {Number(crew.typical_completion_days) === 1 ? '' : 's'} (median)
            </>
          ) : null}
          {crew.most_recent_success_date ? (
            <>
              , most recently on <span className="num">{crew.most_recent_success_date}</span>
            </>
          ) : null}
          .{' '}
          {crew.derived_availability === 'NO_CURRENT_UNFINISHED_TASK'
            ? 'No current unfinished task was found for this crew in the available records — this is not an authoritative availability status.'
            : ''}
          {isConsult
            ? ' Advisory only: this task is already progressing, so no change is being proposed.'
            : ' Advisory only: nothing here assigns or reassigns a crew.'}
        </div>
      ) : null}

      {!crew && suggestion.no_suggestion_reason ? (
        <div className="crew-suggestion__why">{suggestion.no_suggestion_reason}</div>
      ) : null}
    </div>
  )
}

/**
 * The verifier's verdict on an agent answer. Every figure in the text was
 * checked against the tool results before it was shown; an answer that still
 * failed after its revisions is said to be unverified, with the reasons,
 * rather than shown as if it had passed. Absent for a classic answer.
 */
export function AgentVerdict({ result }) {
  if (!result?.available || !result.verification || !('passed' in result.verification)) return null
  const { passed, issues = [], checked_numbers: checked } = result.verification
  return (
    <div className={`agent-verdict ${passed ? 'agent-verdict--ok' : 'agent-verdict--warn'}`}>
      {passed ? (
        <>
          ✓ Verified — {checked ?? 0} figure{checked === 1 ? '' : 's'} checked against the data
          {result.revisions ? `, after ${result.revisions} revision${result.revisions === 1 ? '' : 's'}` : ''}.
        </>
      ) : (
        <>
          <strong>Not verified.</strong> The verifier still found:
          <ul className="agent-verdict__issues">
            {issues.map((issue) => (
              <li key={issue}>{issue}</li>
            ))}
          </ul>
        </>
      )}
    </div>
  )
}

/**
 * How an agent reached its answer: the plan, each tool it called (with its
 * arguments and whether it succeeded), the writer and verifier steps, and the
 * model each role used. Collapsed, like the other drawers.
 */
export function AgentSteps({ result }) {
  if (!result?.trace?.length) return null
  const models = Object.entries(result.models || {})
  return (
    <details className="explain__drawer">
      <summary className="explain__drawer-summary">
        Agent steps — {result.trace.length} step{result.trace.length === 1 ? '' : 's'}
        {result.duration_s ? `, ${result.duration_s} s` : ''}
      </summary>
      {models.length ? (
        <div className="explain__note">
          Models: {models.map(([role, model]) => `${role} ${model}`).join(' · ')}
        </div>
      ) : null}
      <ol className="agent-steps">
        {result.trace.map((step, index) => (
          <li key={index} className={step.ok === false ? 'agent-steps__step agent-steps__step--failed' : 'agent-steps__step'}>
            <span className="agent-steps__node">{step.tool || step.node}</span>
            {step.args && Object.keys(step.args).length ? (
              <code className="agent-steps__args">{JSON.stringify(step.args)}</code>
            ) : null}
            <span className="agent-steps__detail">
              {[step.why, step.mode, step.error, step.passed === undefined ? null : step.passed ? 'passed' : `${step.issues} issue(s)`]
                .filter(Boolean)
                .join(' — ')}
            </span>
            <span className="agent-steps__time">{step.duration_s} s</span>
          </li>
        ))}
      </ol>
    </details>
  )
}

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
function Unavailable({ message }) {
  return (
    <div className="explain__body">
      <strong>AI explanation unavailable.</strong>
      {'\n'}The underlying daily task data is still available and unchanged.
      {message ? `\n\n${message}` : ''}
    </div>
  )
}

<<<<<<< HEAD
=======
/**
 * First render's state: an already-read explanation appears immediately,
 * anything else starts in its loading state. Only the very first render uses
 * this; the effect owns every state change after it.
 */
function initialState(requestKey) {
  const cached = requestKey ? getCachedExplanation(requestKey) : undefined
  if (cached) return { loading: false, result: cached, error: null }
  return { loading: true, result: null, error: null }
}

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
/** Names the scope from narrowest key to broadest, matching the drill-down. */
function describeScope(request) {
  if (request.task_daily_id) return `task on well ${request.well_id ?? ''}`.trim()
  if (request.well_id) return `well ${request.well_id}`

  const parts = []
  if (request.status) parts.push(statusLabel(request.status))
  if (request.wbs) parts.push(request.wbs)
  if (request.activity_code) parts.push(request.activity_code)
  if (parts.length) return parts.join(' · ')

  if (request.uom) return `UOM ${request.uom}`
  return `all daily work on ${request.report_date}`
}

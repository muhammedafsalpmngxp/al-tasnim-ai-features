import { useEffect, useRef, useState } from 'react'
import api from '../../services/api'
import './BootstrapGate.css'

/**
 * Blocks the Daily Report UI until DYNAMIC_DB reports READY.
 *
 * EVERY STATE SHOWN HERE COMES FROM THE BACKEND. This component polls
 * GET /api/bootstrap/status on a fixed interval and renders exactly what
 * comes back -- it never invents a percentage, never advances a step on a
 * timer, and never assumes success while waiting. If the backend has not
 * started a bootstrap run at all yet (a request race at the very first
 * paint), that is shown as "starting", not skipped over.
 */
const CHECKLIST = [
  { state: 'CONNECTING', label: 'Connecting to database' },
  { state: 'INTROSPECTING', label: 'Inspecting schema' },
  { state: 'GENERATING_FINGERPRINT', label: 'Generating schema fingerprint' },
  { state: 'COMPARING_FINGERPRINT', label: 'Comparing schema fingerprint' },
  { state: 'VALIDATING', label: 'Validating capabilities' },
  { state: 'COMPILING', label: 'Compiling affected capabilities' },
  { state: 'LOADING_ARTIFACTS', label: 'Loading verified artifacts' },
  { state: 'READY', label: 'Loading Daily Report' },
]

const POLL_MS = 1200

function stepGlyph(step, currentState, ready) {
  if (step.state === 'READY' && ready) return '✓'
  if (step.status === 'done') return '✓'
  if (step.status === 'skipped') return '○'
  if (step.status === 'failed') return '✗'
  if (currentState === step.state) return '⟳'
  return '○'
}

function StatusChecklist({ status }) {
  const stepsByName = new Map((status.steps || []).map((s) => [s.name, s]))
  return (
    <ul className="bootstrap-checklist" aria-live="polite">
      {CHECKLIST.map((item) => {
        const found = stepsByName.get(item.state)
        const merged = found || { name: item.state, status: 'pending' }
        const glyph = stepGlyph(merged, status.state, status.ready)
        const active = status.state === item.state && merged.status !== 'done'
        return (
          <li
            key={item.state}
            className={`bootstrap-step bootstrap-step--${merged.status}${active ? ' bootstrap-step--active' : ''}`}
          >
            <span className="bootstrap-step__glyph" aria-hidden="true">
              {glyph}
            </span>
            <span className="bootstrap-step__label">{item.label}</span>
            {merged.duration_ms != null && (
              <span className="bootstrap-step__duration">{Math.round(merged.duration_ms)} ms</span>
            )}
            {merged.detail && <span className="bootstrap-step__detail">{merged.detail}</span>}
          </li>
        )
      })}
    </ul>
  )
}

function FailurePanel({ status }) {
  const error = status.error || {}
  return (
    <div className="bootstrap-failure" role="alert">
      <h2>Dynamic DB failed to start</h2>
      <p className="bootstrap-failure__summary">
        {error.message || 'The database could not be prepared for the Daily Report.'}
      </p>
      <dl className="bootstrap-failure__meta">
        <dt>Run ID</dt>
        <dd>{status.run_id || 'unknown'}</dd>
        <dt>Timestamp</dt>
        <dd>{status.updated_at || status.started_at || 'unknown'}</dd>
        <dt>Failure type</dt>
        <dd>{error.type || 'unknown'}</dd>
      </dl>
      <p className="bootstrap-failure__log-pointer">
        Full log: <code>DYNAMIC_DB/logs/runs/{status.run_id || '&lt;run-id&gt;'}.json</code> -- or run{' '}
        <code>python -m dynamic_db.cli logs --run-id {status.run_id || '&lt;run-id&gt;'}</code> from the
        DYNAMIC_DB project.
      </p>
    </div>
  )
}

export default function BootstrapGate({ children }) {
  const [status, setStatus] = useState(null)
  const [pollError, setPollError] = useState(null)
  const timerRef = useRef(null)

  useEffect(() => {
    let cancelled = false
    const controller = new AbortController()

    async function poll() {
      try {
        const next = await api.bootstrapStatus(controller.signal)
        if (cancelled) return
        setStatus(next)
        setPollError(null)
        if (next.ready || next.state === 'FAILED') {
          return // stop polling once settled; nothing more will change
        }
      } catch (error) {
        if (cancelled || error.name === 'AbortError') return
        setPollError(error)
      }
      if (!cancelled) {
        timerRef.current = window.setTimeout(poll, POLL_MS)
      }
    }

    poll()

    return () => {
      cancelled = true
      controller.abort()
      if (timerRef.current) window.clearTimeout(timerRef.current)
    }
  }, [])

  if (status?.ready) {
    return children
  }

  return (
    <div className="bootstrap-gate">
      <div className="bootstrap-card">
        <h1>Initializing Daily Report</h1>
        {pollError && (
          <p className="bootstrap-poll-error">
            Could not reach the backend to check startup status. Retrying...
          </p>
        )}
        {status ? (
          status.state === 'FAILED' ? (
            <FailurePanel status={status} />
          ) : (
            <>
              <StatusChecklist status={status} />
              {status.schema_changed && (
                <p className="bootstrap-note">
                  Schema change detected: {status.change_summary}. Affected capabilities are being
                  recompiled.
                </p>
              )}
            </>
          )
        ) : (
          <p className="bootstrap-note">Waiting for the backend to report its startup state...</p>
        )}
      </div>
    </div>
  )
}

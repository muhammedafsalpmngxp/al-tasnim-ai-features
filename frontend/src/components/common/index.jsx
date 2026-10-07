/**
 * Shared presentation primitives.
 *
 * These render values, they never derive them. Anything shown here was already
 * computed by the backend.
 */

/** Human label for a backend status code. The code itself stays authoritative. */
export const STATUS_LABELS = {
  ON_PLAN: 'On Plan',
  ABOVE_PLAN: 'Above Plan',
  BELOW_PLAN: 'Below Plan',
  NO_ACTUAL: 'No Actual',
  NOT_VALIDATED: 'Not Validated',
}

/**
 * What each status means, in the operator's words rather than the enum's.
 *
 * These restate the rule validation_service applied; they never add to it. In
 * particular neither Above Plan nor Below Plan is described as an error,
 * because the business rules do not define one.
 */
export const STATUS_DESCRIPTIONS = {
  ON_PLAN: 'Reported actual equals the planned quantity.',
  ABOVE_PLAN: 'Reported actual is above the planned quantity. Not an error.',
  BELOW_PLAN: 'Reported actual is below the planned quantity. Not, on its own, an error.',
  NO_ACTUAL: 'No actual quantity was reported for the task on this date.',
  NOT_VALIDATED: 'An actual was reported, but there is no planned quantity to compare it against.',
}

/**
 * Display order of statuses, matching the backend's QuantityStatus order.
 *
 * The summary already arrives in this order, so this is only used where the UI
 * renders a status_counts map. Unknown codes are appended as they arrive.
 */
export const STATUS_ORDER = [
  'ON_PLAN',
  'ABOVE_PLAN',
  'BELOW_PLAN',
  'NO_ACTUAL',
  'NOT_VALIDATED',
]

export function statusLabel(code) {
  return STATUS_LABELS[code] || code
}

export function statusDescription(code) {
  return STATUS_DESCRIPTIONS[code] || ''
}

/** Orders a status_counts object without inventing or dropping any entry. */
export function orderedStatuses(statusCounts = {}) {
  const known = STATUS_ORDER.filter((code) => code in statusCounts)
  const extra = Object.keys(statusCounts).filter((code) => !STATUS_ORDER.includes(code))
  return [...known, ...extra]
}

/** Renders a value, or an explicit "not recorded" marker. Never a substitute. */
export function Value({ value, missingText = 'Not recorded', mono = false }) {
  if (value === null || value === undefined || value === '') {
    return <span className="missing">{missingText}</span>
  }
  return <span className={mono ? 'field__value--mono' : undefined}>{String(value)}</span>
}

export function Field({ label, value, missingText, mono }) {
  return (
    <div className="field">
      <div className="field__label">{label}</div>
      <div className="field__value">
        <Value value={value} missingText={missingText} mono={mono} />
      </div>
    </div>
  )
}

export function Spinner() {
  return <span className="spinner" aria-hidden="true" />
}

export function LoadingBlock({ message = 'Loading daily task data…' }) {
  return (
    <div className="loading-block" role="status">
      <Spinner />
      <span>{message}</span>
    </div>
  )
}

export function Notice({ variant = 'info', title, children }) {
  const className = variant === 'info' ? 'notice' : `notice notice--${variant}`
  return (
    <div className={className} role={variant === 'error' ? 'alert' : undefined}>
      {title ? <div className="notice__title">{title}</div> : null}
      <div className="notice__body">{children}</div>
    </div>
  )
}

export function EmptyState({ title, children }) {
  return (
    <div className="empty">
      <div className="empty__title">{title}</div>
      <div>{children}</div>
    </div>
  )
}

/**
 * Database and network failures read differently from an empty day, so they are
 * presented differently. No data is ever substituted to fill the gap.
 */
export function ErrorState({ error, onRetry }) {
  const isDatabase = error?.kind === 'database'
  const isNetwork = error?.kind === 'network'
  const title = isDatabase
    ? 'The database could not be reached'
    : isNetwork
      ? 'The Daily Morning Brief API could not be reached'
      : 'The request could not be completed'

  return (
    <Notice variant="error" title={title}>
      <p style={{ margin: '0 0 10px' }}>{error?.message}</p>
      <p style={{ margin: '0 0 12px' }}>
        No daily task data is shown, because no data was returned. Nothing on this
        screen is estimated or substituted.
      </p>
      {onRetry ? (
        <button type="button" className="btn" onClick={onRetry}>
          Try again
        </button>
      ) : null}
    </Notice>
  )
}

/** Wraps a numeric value in the shared "this is a number" colour and weight. */
export function Num({ children }) {
  return <span className="num">{children}</span>
}

/** Quantity plus its unit. Units are never converted, so they travel together. */
export function Quantity({ value, uom }) {
  if (value === null || value === undefined || value === '') {
    return <span className="missing">—</span>
  }
  return (
    <span>
      <Num>{value}</Num>
      {uom ? <span style={{ color: 'var(--text-dim)' }}> {uom}</span> : null}
    </span>
  )
}

/**
 * Planned, Actual and Progress shown as one grouped unit wherever a task's
 * figures appear -- these three always belong together, so they are never
 * split across separate rows in a longer field list.
 */
export function QuantityTrio({ planned, actual, progress, uom }) {
  return (
    <div className="qty-trio">
      <div className="qty-trio__item">
        <div className="qty-trio__label">Planned</div>
        <div className="qty-trio__value">
          <Quantity value={planned} uom={uom} />
        </div>
      </div>
      <div className="qty-trio__item">
        <div className="qty-trio__label">Actual</div>
        <div className="qty-trio__value">
          {actual === null || actual === undefined ? (
            <span className="missing">No actual entry</span>
          ) : (
            <Quantity value={actual} uom={uom} />
          )}
        </div>
      </div>
      <div className="qty-trio__item">
        <div className="qty-trio__label">Progress</div>
        <div className="qty-trio__value">
          <Quantity value={progress} />
        </div>
      </div>
    </div>
  )
}

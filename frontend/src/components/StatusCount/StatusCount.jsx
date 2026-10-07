import { statusLabel } from '../common'

/**
 * A clickable status count.
 *
 * The number comes from the backend's deterministic classification. Clicking it
 * asks the backend for exactly the rows behind it -- this component never
 * filters or counts anything itself.
 */
export default function StatusCount({ status, count, onSelect, small = false, title }) {
  const disabled = !count || !onSelect
  const className = [
    'status-pill',
    `status-pill--${status}`,
    small ? 'status-pill--small' : '',
  ]
    .filter(Boolean)
    .join(' ')

  return (
    <button
      type="button"
      className={className}
      disabled={disabled}
      onClick={disabled ? undefined : () => onSelect(status)}
      title={
        title ||
        (disabled
          ? `No tasks with status ${statusLabel(status)}`
          : `Show the ${count} task(s) classified ${statusLabel(status)}`)
      }
    >
      <span className="status-pill__count">{count}</span>
      <span>{statusLabel(status)}</span>
    </button>
  )
}

import useApiResource from '../../hooks/useApiResource'
import api from '../../services/api'
import { Num, Spinner } from '../common'

/** Live wells first: the page exists to explore the work still under way. */
export const DEFAULT_STATUS = 'live'
export const DEFAULT_FILTERS = Object.freeze({ category: 'all', function: 'all', completionType: 'all', rig: 'all' })

const STATUS_VIEWS = [
  { value: 'live', label: 'Live' },
  { value: 'completed', label: 'Completed' },
  { value: 'all', label: 'All' },
]

/** Each filter: the key the page keeps, the backend's options key, and its label. */
const FILTERS = [
  { key: 'category', option: 'category', label: 'Well Category' },
  { key: 'function', option: 'function', label: 'Well Function' },
  { key: 'completionType', option: 'completion_type', label: 'Completion Type' },
  { key: 'rig', option: 'rig', label: 'Rig' },
]

/**
 * The backend's status code in the words this page uses. INCOMPLETE is a well
 * with no engineering completion date: a live well.
 */
const STATUS_WORDS = {
  INCOMPLETE: { label: 'Live', title: 'Live: no engineering completion date recorded' },
  COMPLETED: { label: 'Completed', title: 'Completed: engineering completion date recorded' },
}

function requestFilters(status, filters) {
  const chosen = (value) => (value && value !== 'all' ? value : undefined)
  return {
    status,
    category: chosen(filters.category),
    function: chosen(filters.function),
    completionType: chosen(filters.completionType),
    rig: chosen(filters.rig),
  }
}

/**
 * One field's wells, narrowed by status and the four filters. Every option,
 * count and row comes from `/api/field-map/{field}/wells`; a superseded
 * request is aborted, so a quick change of filter can never leave an older
 * answer on screen. Answers already fetched are kept in `cache`.
 */
export default function FieldWells({ field, status, filters, onStatusChange, onFiltersChange, cache, onSelectWell }) {
  const params = requestFilters(status, filters)
  const cacheKey = `${field.field_key}|${JSON.stringify(params)}`

  const resource = useApiResource(
    (signal) => {
      const cached = cache.current.get(cacheKey)
      if (cached) return Promise.resolve(cached)
      return api.fieldWells(field.field_key, params, signal).then((result) => {
        cache.current.set(cacheKey, result)
        return result
      })
    },
    [cacheKey],
  )

  // Never show another field's answer while this one's loads.
  const data = resource.data && resource.data.field_key === field.field_key ? resource.data : null
  const filtered = FILTERS.some((f) => filters[f.key] !== 'all')

  return (
    <div className="field-wells" aria-label={`Wells in ${field.field}`} role="region">
      <div className="field-wells__controls">
        <div className="segmented" role="group" aria-label="Well status">
          {STATUS_VIEWS.map((view) => (
            <button
              key={view.value}
              type="button"
              className={`segmented__option${status === view.value ? ' segmented__option--active' : ''}`}
              aria-pressed={status === view.value}
              onClick={() => onStatusChange(view.value)}
            >
              {view.label}
            </button>
          ))}
        </div>

        {FILTERS.map((f) => (
          <div className="date-field field-wells__filter" key={f.key}>
            <label htmlFor={`field-filter-${f.key}`}>{f.label}</label>
            <select
              id={`field-filter-${f.key}`}
              value={filters[f.key]}
              disabled={!data}
              onChange={(event) => onFiltersChange({ ...filters, [f.key]: event.target.value })}
            >
              <option value="all">All</option>
              {(data?.filters?.[f.option] || []).map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label} ({option.count})
                </option>
              ))}
            </select>
          </div>
        ))}

        {filtered ? (
          <button type="button" className="btn btn--ghost" onClick={() => onFiltersChange({ ...DEFAULT_FILTERS })}>
            Reset filters
          </button>
        ) : null}
      </div>

      {resource.error ? (
        <div className="field-map__note field-map__note--error" role="alert">
          The wells for this field could not be loaded. {resource.error.message}{' '}
          <button type="button" className="btn btn--ghost" onClick={resource.reload}>
            Try again
          </button>
        </div>
      ) : !data ? (
        <div className="field-map__note"><Spinner /> Loading wells…</div>
      ) : (
        <>
          <p className="field-wells__count" aria-live="polite">
            {resource.loading ? <Spinner /> : null}
            <Num>{data.matching_well_count}</Num>{' '}
            {status === 'live' ? 'live ' : status === 'completed' ? 'completed ' : ''}
            well{data.matching_well_count === 1 ? '' : 's'}
            {filtered ? ' match the filters' : ` in ${field.field}`}
          </p>
          {data.wells.length ? (
            <div className="table-wrap">
              <table className="data field-wells__table">
                <thead>
                  <tr>
                    <th>Well ID</th>
                    <th>Category</th>
                    <th>Function</th>
                    <th>Completion Type</th>
                    <th>Rig</th>
                    <th>Well Location</th>
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {data.wells.map((well) => (
                    <WellRow key={well.well_id} well={well} onSelectWell={onSelectWell} />
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="field-map__muted field-wells__empty">
              No {status === 'all' ? '' : `${status} `}wells in {field.field}
              {filtered ? ' match these filters.' : '.'}
            </p>
          )}
        </>
      )}
    </div>
  )
}

function WellRow({ well, onSelectWell }) {
  const words = STATUS_WORDS[well.status_code] || { label: well.status, title: well.status }
  const open = () => onSelectWell(well.well_id)
  return (
    <tr className="clickable" onClick={open}>
      <td>
        <button
          type="button"
          className="linklike field-wells__id"
          onClick={(event) => {
            event.stopPropagation()
            open()
          }}
          title={`Open well ${well.well_id}'s detail`}
          aria-label={`Open well ${well.well_id}`}
        >
          {well.well_id}
        </button>
      </td>
      <td><Cell value={well.category} /></td>
      <td><Cell value={well.function} /></td>
      <td><Cell value={well.completion_type} /></td>
      <td><Cell value={well.rig} missing="Unassigned" /></td>
      <td><Cell value={well.well_location} /></td>
      <td>
        <span
          className={`field-well-status field-well-status--${String(well.status_code).toLowerCase()}`}
          title={words.title}
        >
          {words.label}
        </span>
      </td>
    </tr>
  )
}

function Cell({ value, missing = 'Not recorded' }) {
  if (value === null || value === undefined || value === '') return <span className="missing">{missing}</span>
  return value
}

import { Component, lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import useApiResource from '../../hooks/useApiResource'
import api from '../../services/api'
import { Num, Spinner } from '../common'
import FieldWells, { DEFAULT_FILTERS, DEFAULT_STATUS } from './FieldWells'

// Leaflet is only loaded when the map is actually shown.
const FieldMapCanvas = lazy(() => import('./FieldMapCanvas'))

const SEARCH_RESULTS = 8

/**
 * The Oman Field Map page: a satellite (or standard) map of Oman's petroleum
 * fields, and a selected field's wells with SQL-backed filters.
 *
 * A read-only visualisation: every figure -- which field a well belongs to,
 * whether it is live or completed, its category, function, completion type
 * and rig, how many wells a field has, where the field approximately is --
 * arrives from `/api/field-map`, already decided by SQL and the backend's
 * coordinate and basemap configuration. This component groups the fields
 * that share a position into one marker and displays; it counts and
 * classifies nothing.
 *
 * A marker is a FIELD's approximate representative position, never a well's.
 * Satellite imagery is a geographic backdrop only; it says nothing about
 * whether a well exists or is operating. A well opens the application's
 * existing well detail through `onSelectWell`.
 *
 * `initialView` / `onViewChange` let the page around it keep the selection,
 * filters and basemap while the operator visits a well and comes Back.
 */
export default function FieldMap({ onSelectWell, refreshToken = 0, initialView = null, onViewChange }) {
  // Wrapped so even a synchronous failure becomes this section's own error
  // state rather than an exception in the page around it.
  const summary = useApiResource(
    (signal) => Promise.resolve().then(() => api.fieldMap({ refresh: refreshToken > 0 }, signal)),
    [refreshToken],
  )

  return (
    <section className="field-map field-map--page" aria-labelledby="field-map-title">
      <div className="field-map__head">
        <div>
          <h2 id="field-map-title" className="field-map__title">Oman Field Map</h2>
          <p className="field-map__subtitle">
            Petroleum fields on satellite imagery, and each field&apos;s live wells
          </p>
        </div>
      </div>

      {summary.loading ? (
        <div className="field-map__note">
          <Spinner /> Loading the Oman field map…
        </div>
      ) : summary.error || !summary.data ? (
        <div className="field-map__note field-map__note--error" role="alert">
          Unable to load Oman field map.
          {summary.error ? ` ${summary.error.message}` : ''}{' '}
          <button type="button" className="btn btn--ghost" onClick={summary.reload}>
            Try again
          </button>
        </div>
      ) : (
        <MapBoundary key={refreshToken}>
          <FieldMapBody
            data={summary.data}
            onSelectWell={onSelectWell}
            initialView={initialView}
            onViewChange={onViewChange}
          />
        </MapBoundary>
      )}
    </section>
  )
}

/**
 * Anything the map itself throws stays inside this section: the rest of the
 * Daily Report never depends on it.
 */
class MapBoundary extends Component {
  constructor(props) {
    super(props)
    this.state = { failed: false }
  }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error) {
    // eslint-disable-next-line no-console
    console.error('Oman field map failed to render', error)
  }

  render() {
    if (this.state.failed) {
      return (
        <div className="field-map__note field-map__note--error" role="alert">
          Unable to load Oman field map.
        </div>
      )
    }
    return this.props.children
  }
}

/** Fields sharing one approximate position become one map location. */
function groupByPosition(fields) {
  const byId = new Map()
  for (const field of fields) {
    const id = `${field.latitude},${field.longitude}`
    if (!byId.has(id)) {
      byId.set(id, { id, latitude: field.latitude, longitude: field.longitude, fields: [] })
    }
    byId.get(id).fields.push(field)
  }
  return [...byId.values()]
}

function FieldMapBody({ data, onSelectWell, initialView, onViewChange }) {
  const fields = data.fields || []
  const unmapped = data.unmapped_fields || []
  const basemaps = data.basemaps || null
  const satelliteAvailable = Boolean(basemaps?.layers?.satellite?.url)
  const locations = useMemo(() => groupByPosition(fields), [fields])
  const locationOf = useMemo(() => {
    const index = new Map()
    for (const location of locations) {
      for (const field of location.fields) index.set(field.field_key, location.id)
    }
    return index
  }, [locations])

  const start = initialView || {}
  const [locationId, setLocationId] = useState(start.locationId ?? null)
  const [fieldKey, setFieldKey] = useState(start.fieldKey ?? null)
  const [status, setStatus] = useState(start.status || DEFAULT_STATUS)
  const [filters, setFilters] = useState(start.filters || DEFAULT_FILTERS)
  const [basemap, setBasemap] = useState(() => {
    const wanted = start.basemap || basemaps?.default || 'satellite'
    return wanted === 'satellite' && !satelliteAvailable ? 'standard' : wanted
  })
  const [showLabels, setShowLabels] = useState(start.showLabels ?? true)
  const [satelliteFailed, setSatelliteFailed] = useState(false)
  const [query, setQuery] = useState('')
  const [showUnmapped, setShowUnmapped] = useState(false)
  // Each field's wells per filter set, once fetched, for as long as this data
  // is shown. Refresh remounts this component (see the boundary's key).
  const wellsCache = useRef(new Map())

  useEffect(() => {
    onViewChange?.({ locationId, fieldKey, status, filters, basemap, showLabels })
  }, [onViewChange, locationId, fieldKey, status, filters, basemap, showLabels])

  const allFields = useMemo(() => [...fields, ...unmapped], [fields, unmapped])
  const selectedField = allFields.find((f) => f.field_key === fieldKey) || null
  const selectedLocation = locations.find((l) => l.id === locationId) || null

  // A different field starts from the default view of its wells.
  const chooseField = (key) => {
    if (key !== fieldKey) {
      setStatus(DEFAULT_STATUS)
      setFilters(DEFAULT_FILTERS)
    }
    setFieldKey(key)
  }

  const selectField = (key) => {
    chooseField(key)
    setLocationId(locationOf.get(key) ?? null)
    setQuery('')
  }

  const selectLocation = (id) => {
    const location = locations.find((l) => l.id === id)
    setLocationId(id)
    chooseField(location && location.fields.length === 1 ? location.fields[0].field_key : null)
  }

  const overview = () => {
    setLocationId(null)
    chooseField(null)
  }

  const onSatelliteFailure = useCallback(() => {
    setSatelliteFailed(true)
    setBasemap('standard')
  }, [])

  const pickBasemap = (id) => {
    if (id === 'satellite') setSatelliteFailed(false)
    setBasemap(id)
  }

  const matches = useMemo(() => {
    const needle = query.trim().toUpperCase()
    if (!needle) return []
    return allFields
      .filter((f) => f.field_key.includes(needle) || (f.label || '').toUpperCase().includes(needle))
      .slice(0, SEARCH_RESULTS)
  }, [allFields, query])

  const notice = satelliteFailed
    ? 'Satellite imagery could not be loaded (the imagery service may be blocked, unconfigured or over its quota). Showing the standard map.'
    : !satelliteAvailable
      ? basemaps?.notice || 'Satellite imagery is not configured. Showing the standard map.'
      : null

  return (
    <>
      <div className="field-map__toolbar">
        <div className="field-map__search">
          <input
            type="search"
            className="field-map__search-input"
            placeholder="Search field…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            aria-label="Search field"
          />
          {matches.length ? (
            <ul className="field-map__search-results" role="listbox" aria-label="Matching fields">
              {matches.map((f) => (
                <li key={f.field_key}>
                  <button type="button" className="field-map__search-option" onClick={() => selectField(f.field_key)}>
                    {f.field}
                    {locationOf.has(f.field_key) ? null : <span className="field-map__muted"> · no location</span>}
                  </button>
                </li>
              ))}
            </ul>
          ) : null}
        </div>
        {locationId || fieldKey ? (
          <button type="button" className="btn btn--ghost" onClick={overview}>
            ← Oman overview
          </button>
        ) : null}
        <div className="field-map__spacer" />
        <BasemapSwitch
          basemap={basemap}
          satelliteAvailable={satelliteAvailable}
          labelsAvailable={Boolean(basemaps?.labels?.url)}
          showLabels={showLabels}
          onPick={pickBasemap}
          onToggleLabels={() => setShowLabels((value) => !value)}
        />
      </div>

      {notice ? (
        <div className="field-map__basemap-notice" role="status">
          {notice}
          {satelliteFailed ? (
            <>
              {' '}
              <button type="button" className="linklike" onClick={() => pickBasemap('satellite')}>
                Try satellite again
              </button>
            </>
          ) : null}
        </div>
      ) : null}

      <div className="field-map__map-wrap">
        {locations.length ? (
          <Suspense fallback={<div className="field-map__note field-map__map-placeholder"><Spinner /> Loading map…</div>}>
            <FieldMapCanvas
              locations={locations}
              bounds={data.bounds}
              selectedLocationId={locationId}
              onSelectLocation={selectLocation}
              basemaps={basemaps}
              basemap={basemap}
              showLabels={showLabels}
              onSatelliteFailure={onSatelliteFailure}
            />
          </Suspense>
        ) : (
          <div className="field-map__note">No field has a mapped location yet.</div>
        )}
        <Legend satellite={basemap === 'satellite'} />
        <p className="field-map__disclaimer">
          {data.location_note}
          {basemap === 'satellite'
            ? ' Satellite imagery is a geographic reference only; it does not show whether a well exists, is live or is assigned to a rig.'
            : ''}
        </p>
      </div>

      <div className="field-map__selection" aria-label="Selected field">
        {selectedField ? (
          <FieldSummary field={selectedField} onClear={overview} />
        ) : selectedLocation ? (
          <LocationChooser location={selectedLocation} onSelectField={chooseField} />
        ) : (
          <Coverage coverage={data.coverage} />
        )}
      </div>

      {selectedField ? (
        <FieldWells
          field={selectedField}
          status={status}
          filters={filters}
          onStatusChange={setStatus}
          onFiltersChange={setFilters}
          cache={wellsCache}
          onSelectWell={onSelectWell}
        />
      ) : null}

      {unmapped.length ? (
        <div className="field-map__unmapped">
          <button
            type="button"
            className="btn btn--ghost"
            aria-expanded={showUnmapped}
            onClick={() => setShowUnmapped((value) => !value)}
          >
            {unmapped.length} field{unmapped.length === 1 ? ' has' : 's have'} no mapped coordinates
          </button>
          {showUnmapped ? (
            <>
              <p className="field-map__muted">
                No verified location is configured for these fields, so they are never placed on the map.
                Their wells are still listed when a field is chosen.
              </p>
              <ul className="field-map__unmapped-list">
                {unmapped.map((f) => (
                  <li key={f.field_key}>
                    <button type="button" className="field-map__search-option" onClick={() => selectField(f.field_key)}>
                      {f.field} <span className="field-map__muted">· {f.total_well_count} well{f.total_well_count === 1 ? '' : 's'}</span>
                    </button>
                  </li>
                ))}
              </ul>
            </>
          ) : null}
        </div>
      ) : null}
    </>
  )
}

function BasemapSwitch({ basemap, satelliteAvailable, labelsAvailable, showLabels, onPick, onToggleLabels }) {
  return (
    <div className="field-map__basemaps">
      <div className="segmented" role="group" aria-label="Basemap">
        <button
          type="button"
          className={`segmented__option${basemap === 'satellite' ? ' segmented__option--active' : ''}`}
          aria-pressed={basemap === 'satellite'}
          disabled={!satelliteAvailable}
          title={satelliteAvailable ? 'Satellite imagery' : 'Satellite imagery is not configured'}
          onClick={() => onPick('satellite')}
        >
          Satellite
        </button>
        <button
          type="button"
          className={`segmented__option${basemap === 'standard' ? ' segmented__option--active' : ''}`}
          aria-pressed={basemap === 'standard'}
          title="Standard map with English labels"
          onClick={() => onPick('standard')}
        >
          Standard
        </button>
      </div>
      {basemap === 'satellite' && labelsAvailable ? (
        <label className="field-map__labels-toggle" title="Place names over the imagery, in English">
          <input type="checkbox" checked={showLabels} onChange={onToggleLabels} /> Place labels
        </label>
      ) : null}
    </div>
  )
}

function Legend({ satellite }) {
  return (
    <div className="field-map__legend" aria-label="Legend">
      <span><span className="field-map__legend-dot field-map__legend-dot--field" aria-hidden="true" /> Petroleum field</span>
      <span><span className="field-map__legend-dot field-map__legend-dot--selected" aria-hidden="true" /> Selected field</span>
      <span className="field-map__muted">
        Approximate field locations{satellite ? ' · zoom in to see field roads and pads where the imagery resolves them' : ''}
      </span>
    </div>
  )
}

function Coverage({ coverage }) {
  if (!coverage) return null
  return (
    <div className="field-map__coverage">
      <div className="field-map__panel-title">Select a field</div>
      <p className="field-map__muted">
        Click a marker, or search above, to see that field&apos;s wells.
      </p>
      <dl className="field-map__stats">
        <div><dt>Fields on the map</dt><dd><Num>{coverage.mapped_field_count}</Num></dd></div>
        <div><dt>Live wells on the map</dt><dd><Num>{coverage.live_wells_on_map}</Num> of <Num>{coverage.live_wells}</Num></dd></div>
        <div><dt>Wells on the map</dt><dd><Num>{coverage.wells_on_map}</Num> of <Num>{coverage.total_wells}</Num></dd></div>
        <div><dt>Wells with no field recorded</dt><dd><Num>{coverage.wells_without_field}</Num></dd></div>
      </dl>
    </div>
  )
}

function LocationChooser({ location, onSelectField }) {
  return (
    <div>
      <div className="field-map__panel-title">{location.fields.length} fields at this location</div>
      <p className="field-map__muted">Approximate field location. Choose a field:</p>
      <ul className="field-map__chooser">
        {location.fields.map((f) => (
          <li key={f.field_key}>
            <button type="button" className="field-map__search-option" onClick={() => onSelectField(f.field_key)}>
              {f.field} <span className="field-map__muted">· {f.live_well_count} live · {f.total_well_count} well{f.total_well_count === 1 ? '' : 's'}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function FieldSummary({ field, onClear }) {
  const located = field.latitude !== undefined && field.latitude !== null
  return (
    <div className="field-map__summary">
      <div>
        <div className="field-map__summary-label">Selected field</div>
        <div className="field-map__panel-title">{field.field}</div>
        <p className="field-map__muted">
          {located
            ? field.location_basis === 'parent_field'
              ? `Approximate field location (${field.parent_field} field area)`
              : 'Approximate field location'
            : 'Field location unavailable.'}
        </p>
      </div>
      <dl className="field-map__stats">
        <div><dt>Live Wells</dt><dd><Num>{field.live_well_count}</Num></dd></div>
        <div><dt>Completed Wells</dt><dd><Num>{field.completed_well_count}</Num></dd></div>
        <div><dt>Total Wells</dt><dd><Num>{field.total_well_count}</Num></dd></div>
      </dl>
      <button type="button" className="btn btn--ghost" onClick={onClear}>
        Clear selection
      </button>
    </div>
  )
}

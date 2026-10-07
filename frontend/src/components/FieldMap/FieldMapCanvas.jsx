import { useEffect, useMemo, useRef, useState } from 'react'
import L from 'leaflet'
import { MapContainer, Marker, Polygon, ScaleControl, TileLayer, Tooltip, useMap, useMapEvents } from 'react-leaflet'
import 'leaflet/dist/leaflet.css'
import omanBoundary from './oman_boundary.json'

/**
 * The standard map when the backend sends no basemap configuration: Esri
 * World Street Map, chosen because every place carries an English name
 * (Muscat, Sohar, Ibri, Salalah; at closer zooms the Arabic name is printed
 * beside it). The standard OpenStreetMap tiles label each place in its local
 * language only, which in Oman is Arabic. Needs no API key.
 * VITE_MAP_TILE_URL still replaces it (e.g. with an internal tile server);
 * note Esri's URL order is {z}/{y}/{x}.
 */
const FALLBACK_STANDARD = {
  label: 'Standard',
  url:
    import.meta.env.VITE_MAP_TILE_URL ||
    'https://server.arcgisonline.com/ArcGIS/rest/services/World_Street_Map/MapServer/tile/{z}/{y}/{x}',
  attribution:
    import.meta.env.VITE_MAP_TILE_ATTRIBUTION ||
    'Tiles &copy; Esri &mdash; Source: Esri, HERE, Garmin, USGS, OpenStreetMap contributors and the GIS User Community',
  max_native_zoom: 19,
  max_zoom: 19,
  tile_size: 256,
  zoom_offset: 0,
}

/**
 * The layer a basemap id draws. The standard map is always available: it is
 * what the page falls back to whenever imagery is off, unconfigured or failing.
 */
export function resolveLayer(basemaps, id) {
  const layers = basemaps?.layers || {}
  if (id === 'satellite' && layers.satellite?.url) return layers.satellite
  const standard = layers.standard?.url ? layers.standard : FALLBACK_STANDARD
  return import.meta.env.VITE_MAP_TILE_URL ? { ...standard, ...FALLBACK_STANDARD } : standard
}

/**
 * Oman in full, everything else dimmed: one polygon covering the world with
 * Oman's outline cut out of it (mainland, Masirah, Musandam and Madha each a
 * hole), drawn over the tiles. Natural Earth boundary, see oman_boundary.json.
 * Over imagery the dimming is dark; over the pale street map, light.
 */
const WORLD_RING = [
  [-89.9, -179.9],
  [-89.9, 179.9],
  [89.9, 179.9],
  [89.9, -179.9],
]
export const OMAN_RINGS = omanBoundary.rings
const MASK_POSITIONS = [WORLD_RING, ...OMAN_RINGS]
const MASK_STYLE = {
  standard: { stroke: false, fillColor: '#eef1f4', fillOpacity: 0.86, interactive: false },
  satellite: { stroke: false, fillColor: '#05080c', fillOpacity: 0.55, interactive: false },
}
const OUTLINE_STYLE = {
  standard: { color: '#1f6fd1', weight: 2, opacity: 0.9, fill: false, interactive: false },
  satellite: { color: '#ffd166', weight: 1.5, opacity: 0.85, fill: false, interactive: false },
}

/** How far outside Oman the map may be panned, in degrees, and how far out it may zoom. */
const PAN_MARGIN = 2.5
const MIN_ZOOM = 5

/**
 * Zoom used when a field is selected: the field area and its access roads
 * and pads (where the imagery resolves them), not a wellhead.
 */
export const FIELD_ZOOM = 11

/** From this zoom each field's name is written beside its marker. */
export const LABEL_ZOOM = 8

/**
 * Imagery counts as failing when this many tiles have errored and none has
 * loaded, or when failures outnumber loads past a larger sample -- a blocked
 * host, a bad key or an exhausted quota, not one missing tile.
 */
export const TILE_FAILURE_THRESHOLD = 4

/**
 * Moves the map to what is selected: the chosen location, or the whole of
 * Oman when nothing is. A separate function so the behaviour can be tested
 * without a browser's layout engine.
 */
export function focusMap(map, location, bounds, { animate = true } = {}) {
  if (!map) return
  if (location && !animate) {
    map.setView([location.latitude, location.longitude], FIELD_ZOOM, { animate: false })
  } else if (location) {
    map.flyTo([location.latitude, location.longitude], FIELD_ZOOM, { duration: 0.8 })
  } else if (bounds) {
    map.fitBounds(
      [
        [bounds.lat_min, bounds.lon_min],
        [bounds.lat_max, bounds.lon_max],
      ],
      { padding: [12, 12] },
    )
  }
}

function FocusController({ location, bounds }) {
  const map = useMap()
  // The first placement happens as the map is created -- e.g. returning from
  // a well to a field already selected -- where an animated flight does not
  // take effect, so it jumps; every later change of selection flies.
  const placed = useRef(false)
  useEffect(() => {
    focusMap(map, location, bounds, { animate: placed.current })
    placed.current = true
  }, [map, location, bounds])
  return null
}

/**
 * Marks the container with the basemap being drawn. react-leaflet applies
 * MapContainer's className only when the map is created, so a switch of
 * basemap has to be written onto the live container.
 */
function LookClass({ look }) {
  const map = useMap()
  useEffect(() => {
    const container = map.getContainer()
    container.classList.toggle('field-map__map--satellite', look === 'satellite')
    container.classList.toggle('field-map__map--standard', look === 'standard')
  }, [map, look])
  return null
}

/**
 * Keeps Leaflet's idea of the map's size true. Leaflet only re-measures on a
 * window resize; when the container alone changes size (layout, a panel, a
 * viewport switch) tiles, outline and markers would drift apart otherwise.
 */
function SizeWatcher() {
  const map = useMap()
  useEffect(() => {
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(() => map.invalidateSize({ pan: false }))
    observer.observe(map.getContainer())
    return () => observer.disconnect()
  }, [map])
  return null
}

/** Writes each field's name beside its marker once the map is zoomed in far enough. */
function ZoomLabels() {
  const map = useMap()
  const [zoom, setZoom] = useState(() => map.getZoom())
  useMapEvents({ zoomend: () => setZoom(map.getZoom()) })
  useEffect(() => {
    const container = map.getContainer()
    container.classList.toggle('field-map__map--labelled', zoom >= LABEL_ZOOM)
  }, [map, zoom])
  return null
}

/**
 * The basemap tiles, reporting when they cannot be loaded. Keyed by URL by
 * the caller, so switching basemap starts a fresh count.
 */
function BaseTiles({ layer, onFailure }) {
  const counts = useRef({ loaded: 0, failed: 0, reported: false })
  const handlers = useMemo(
    () => ({
      tileload: () => {
        counts.current.loaded += 1
      },
      tileerror: () => {
        const c = counts.current
        c.failed += 1
        const failing =
          (c.loaded === 0 && c.failed >= TILE_FAILURE_THRESHOLD) ||
          (c.failed >= TILE_FAILURE_THRESHOLD * 3 && c.failed > c.loaded)
        if (failing && !c.reported && onFailure) {
          c.reported = true
          onFailure()
        }
      },
    }),
    [onFailure],
  )
  return (
    <TileLayer
      url={layer.url}
      attribution={layer.attribution}
      maxNativeZoom={layer.max_native_zoom}
      maxZoom={layer.max_zoom}
      tileSize={layer.tile_size || 256}
      zoomOffset={layer.zoom_offset || 0}
      eventHandlers={handlers}
    />
  )
}

/**
 * A DOM marker rather than a vector circle: it carries the field's name as
 * text (visible when zoomed in, and always to screen readers), and its
 * "selected" state is a plain class. The name is the database's own English
 * field name, exactly as the panel and well list show it.
 */
function markerIcon(location, selected) {
  const size = location.fields.length > 1 ? 16 : 13
  const names = location.fields.map((f) => f.field).join(', ')
  return L.divIcon({
    className: `field-marker${selected ? ' field-marker--selected' : ''}`,
    html:
      `<span class="field-marker__dot" style="width:${size}px;height:${size}px" aria-hidden="true"></span>` +
      `<span class="field-marker__name">${escapeHtml(names)}</span>`,
    iconSize: [size, size],
    iconAnchor: [size / 2, size / 2],
  })
}

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c])
}

/**
 * The interactive map: one marker per approximate field position. Several
 * named sub-areas can share their parent field's position; they share one
 * marker, and its tooltip lists each field with its own counts.
 *
 * `basemap` is 'satellite' or 'standard'; `basemaps` is the backend's layer
 * configuration. `onSatelliteFailure` is called once if imagery tiles cannot
 * be loaded, so the page can switch to the standard map and say why.
 */
export default function FieldMapCanvas({
  locations,
  bounds,
  selectedLocationId,
  onSelectLocation,
  basemaps = null,
  basemap = 'standard',
  showLabels = true,
  onSatelliteFailure,
}) {
  const selected = useMemo(
    () => locations.find((location) => location.id === selectedLocationId) || null,
    [locations, selectedLocationId],
  )
  const initialBounds = useMemo(
    () => [
      [bounds.lat_min, bounds.lon_min],
      [bounds.lat_max, bounds.lon_max],
    ],
    [bounds],
  )
  // Oman is the whole subject: the map cannot be dragged or zoomed far away from it.
  const panLimit = useMemo(
    () => [
      [bounds.lat_min - PAN_MARGIN, bounds.lon_min - PAN_MARGIN],
      [bounds.lat_max + PAN_MARGIN, bounds.lon_max + PAN_MARGIN],
    ],
    [bounds],
  )

  const layer = resolveLayer(basemaps, basemap)
  const onSatellite = basemap === 'satellite' && layer === basemaps?.layers?.satellite
  const labels = onSatellite && showLabels ? basemaps?.labels : null
  const look = onSatellite ? 'satellite' : 'standard'

  return (
    <MapContainer
      className="field-map__map"
      bounds={initialBounds}
      maxBounds={panLimit}
      maxBoundsViscosity={1}
      minZoom={MIN_ZOOM}
      maxZoom={layer.max_zoom || 19}
      scrollWheelZoom
      aria-label="Map of Oman petroleum fields"
    >
      <BaseTiles key={layer.url} layer={layer} onFailure={onSatellite ? onSatelliteFailure : undefined} />
      {labels?.url ? (
        <TileLayer
          key={labels.url}
          url={labels.url}
          attribution={labels.attribution}
          maxNativeZoom={labels.max_native_zoom}
          maxZoom={labels.max_zoom}
          tileSize={labels.tile_size || 256}
          zoomOffset={labels.zoom_offset || 0}
          className="field-map__labels"
          zIndex={2}
        />
      ) : null}
      <Polygon key={`mask-${look}`} positions={MASK_POSITIONS} pathOptions={MASK_STYLE[look]} className="field-map__mask" />
      <Polygon
        key={`outline-${look}`}
        positions={OMAN_RINGS}
        pathOptions={OUTLINE_STYLE[look]}
        className="field-map__oman-outline"
      />
      <ScaleControl position="bottomleft" imperial={false} />
      <LookClass look={look} />
      <SizeWatcher />
      <ZoomLabels />
      <FocusController location={selected} bounds={bounds} />
      {locations.map((location) => (
        <Marker
          key={location.id}
          position={[location.latitude, location.longitude]}
          icon={markerIcon(location, location.id === selectedLocationId)}
          keyboard
          title={location.fields.map((f) => f.field).join(', ')}
          eventHandlers={{ click: () => onSelectLocation(location.id) }}
          zIndexOffset={location.id === selectedLocationId ? 1000 : 0}
        >
          <Tooltip direction="top" offset={[0, -6]}>
            {location.fields.map((field) => (
              <div key={field.field_key} className="field-marker__tip">
                <strong>Field: {field.field}</strong>
                <br />
                Live Wells: {field.live_well_count}
                <br />
                Completed Wells: {field.completed_well_count}
                <br />
                Total Wells: {field.total_well_count}
              </div>
            ))}
            <div className="field-marker__tip-note">Approximate field location</div>
          </Tooltip>
        </Marker>
      ))}
    </MapContainer>
  )
}

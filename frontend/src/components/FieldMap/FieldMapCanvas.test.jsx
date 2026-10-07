/**
 * The real Leaflet map, in jsdom: it renders, draws a marker per location,
 * reports a click, and moves to the selected field (or back to Oman).
 */
import { fireEvent, render } from '@testing-library/react'
import L from 'leaflet'
import { afterEach, describe, expect, it, vi } from 'vitest'
import FieldMapCanvas, { FIELD_ZOOM, LABEL_ZOOM, OMAN_RINGS, TILE_FAILURE_THRESHOLD, focusMap, resolveLayer } from './FieldMapCanvas'

const BOUNDS = { lat_min: 16.4, lat_max: 26.6, lon_min: 51.8, lon_max: 60 }
const LOCATIONS = [
  { id: '18.55,55.65', latitude: 18.55, longitude: 55.65, fields: [
    { field: 'NIMR', field_key: 'NIMR', live_well_count: 2, completed_well_count: 1, total_well_count: 3 },
  ] },
  { id: '22.35,56.48', latitude: 22.35, longitude: 56.48, fields: [
    { field: 'FAHUD', field_key: 'FAHUD', live_well_count: 1, completed_well_count: 0, total_well_count: 1 },
  ] },
]

afterEach(() => vi.restoreAllMocks())

describe('FieldMapCanvas', () => {
  it('renders an Oman map with one marker per location', () => {
    const { container } = render(
      <FieldMapCanvas locations={LOCATIONS} bounds={BOUNDS} selectedLocationId={null} onSelectLocation={vi.fn()} />,
    )
    expect(container.querySelector('.leaflet-container')).toBeInTheDocument()
    expect(container.querySelectorAll('.field-marker')).toHaveLength(2)
    expect(container.querySelector('.field-marker').textContent).toBe('NIMR')
  })

  it('shows Oman in colour and fades the rest of the world', () => {
    const { container } = render(
      <FieldMapCanvas locations={LOCATIONS} bounds={BOUNDS} selectedLocationId={null} onSelectLocation={vi.fn()} />,
    )
    expect(container.querySelector('path.field-map__mask')).toBeInTheDocument()
    expect(container.querySelector('path.field-map__oman-outline')).toBeInTheDocument()
    // Mainland, Masirah, Musandam and Madha.
    expect(OMAN_RINGS).toHaveLength(4)
    for (const ring of OMAN_RINGS) {
      for (const [lat, lng] of ring) {
        expect(lat).toBeGreaterThan(16)
        expect(lat).toBeLessThan(27)
        expect(lng).toBeGreaterThan(51)
        expect(lng).toBeLessThan(60)
      }
    }
  })

  it('reports a marker click and highlights the selected marker', () => {
    const onSelect = vi.fn()
    const { container, rerender } = render(
      <FieldMapCanvas locations={LOCATIONS} bounds={BOUNDS} selectedLocationId={null} onSelectLocation={onSelect} />,
    )
    fireEvent.click(container.querySelectorAll('.field-marker')[0])
    expect(onSelect).toHaveBeenCalledWith('18.55,55.65')

    rerender(
      <FieldMapCanvas locations={LOCATIONS} bounds={BOUNDS} selectedLocationId="18.55,55.65" onSelectLocation={onSelect} />,
    )
    expect(container.querySelectorAll('.field-marker--selected')).toHaveLength(1)
  })

  it('flies to a newly selected field', () => {
    const flyTo = vi.spyOn(L.Map.prototype, 'flyTo').mockImplementation(function () { return this })
    const { rerender } = render(
      <FieldMapCanvas locations={LOCATIONS} bounds={BOUNDS} selectedLocationId={null} onSelectLocation={vi.fn()} />,
    )
    rerender(
      <FieldMapCanvas locations={LOCATIONS} bounds={BOUNDS} selectedLocationId="22.35,56.48" onSelectLocation={vi.fn()} />,
    )
    expect(flyTo).toHaveBeenCalledWith([22.35, 56.48], FIELD_ZOOM, expect.anything())
  })

  it('opens directly on a field already selected (e.g. back from a well)', () => {
    const setView = vi.spyOn(L.Map.prototype, 'setView')
    const flyTo = vi.spyOn(L.Map.prototype, 'flyTo')
    render(
      <FieldMapCanvas locations={LOCATIONS} bounds={BOUNDS} selectedLocationId="22.35,56.48" onSelectLocation={vi.fn()} />,
    )
    expect(setView).toHaveBeenCalledWith([22.35, 56.48], FIELD_ZOOM, expect.objectContaining({ animate: false }))
    expect(flyTo).not.toHaveBeenCalled()
  })
})

describe('focusMap', () => {
  it('zooms to a location, and back to Oman when there is none', () => {
    const map = { flyTo: vi.fn(), fitBounds: vi.fn(), setView: vi.fn() }
    focusMap(map, LOCATIONS[1], BOUNDS, { animate: false })
    expect(map.setView).toHaveBeenCalledWith([22.35, 56.48], FIELD_ZOOM, { animate: false })
    focusMap(map, LOCATIONS[0], BOUNDS)
    expect(map.flyTo).toHaveBeenCalledWith([18.55, 55.65], FIELD_ZOOM, expect.anything())
    focusMap(map, null, BOUNDS)
    expect(map.fitBounds).toHaveBeenCalledWith([[16.4, 51.8], [26.6, 60]], expect.anything())
  })
})

const BASEMAPS = {
  default: 'satellite',
  layers: {
    satellite: { label: 'Satellite', url: 'https://imagery.test/{z}/{y}/{x}', attribution: 'Imagery &copy; Esri', max_native_zoom: 18, max_zoom: 19, tile_size: 256, zoom_offset: 0 },
    standard: { label: 'Standard', url: 'https://streets.test/{z}/{y}/{x}', attribution: 'Streets &copy; Esri', max_native_zoom: 19, max_zoom: 19, tile_size: 256, zoom_offset: 0 },
  },
  labels: { label: 'Labels', url: 'https://labels.test/{z}/{y}/{x}', attribution: 'Labels &copy; Esri', max_native_zoom: 19, max_zoom: 19, tile_size: 256, zoom_offset: 0 },
}

/** jsdom lays nothing out; give the map a size so Leaflet requests tiles. */
function withMapSize() {
  vi.spyOn(HTMLElement.prototype, 'clientWidth', 'get').mockReturnValue(800)
  vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockReturnValue(600)
}

const tiles = (container, host) => [...container.querySelectorAll('img.leaflet-tile')].filter((img) => img.src.includes(host))

function renderMap(props = {}) {
  return render(
    <FieldMapCanvas locations={LOCATIONS} bounds={BOUNDS} selectedLocationId={null} onSelectLocation={vi.fn()} {...props} />,
  )
}

describe('FieldMapCanvas basemaps', () => {
  it('draws satellite imagery with the English label overlay above it, and a scale', () => {
    withMapSize()
    const { container } = renderMap({ basemaps: BASEMAPS, basemap: 'satellite' })
    expect(tiles(container, 'imagery.test').length).toBeGreaterThan(0)
    expect(tiles(container, 'labels.test').length).toBeGreaterThan(0)
    expect(tiles(container, 'streets.test')).toHaveLength(0)
    expect(container.querySelector('.field-map__labels')).toBeInTheDocument()
    expect(container.querySelector('.leaflet-control-scale')).toBeInTheDocument()
    expect(container.querySelector('.leaflet-control-attribution').innerHTML).toContain('Imagery © Esri')
    expect(container.querySelector('.field-map__map--satellite')).toBeInTheDocument()
  })

  it('can hide the labels, and the standard map carries no imagery overlay', () => {
    withMapSize()
    const { container, rerender } = renderMap({ basemaps: BASEMAPS, basemap: 'satellite', showLabels: false })
    expect(tiles(container, 'labels.test')).toHaveLength(0)
    rerender(
      <FieldMapCanvas locations={LOCATIONS} bounds={BOUNDS} selectedLocationId={null} onSelectLocation={vi.fn()}
        basemaps={BASEMAPS} basemap="standard" />,
    )
    expect(tiles(container, 'streets.test').length).toBeGreaterThan(0)
    expect(tiles(container, 'imagery.test')).toHaveLength(0)
    expect(tiles(container, 'labels.test')).toHaveLength(0)
    expect(container.querySelector('.field-map__map--standard')).toBeInTheDocument()
  })

  it('reports failing imagery once, so the page can fall back', () => {
    withMapSize()
    const onSatelliteFailure = vi.fn()
    const { container } = renderMap({ basemaps: BASEMAPS, basemap: 'satellite', onSatelliteFailure })
    const imagery = tiles(container, 'imagery.test')
    expect(imagery.length).toBeGreaterThanOrEqual(TILE_FAILURE_THRESHOLD + 1)
    imagery.slice(0, TILE_FAILURE_THRESHOLD - 1).forEach((img) => fireEvent.error(img))
    expect(onSatelliteFailure).not.toHaveBeenCalled()
    imagery.slice(TILE_FAILURE_THRESHOLD - 1).forEach((img) => fireEvent.error(img))
    expect(onSatelliteFailure).toHaveBeenCalledTimes(1)
  })

  it('a few missing tiles among loaded ones are not a failure', () => {
    withMapSize()
    const onSatelliteFailure = vi.fn()
    const { container } = renderMap({ basemaps: BASEMAPS, basemap: 'satellite', onSatelliteFailure })
    const imagery = tiles(container, 'imagery.test')
    imagery.slice(0, 3).forEach((img) => fireEvent.load(img))
    imagery.slice(3, 3 + TILE_FAILURE_THRESHOLD).forEach((img) => fireEvent.error(img))
    expect(onSatelliteFailure).not.toHaveBeenCalled()
  })

  it('never reports the standard map as failing imagery', () => {
    withMapSize()
    const onSatelliteFailure = vi.fn()
    const { container } = renderMap({ basemaps: BASEMAPS, basemap: 'standard', onSatelliteFailure })
    tiles(container, 'streets.test').forEach((img) => fireEvent.error(img))
    expect(onSatelliteFailure).not.toHaveBeenCalled()
  })

  it('writes field names beside markers once zoomed in', () => {
    withMapSize()
    const { container } = renderMap({ basemaps: BASEMAPS, basemap: 'satellite' })
    const map = container.querySelector('.leaflet-container')
    // Oman fits at a zoom well below LABEL_ZOOM, so names are not written yet.
    expect(map).not.toHaveClass('field-map__map--labelled')
    expect(LABEL_ZOOM).toBeLessThanOrEqual(FIELD_ZOOM)
    expect(container.querySelector('.field-marker__name').textContent).toBe('NIMR')
  })
})

describe('FieldMapCanvas resizing', () => {
  it('re-measures the map whenever its container changes size', () => {
    let notify
    const observe = vi.fn()
    const disconnect = vi.fn()
    vi.stubGlobal('ResizeObserver', class {
      constructor(callback) { notify = callback }
      observe(element) { observe(element) }
      disconnect() { disconnect() }
    })
    const invalidateSize = vi.spyOn(L.Map.prototype, 'invalidateSize')
    const { container, unmount } = renderMap({ basemaps: BASEMAPS, basemap: 'satellite' })
    expect(observe).toHaveBeenCalledWith(container.querySelector('.leaflet-container'))
    invalidateSize.mockClear()
    notify()
    expect(invalidateSize).toHaveBeenCalledWith({ pan: false })
    unmount()
    expect(disconnect).toHaveBeenCalled()
    vi.unstubAllGlobals()
  })
})

describe('resolveLayer', () => {
  it('draws imagery only when it is configured, and otherwise the standard map', () => {
    expect(resolveLayer(BASEMAPS, 'satellite')).toBe(BASEMAPS.layers.satellite)
    expect(resolveLayer(BASEMAPS, 'standard')).toBe(BASEMAPS.layers.standard)
    const none = { layers: { satellite: null, standard: BASEMAPS.layers.standard } }
    expect(resolveLayer(none, 'satellite')).toBe(BASEMAPS.layers.standard)
    // No configuration at all: the built-in English street map.
    expect(resolveLayer(null, 'satellite').url).toContain('World_Street_Map')
  })
})

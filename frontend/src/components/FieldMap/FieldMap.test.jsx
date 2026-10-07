/**
 * The Oman Field Map page, with the Leaflet canvas replaced by a plain
 * stand-in: these tests are about what the page shows and asks for.
 * FieldMapCanvas.test.jsx covers the real Leaflet map.
 */
import { act, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import FieldMap from './FieldMap'
import api from '../../services/api'

vi.mock('../../services/api', () => ({ default: { fieldMap: vi.fn(), fieldWells: vi.fn() } }))

// The canvas stand-in: one button per location, the selected one marked, the
// basemap it was asked to draw, and a way to make its imagery "fail".
vi.mock('./FieldMapCanvas', () => ({
  default: ({ locations, selectedLocationId, onSelectLocation, basemap, showLabels, onSatelliteFailure }) => (
    <div data-testid="map" data-basemap={basemap} data-labels={String(showLabels)}>
      {locations.map((location) => (
        <button
          key={location.id}
          type="button"
          data-selected={location.id === selectedLocationId}
          onClick={() => onSelectLocation(location.id)}
        >
          marker {location.fields.map((f) => f.field).join(' + ')}
        </button>
      ))}
      <button type="button" onClick={() => onSatelliteFailure?.()}>imagery fails</button>
    </div>
  ),
}))

const BASEMAPS = {
  default: 'satellite',
  satellite_status: 'esri_public',
  notice: null,
  layers: {
    satellite: { label: 'Satellite', url: 'https://imagery/{z}/{y}/{x}', attribution: 'Esri', max_native_zoom: 18, max_zoom: 19 },
    standard: { label: 'Standard', url: 'https://streets/{z}/{y}/{x}', attribution: 'Esri', max_native_zoom: 19, max_zoom: 19 },
  },
  labels: { label: 'Labels', url: 'https://labels/{z}/{y}/{x}', attribution: 'Esri' },
  labels_language: 'en',
}

const SUMMARY = {
  location_note:
    'Field positions are approximate representative locations and do not represent exact wellhead coordinates.',
  bounds: { lat_min: 16.4, lat_max: 26.6, lon_min: 51.8, lon_max: 60 },
  basemaps: BASEMAPS,
  fields: [
    { field: 'NIMR', field_key: 'NIMR', label: 'Nimr', latitude: 18.55, longitude: 55.65, location_type: 'approximate',
      location_basis: 'field', live_well_count: 2, completed_well_count: 1, total_well_count: 3 },
    { field: 'MARMUL AK', field_key: 'MARMUL AK', latitude: 18.15, longitude: 55.2, location_type: 'approximate',
      location_basis: 'parent_field', parent_field: 'MARMUL', live_well_count: 1, completed_well_count: 0, total_well_count: 1 },
    { field: 'MARMUL HW', field_key: 'MARMUL HW', latitude: 18.15, longitude: 55.2, location_type: 'approximate',
      location_basis: 'parent_field', parent_field: 'MARMUL', live_well_count: 0, completed_well_count: 2, total_well_count: 2 },
  ],
  unmapped_fields: [
    { field: 'EASTERN FLANK', field_key: 'EASTERN FLANK', live_well_count: 1, completed_well_count: 0, total_well_count: 1, well_count: 1 },
  ],
  coverage: { mapped_field_count: 3, wells_on_map: 6, total_wells: 10, live_wells_on_map: 3, live_wells: 5, wells_without_field: 1 },
}

const well = (id, status_code, extra = {}) => ({
  well_id: id,
  status: status_code === 'COMPLETED' ? 'Completed' : 'Incomplete',
  status_code,
  category: 'Development', category_id: 1,
  function: 'Oil Producer', function_id: 5,
  completion_type: 'PI', completion_type_id: 7,
  rig: 'RIG-A', rig_id: 10,
  well_location: null,
  latitude: null,
  longitude: null,
  ...extra,
})

const FILTER_OPTIONS = {
  category: [{ value: '1', label: 'Development', count: 2 }, { value: 'none', label: 'Not recorded', count: 0 }],
  function: [{ value: '5', label: 'Oil Producer', count: 1 }, { value: '6', label: 'Water Injector', count: 1 }],
  completion_type: [{ value: '7', label: 'PI', count: 1 }, { value: 'none', label: 'Not recorded', count: 1 }],
  rig: [{ value: '10', label: 'RIG-A', count: 1 }, { value: 'none', label: 'Unassigned', count: 1 }],
}

const wellsResponse = (field_key, wells, extra = {}) => ({
  field: field_key,
  field_key,
  live_well_count: 2,
  completed_well_count: 1,
  total_well_count: 3,
  status_filter: 'live',
  applied_filters: {},
  matching_well_count: wells.length,
  filters: FILTER_OPTIONS,
  wells,
  ...extra,
})

const NIMR_LIVE = wellsResponse('NIMR', [
  well(101, 'INCOMPLETE', { well_location: 'Pad 12' }),
  well(103, 'INCOMPLETE', { function: 'Water Injector', function_id: 6, completion_type: null, completion_type_id: null, rig: null, rig_id: null }),
])

beforeEach(() => {
  api.fieldMap.mockReset()
  api.fieldWells.mockReset()
  api.fieldMap.mockResolvedValue(SUMMARY)
  api.fieldWells.mockResolvedValue(NIMR_LIVE)
})

const lastWellsCall = () => api.fieldWells.mock.calls.at(-1)

async function openNimr() {
  render(<FieldMap onSelectWell={vi.fn()} />)
  await userEvent.click(await screen.findByRole('button', { name: 'marker NIMR' }))
  return screen.findByRole('region', { name: 'Wells in NIMR' })
}

describe('FieldMap page', () => {
  it('shows the page, the map, a marker per location, the legend and the disclaimer', async () => {
    render(<FieldMap onSelectWell={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'Oman Field Map' })).toBeInTheDocument()
    expect(await screen.findByTestId('map')).toBeInTheDocument()
    // Two fields at Marmul's position share one marker.
    expect(screen.getByRole('button', { name: 'marker NIMR' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'marker MARMUL AK + MARMUL HW' })).toBeInTheDocument()
    expect(screen.getByText(/do not represent exact wellhead coordinates/)).toBeInTheDocument()
    expect(screen.getByText(/imagery is a geographic reference only/)).toBeInTheDocument()
    expect(screen.getByLabelText('Legend')).toHaveTextContent('Approximate field locations')
    expect(screen.getByText('Select a field')).toBeInTheDocument()
  })

  it('clicking a marker selects the field, shows its counts and loads its live wells', async () => {
    const region = await openNimr()
    expect(screen.getByRole('button', { name: 'marker NIMR' })).toHaveAttribute('data-selected', 'true')
    const selection = screen.getByLabelText('Selected field')
    expect(within(selection).getByText('NIMR')).toBeInTheDocument()
    expect(within(selection).getByText('Approximate field location')).toBeInTheDocument()
    expect(within(selection).getByText('Live Wells').nextSibling).toHaveTextContent('2')
    expect(within(selection).getByText('Completed Wells').nextSibling).toHaveTextContent('1')
    // Live is the default view, asked of the backend rather than filtered here.
    expect(lastWellsCall()[0]).toBe('NIMR')
    expect(lastWellsCall()[1]).toEqual({ status: 'live' })
    expect(await within(region).findByRole('button', { name: 'Open well 101' })).toBeInTheDocument()
    expect(within(region).getAllByRole('row')).toHaveLength(3) // header + 2 wells
  })

  it('lists every column, and says so when a value is not recorded', async () => {
    const region = await openNimr()
    const table = await within(region).findByRole('table')
    const headers = within(table).getAllByRole('columnheader').map((th) => th.textContent)
    expect(headers).toEqual(['Well ID', 'Category', 'Function', 'Completion Type', 'Rig', 'Well Location', 'Status'])
    const row101 = within(table).getByRole('button', { name: 'Open well 101' }).closest('tr')
    expect(row101).toHaveTextContent('Development')
    expect(row101).toHaveTextContent('Oil Producer')
    expect(row101).toHaveTextContent('Pad 12')
    const row103 = within(table).getByRole('button', { name: 'Open well 103' }).closest('tr')
    expect(within(row103).getByText('Unassigned')).toHaveClass('missing')
    expect(within(row103).getAllByText('Not recorded')).toHaveLength(2) // completion type, location
  })

  it('shows live and completed wells distinctly, always with the words', async () => {
    api.fieldWells.mockResolvedValue(
      wellsResponse('NIMR', [well(101, 'INCOMPLETE'), well(102, 'COMPLETED')], { status_filter: 'all' }),
    )
    const region = await openNimr()
    const live = await within(region).findByText('Live', { selector: '.field-well-status' })
    const completed = within(region).getByText('Completed', { selector: '.field-well-status' })
    expect(live).toHaveClass('field-well-status--incomplete')
    expect(completed).toHaveClass('field-well-status--completed')
  })

  it('clicking a well, or its row, opens the existing well detail', async () => {
    const onSelectWell = vi.fn()
    render(<FieldMap onSelectWell={onSelectWell} />)
    await userEvent.click(await screen.findByRole('button', { name: 'marker NIMR' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Open well 101' }))
    expect(onSelectWell).toHaveBeenCalledWith(101)
    await userEvent.click(screen.getByRole('button', { name: 'Open well 103' }).closest('tr').cells[2])
    expect(onSelectWell).toHaveBeenLastCalledWith(103)
    expect(onSelectWell).toHaveBeenCalledTimes(2)
  })

  it('a shared marker lets the user choose which field', async () => {
    api.fieldWells.mockResolvedValue(wellsResponse('MARMUL HW', [well(7, 'COMPLETED')]))
    render(<FieldMap onSelectWell={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'marker MARMUL AK + MARMUL HW' }))
    expect(screen.getByText('2 fields at this location')).toBeInTheDocument()
    await userEvent.click(within(screen.getByLabelText('Selected field')).getByRole('button', { name: /MARMUL HW/ }))
    expect(screen.getByText('Approximate field location (MARMUL field area)')).toBeInTheDocument()
    expect(lastWellsCall()[0]).toBe('MARMUL HW')
  })

  it('searching selects a field exactly as its marker does', async () => {
    render(<FieldMap onSelectWell={vi.fn()} />)
    await screen.findByTestId('map')
    await userEvent.type(screen.getByLabelText('Search field'), 'nim')
    await userEvent.click(within(screen.getByRole('listbox')).getByRole('button', { name: 'NIMR' }))
    expect(screen.getByRole('button', { name: 'marker NIMR' })).toHaveAttribute('data-selected', 'true')
    expect(await screen.findByRole('region', { name: 'Wells in NIMR' })).toBeInTheDocument()
  })

  it('returns to the Oman overview, from the toolbar or by clearing the selection', async () => {
    await openNimr()
    await userEvent.click(screen.getByRole('button', { name: '← Oman overview' }))
    expect(screen.getByRole('button', { name: 'marker NIMR' })).toHaveAttribute('data-selected', 'false')
    expect(screen.getByText('Select a field')).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'marker NIMR' }))
    await userEvent.click(screen.getByRole('button', { name: 'Clear selection' }))
    expect(screen.queryByRole('region', { name: 'Wells in NIMR' })).not.toBeInTheDocument()
  })

  it('lists the fields with no coordinates, and never plots them', async () => {
    api.fieldWells.mockResolvedValue(wellsResponse('EASTERN FLANK', [well(9, 'INCOMPLETE')]))
    render(<FieldMap onSelectWell={vi.fn()} />)
    await screen.findByTestId('map')
    expect(screen.queryByRole('button', { name: /marker EASTERN FLANK/ })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '1 field has no mapped coordinates' }))
    await userEvent.click(screen.getByRole('button', { name: /EASTERN FLANK/ }))
    expect(screen.getByText('Field location unavailable.')).toBeInTheDocument()
    expect(await screen.findByRole('region', { name: 'Wells in EASTERN FLANK' })).toBeInTheDocument()
  })

  it('a page with no mapped field still works', async () => {
    api.fieldMap.mockResolvedValue({ ...SUMMARY, fields: [] })
    render(<FieldMap onSelectWell={vi.fn()} />)
    expect(await screen.findByText('No field has a mapped location yet.')).toBeInTheDocument()
    expect(screen.queryByTestId('map')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '1 field has no mapped coordinates' })).toBeInTheDocument()
  })

  it('a failure to load says so, inside the section only', async () => {
    api.fieldMap.mockRejectedValue(new Error('HTTP 503'))
    render(<FieldMap onSelectWell={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load Oman field map.')
  })

  it('even a synchronous failure stays inside the section', async () => {
    api.fieldMap.mockImplementation(() => {
      throw new TypeError('not a function')
    })
    render(<FieldMap onSelectWell={vi.fn()} />)
    expect(await screen.findByRole('alert')).toHaveTextContent('Unable to load Oman field map.')
  })

  it('shows loading states for the map and for a field’s wells', async () => {
    let finish
    api.fieldWells.mockReturnValue(new Promise((resolve) => { finish = resolve }))
    render(<FieldMap onSelectWell={vi.fn()} />)
    expect(screen.getByText(/Loading the Oman field map/)).toBeInTheDocument()
    await userEvent.click(await screen.findByRole('button', { name: 'marker NIMR' }))
    expect(screen.getByText(/Loading wells/)).toBeInTheDocument()
    await act(async () => finish(NIMR_LIVE))
    expect(await screen.findByRole('button', { name: 'Open well 101' })).toBeInTheDocument()
  })

  it('a wells failure is reported beside the map, which keeps working', async () => {
    api.fieldWells.mockRejectedValue(new Error('HTTP 503'))
    await openNimr()
    expect(await screen.findByRole('alert')).toHaveTextContent('The wells for this field could not be loaded.')
    expect(screen.getByTestId('map')).toBeInTheDocument()
  })
})

describe('FieldMap filters', () => {
  it('offers the backend’s options, with counts and an All choice', async () => {
    const region = await openNimr()
    await within(region).findByRole('button', { name: 'Open well 101' })
    const rig = within(region).getByLabelText('Rig')
    expect([...rig.options].map((o) => o.textContent)).toEqual(['All', 'RIG-A (1)', 'Unassigned (1)'])
    expect(within(region).getByLabelText('Well Category')).toHaveValue('all')
    expect(within(region).getByLabelText('Well Function')).toBeInTheDocument()
    expect(within(region).getByLabelText('Completion Type')).toBeInTheDocument()
  })

  it('combines filters and asks the backend for each combination', async () => {
    const region = await openNimr()
    await within(region).findByRole('button', { name: 'Open well 101' })
    api.fieldWells.mockResolvedValue(wellsResponse('NIMR', [well(101, 'INCOMPLETE')], { matching_well_count: 1 }))

    await userEvent.selectOptions(within(region).getByLabelText('Well Function'), '5')
    expect(lastWellsCall()[1]).toEqual({ status: 'live', function: '5' })
    await userEvent.selectOptions(within(region).getByLabelText('Rig'), 'none')
    expect(lastWellsCall()[1]).toEqual({ status: 'live', function: '5', rig: 'none' })
    await userEvent.selectOptions(within(region).getByLabelText('Completion Type'), '7')
    await userEvent.selectOptions(within(region).getByLabelText('Well Category'), '1')
    expect(lastWellsCall()[1]).toEqual({ status: 'live', category: '1', function: '5', completionType: '7', rig: 'none' })
    expect(await within(region).findByText(/match the filters/)).toBeInTheDocument()

    await userEvent.click(within(region).getByRole('button', { name: 'Reset filters' }))
    expect(within(region).getByLabelText('Rig')).toHaveValue('all')
  })

  it('switches between live, completed and all wells', async () => {
    const region = await openNimr()
    await within(region).findByRole('button', { name: 'Open well 101' })
    expect(within(region).getByRole('button', { name: 'Live' })).toHaveAttribute('aria-pressed', 'true')
    api.fieldWells.mockResolvedValue(wellsResponse('NIMR', [well(102, 'COMPLETED')], { status_filter: 'completed' }))
    await userEvent.click(within(region).getByRole('button', { name: 'Completed' }))
    expect(lastWellsCall()[1]).toEqual({ status: 'completed' })
    expect(await within(region).findByRole('button', { name: 'Open well 102' })).toBeInTheDocument()
    await userEvent.click(within(region).getByRole('button', { name: 'All' }))
    expect(lastWellsCall()[1]).toEqual({ status: 'all' })
  })

  it('an empty result says so', async () => {
    api.fieldWells.mockResolvedValue(wellsResponse('NIMR', [], { matching_well_count: 0 }))
    const region = await openNimr()
    expect(await within(region).findByText('No live wells in NIMR.')).toBeInTheDocument()
  })

  it('resets the filters when a different field is chosen', async () => {
    const region = await openNimr()
    await within(region).findByRole('button', { name: 'Open well 101' })
    await userEvent.selectOptions(within(region).getByLabelText('Rig'), '10')
    api.fieldWells.mockResolvedValue(wellsResponse('MARMUL HW', [well(7, 'INCOMPLETE')]))
    await userEvent.click(screen.getByRole('button', { name: 'marker MARMUL AK + MARMUL HW' }))
    await userEvent.click(within(screen.getByLabelText('Selected field')).getByRole('button', { name: /MARMUL HW/ }))
    expect(lastWellsCall()).toEqual(['MARMUL HW', { status: 'live' }, expect.anything()])
  })

  it('an older answer arriving late never replaces a newer one', async () => {
    const pending = []
    api.fieldWells.mockImplementation((field, params, signal) => new Promise((resolve, reject) => {
      pending.push({ params, resolve })
      signal?.addEventListener('abort', () => reject(Object.assign(new Error('aborted'), { name: 'AbortError' })))
    }))
    render(<FieldMap onSelectWell={vi.fn()} />)
    await userEvent.click(await screen.findByRole('button', { name: 'marker NIMR' }))
    await act(async () => pending[0].resolve(NIMR_LIVE))
    const region = screen.getByRole('region', { name: 'Wells in NIMR' })
    await within(region).findByRole('button', { name: 'Open well 101' })

    await userEvent.selectOptions(within(region).getByLabelText('Rig'), '10')      // request A
    await userEvent.selectOptions(within(region).getByLabelText('Rig'), 'none')    // request B
    const [a, b] = pending.slice(-2)
    await act(async () => b.resolve(wellsResponse('NIMR', [well(555, 'INCOMPLETE')])))
    await act(async () => a.resolve(wellsResponse('NIMR', [well(444, 'INCOMPLETE')])))
    expect(await within(region).findByRole('button', { name: 'Open well 555' })).toBeInTheDocument()
    expect(within(region).queryByRole('button', { name: 'Open well 444' })).not.toBeInTheDocument()
  })
})

describe('FieldMap basemaps', () => {
  it('opens on satellite imagery with the place-label overlay, and switches to the standard map', async () => {
    render(<FieldMap onSelectWell={vi.fn()} />)
    const map = await screen.findByTestId('map')
    expect(map).toHaveAttribute('data-basemap', 'satellite')
    expect(screen.getByRole('checkbox', { name: 'Place labels' })).toBeChecked()

    await userEvent.click(screen.getByRole('checkbox', { name: 'Place labels' }))
    expect(map).toHaveAttribute('data-labels', 'false')

    await userEvent.click(screen.getByRole('button', { name: 'Standard' }))
    expect(map).toHaveAttribute('data-basemap', 'standard')
    expect(screen.getByRole('button', { name: 'Standard' })).toHaveAttribute('aria-pressed', 'true')
    expect(screen.queryByRole('checkbox', { name: 'Place labels' })).not.toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: 'Satellite' }))
    expect(map).toHaveAttribute('data-basemap', 'satellite')
  })

  it('falls back to the standard map, and says why, when imagery fails to load', async () => {
    render(<FieldMap onSelectWell={vi.fn()} />)
    const map = await screen.findByTestId('map')
    await userEvent.click(screen.getByRole('button', { name: 'imagery fails' }))
    expect(map).toHaveAttribute('data-basemap', 'standard')
    expect(screen.getByRole('status')).toHaveTextContent('Satellite imagery could not be loaded')
    await userEvent.click(screen.getByRole('button', { name: 'Try satellite again' }))
    expect(map).toHaveAttribute('data-basemap', 'satellite')
    expect(screen.queryByRole('status')).not.toBeInTheDocument()
  })

  it('works without any satellite configuration: standard map, explained, satellite disabled', async () => {
    api.fieldMap.mockResolvedValue({
      ...SUMMARY,
      basemaps: {
        ...BASEMAPS,
        default: 'standard',
        satellite_status: 'not_configured',
        notice: 'Satellite imagery is not configured. Showing the standard map.',
        layers: { satellite: null, standard: BASEMAPS.layers.standard },
        labels: null,
      },
    })
    render(<FieldMap onSelectWell={vi.fn()} />)
    expect(await screen.findByTestId('map')).toHaveAttribute('data-basemap', 'standard')
    expect(screen.getByRole('status')).toHaveTextContent('not configured')
    expect(screen.getByRole('button', { name: 'Satellite' })).toBeDisabled()
  })

  it('an older backend with no basemap information still shows a map', async () => {
    const { basemaps, ...withoutBasemaps } = SUMMARY
    api.fieldMap.mockResolvedValue(withoutBasemaps)
    render(<FieldMap onSelectWell={vi.fn()} />)
    expect(await screen.findByTestId('map')).toHaveAttribute('data-basemap', 'standard')
  })
})

describe('FieldMap view restore', () => {
  it('reports its view, and reopens exactly as it was left', async () => {
    const onViewChange = vi.fn()
    const { unmount } = render(<FieldMap onSelectWell={vi.fn()} onViewChange={onViewChange} />)
    await userEvent.click(await screen.findByRole('button', { name: 'marker NIMR' }))
    const region = await screen.findByRole('region', { name: 'Wells in NIMR' })
    await within(region).findByRole('button', { name: 'Open well 101' })
    await userEvent.selectOptions(within(region).getByLabelText('Rig'), '10')
    await userEvent.click(screen.getByRole('button', { name: 'Standard' }))
    const view = onViewChange.mock.calls.at(-1)[0]
    expect(view).toMatchObject({ fieldKey: 'NIMR', basemap: 'standard', status: 'live', filters: { rig: '10' } })
    unmount()

    render(<FieldMap onSelectWell={vi.fn()} initialView={view} />)
    expect(await screen.findByTestId('map')).toHaveAttribute('data-basemap', 'standard')
    expect(screen.getByRole('button', { name: 'marker NIMR' })).toHaveAttribute('data-selected', 'true')
    await waitFor(() => expect(screen.getByLabelText('Rig')).toHaveValue('10'))
    expect(lastWellsCall()[1]).toEqual({ status: 'live', rig: '10' })
  })
})

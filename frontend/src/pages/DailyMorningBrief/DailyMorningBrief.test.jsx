/**
<<<<<<< HEAD
 * The page itself: that Refresh reloads every part of the front page at once.
=======
 * The page itself: what it shows on first load, in what order, and that
 * Refresh reloads every part of it at once.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
 *
 * The well row's own figures come from two calls -- the day's rollup and the
 * well's task activity -- and a refresh that reloaded one but not the other
 * would leave two halves of the same row describing different loads. Every
 * API call is stubbed here; this asserts what the page asks for, not what
 * comes back.
 */
<<<<<<< HEAD
import { render, screen, waitFor } from '@testing-library/react'
=======
import { render, screen, waitFor, within } from '@testing-library/react'
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import DailyMorningBrief from './DailyMorningBrief'
import api from '../../services/api'
<<<<<<< HEAD
=======
import { clearExplainCache } from '../../services/explainCache'
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

vi.mock('../../services/api', () => ({
  default: {
    health: vi.fn(),
    summary: vi.fn(),
    groupDetails: vi.fn(),
    wellActivity: vi.fn(),
    wellDetail: vi.fn(),
    recentDates: vi.fn(),
    wellMilestones: vi.fn(),
    explain: vi.fn(),
<<<<<<< HEAD
=======
    fieldMap: vi.fn(),
    fieldWells: vi.fn(),
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    exportUrl: vi.fn(() => '/api/daily/export'),
  },
}))

beforeEach(() => {
  Object.values(api).forEach((fn) => fn.mockReset?.())
<<<<<<< HEAD
  api.exportUrl.mockReturnValue('/api/daily/export')
  api.health.mockResolvedValue({ status: 'ok', database: {}, llm: {}, config: {} })
  api.recentDates.mockResolvedValue({ dates: [] })
  api.wellMilestones.mockResolvedValue({
    generated_at: '', window_days: 7, upcoming: [], overdue_count: 0, overdue: [],
=======
  clearExplainCache()
  api.exportUrl.mockReturnValue('/api/daily/export')
  api.health.mockResolvedValue({ status: 'ok', database: {}, llm: {}, config: {} })
  api.recentDates.mockResolvedValue({ dates: [] })
  api.explain.mockResolvedValue({
    report_date: '2026-08-01',
    scope: 'day',
    available: true,
    cached: false,
    explanation: 'The day in plain words.',
    evidence: { summary: { task_count: 1 } },
    evidence_withheld_from_model: [],
    sql_sources: [],
    proof: [],
  })
  api.wellMilestones.mockResolvedValue({
    generated_at: '',
    window_days: 7,
    upcoming: [
      {
        well_id: 555,
        milestone: 'RIG_ON',
        milestone_label: 'Rig on',
        deadline_date: '2026-08-05',
        days_remaining: 4,
        overdue: false,
      },
    ],
    overdue_count: 3,
    overdue: [],
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  })
  api.summary.mockResolvedValue({
    report_date: '2026-08-01',
    generated_at: '',
    totals: { task_count: 1, well_count: 1, status_counts: { ON_PLAN: 1 } },
    data_quality: {},
    status_groups: [],
    view_mode: 'detail',
    detail_view_task_threshold: 25,
    tasks: [],
  })
  api.groupDetails.mockResolvedValue({
    report_date: '2026-08-01',
    filters: {},
    task_count: 1,
    well_count: 1,
    wells: [{ well_id: 101, task_count: 1, status_counts: { ON_PLAN: 1 } }],
    tasks: [
      {
        well_id: 101,
        task_code: 'FLME1180-101',
        activity_description: 'Pipe Stringing',
        quantity_status: 'ON_PLAN',
      },
    ],
  })
  api.wellActivity.mockResolvedValue({
    report_date: '2026-08-01',
    well_count: 1,
    wells: [
      {
        well_id: 101,
        today_reported_task_count: 1,
        has_task_on_report_date: true,
        incomplete_task_count: 6,
        ongoing_task_count: 4,
        not_started_task_count: 1,
        ended_not_completed_task_count: 1,
        completed_task_count: 3,
        logical_task_count: 9,
        task_state_counts: {},
        last_task_date: '2026-08-01',
      },
    ],
    tasks: [],
  })
})

describe('the front page', () => {
  it('asks for both halves of the well row', async () => {
    render(<DailyMorningBrief />)
    await waitFor(() => expect(api.groupDetails).toHaveBeenCalled())
    await waitFor(() => expect(api.wellActivity).toHaveBeenCalled())
    expect(await screen.findByText('Well 101')).toBeInTheDocument()
<<<<<<< HEAD
    expect(screen.getByText('Incomplete')).toBeInTheDocument()
=======
    expect(screen.getByRole('columnheader', { name: 'Incomplete' })).toBeInTheDocument()
  })

  it('leads with the lifecycle deadlines, then the AI summary, then the wells', async () => {
    render(<DailyMorningBrief />)
    await screen.findByText('Well 101')
    await screen.findByText('The day in plain words.')

    const milestones = document.querySelector('.milestones-summary')
    const explanation = document.querySelector('.explain')
    const wells = document.querySelector('.well-table-wrap')

    // Reading order is the point: what is running out of time, then the
    // brief over the whole day, then the wells it is about.
    expect(milestones.compareDocumentPosition(explanation)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
    expect(explanation.compareDocumentPosition(wells)).toBe(
      Node.DOCUMENT_POSITION_FOLLOWING,
    )
  })

  it('shows the upcoming and overdue counts without being asked', async () => {
    render(<DailyMorningBrief />)
    const panel = document.querySelector('.milestones-summary')
    await waitFor(() => expect(within(panel).getByText('1')).toBeInTheDocument())
    expect(within(panel).getByText(/upcoming milestone/)).toBeInTheDocument()
    expect(within(panel).getByText('3')).toBeInTheDocument()
    expect(within(panel).getByText('overdue')).toBeInTheDocument()
    expect(within(panel).getByText('Well 555')).toBeInTheDocument()
  })

  it('opens the day s AI summary itself, over the whole view', async () => {
    render(<DailyMorningBrief />)
    expect(await screen.findByText('The day in plain words.')).toBeInTheDocument()
    expect(api.explain).toHaveBeenCalledWith({ report_date: expect.any(String), scope: 'day' })
  })

  it('brings the day s summary back from cache after it is closed, without asking again', async () => {
    render(<DailyMorningBrief />)
    await screen.findByText('The day in plain words.')

    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByText('The day in plain words.')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'Show AI summary of this view' }))
    expect(screen.getByText('The day in plain words.')).toBeInTheDocument()
    expect(api.explain).toHaveBeenCalledTimes(1)
  })

  it('regenerates the summary after Refresh rather than reusing the cached text', async () => {
    render(<DailyMorningBrief />)
    await screen.findByText('The day in plain words.')
    expect(api.explain).toHaveBeenCalledTimes(1)

    // Refresh purges the backend's cached explanations for the date, so the
    // session's copy of that same text must go with it.
    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() => expect(api.explain).toHaveBeenCalledTimes(2))
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  })

  it('reloads the task-activity figures when Refresh is pressed', async () => {
    render(<DailyMorningBrief />)
    await screen.findByText('Well 101')
    const before = api.wellActivity.mock.calls.length

    await userEvent.click(screen.getByRole('button', { name: 'Refresh' }))

    await waitFor(() =>
      expect(api.wellActivity.mock.calls.length).toBeGreaterThan(before),
    )
    // Refreshed explicitly, exactly like the day's own rollup beside it --
    // never left showing the previous load while the rest of the page updates.
    const refreshed = api.wellActivity.mock.calls.at(-1)[0]
    expect(refreshed.refresh).toBe(true)
    expect(api.groupDetails.mock.calls.at(-1)[0].refresh).toBe(true)
    expect(api.summary.mock.calls.at(-1)[1].refresh).toBe(true)
  })
})
<<<<<<< HEAD
=======

describe('a well s own page', () => {
  /** The day and each well get their own, distinguishable summary. */
  function explainByScope() {
    api.explain.mockImplementation((payload) =>
      Promise.resolve({
        report_date: payload.report_date,
        scope: payload.scope,
        available: true,
        cached: false,
        explanation:
          payload.scope === 'well'
            ? `Summary of well ${payload.well_id}.`
            : 'The day in plain words.',
        evidence: { summary: {} },
        evidence_withheld_from_model: [],
        sql_sources: [],
        proof: [],
      }),
    )
  }

  const wellCalls = () => api.explain.mock.calls.filter(([payload]) => payload.scope === 'well')

  beforeEach(() => {
    explainByScope()
    api.wellDetail.mockResolvedValue({
      report_date: '2026-08-01',
      well_id: 101,
      task_count: 1,
      tasks: [
        {
          task_daily_id: 1,
          well_id: 101,
          action_on: '2026-08-01',
          task_code: 'FLME1180-101',
          activity_description: 'Pipe Stringing',
          quantity_status: 'ON_PLAN',
        },
      ],
      activity: null,
      open_tasks: [],
    })
  })

  it('shows the exact summary the front-page row already made, without asking again', async () => {
    render(<DailyMorningBrief />)
    await screen.findByText('Well 101')

    await userEvent.click(screen.getByRole('button', { name: 'AI summary' }))
    expect(await screen.findByText('Summary of well 101.')).toBeInTheDocument()
    expect(wellCalls()).toHaveLength(1)
    expect(wellCalls()[0][0]).toEqual({ report_date: expect.any(String), scope: 'well', well_id: 101 })

    await userEvent.click(screen.getByRole('button', { name: 'Well 101' }))
    expect(await screen.findByText('Summary of well 101.')).toBeInTheDocument()
    // Served from the session cache: the same request, so no second call.
    expect(wellCalls()).toHaveLength(1)
  })

  it('opens the well s summary itself when the row never asked for it', async () => {
    render(<DailyMorningBrief />)
    await userEvent.click(await screen.findByRole('button', { name: 'Well 101' }))

    expect(await screen.findByText('Summary of well 101.')).toBeInTheDocument()
    expect(wellCalls()).toHaveLength(1)
    expect(wellCalls()[0][0]).toEqual({ report_date: expect.any(String), scope: 'well', well_id: 101 })
  })

  it('shows a quiet well s open work and summary instead of an error', async () => {
    api.wellDetail.mockResolvedValue({
      report_date: '2026-08-01',
      well_id: 101,
      task_count: 0,
      tasks: [],
      activity: {
        well_id: 101,
        today_reported_task_count: 0,
        has_task_on_report_date: false,
        open_task_count: 3,
        incomplete_task_count: 1,
        ongoing_task_count: 2,
        not_started_task_count: 1,
        ended_not_completed_task_count: 0,
        completed_task_count: 2,
        logical_task_count: 5,
        task_state_counts: {},
        last_task_date: '2026-07-20',
      },
      open_tasks: [
        {
          well_id: 101,
          schedule_id: 12,
          task_code: 'FLME1180-101',
          task_state: 'ONGOING',
          activity_description: 'Pipe Stringing',
          actual_start: '2026-07-10',
          actual_end: null,
          last_task_date: '2026-07-20',
        },
      ],
    })

    render(<DailyMorningBrief />)
    await userEvent.click(await screen.findByRole('button', { name: 'Well 101' }))

    expect(await screen.findByText(/^No daily task reported on/)).toBeInTheDocument()
    expect(screen.getByText('FLME1180-101')).toBeInTheDocument()
    expect(await screen.findByText('Summary of well 101.')).toBeInTheDocument()
    expect(screen.queryByText(/could not be completed/i)).not.toBeInTheDocument()
  })

  it('can close the well s summary and bring it back from cache', async () => {
    render(<DailyMorningBrief />)
    await userEvent.click(await screen.findByRole('button', { name: 'Well 101' }))
    await screen.findByText('Summary of well 101.')

    await userEvent.click(screen.getByRole('button', { name: 'Close' }))
    expect(screen.queryByText('Summary of well 101.')).not.toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: 'AI summary of well 101' }))
    expect(screen.getByText('Summary of well 101.')).toBeInTheDocument()
    expect(wellCalls()).toHaveLength(1)
  })
})

describe('the Oman Field Map', () => {
  const FIELDS = {
    location_note: 'Field positions are approximate representative locations and do not represent exact wellhead coordinates.',
    bounds: { lat_min: 16.4, lat_max: 26.6, lon_min: 51.8, lon_max: 60 },
    fields: [
      { field: 'NIMR', field_key: 'NIMR', latitude: 18.55, longitude: 55.65, location_type: 'approximate',
        location_basis: 'field', live_well_count: 1, completed_well_count: 0, total_well_count: 1 },
    ],
    unmapped_fields: [],
    coverage: { mapped_field_count: 1, wells_on_map: 1, total_wells: 1, live_wells_on_map: 1, live_wells: 1, wells_without_field: 0 },
  }
  const NIMR_WELLS = {
    field: 'NIMR', field_key: 'NIMR', live_well_count: 1, completed_well_count: 0, total_well_count: 1,
    matching_well_count: 1, filters: { category: [], function: [], completion_type: [], rig: [] },
    wells: [{ well_id: 101, status: 'Incomplete', status_code: 'INCOMPLETE', category: 'Development',
      function: 'Oil Producer', completion_type: null, rig: 'RIG-A', well_location: null }],
  }

  it('the front page offers the map without loading it', async () => {
    render(<DailyMorningBrief />)
    expect(await screen.findByText('Well 101')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Open Oman Field Map' })).toBeInTheDocument()
    expect(api.fieldMap).not.toHaveBeenCalled()
  })

  it('opens from the header as its own page, and Back returns to the brief', async () => {
    api.fieldMap.mockResolvedValue(FIELDS)
    render(<DailyMorningBrief />)
    await screen.findByText('Well 101')
    await userEvent.click(screen.getByRole('button', { name: 'Oman Field Map' }))
    expect(await screen.findByRole('heading', { name: 'Oman Field Map' })).toBeInTheDocument()
    expect(within(screen.getByRole('navigation', { name: 'Drill-down path' })).getByText('Oman Field Map'))
      .toHaveAttribute('aria-current', 'page')
    await userEvent.click(screen.getByRole('button', { name: '← Back' }))
    expect(await screen.findByRole('button', { name: 'Open Oman Field Map' })).toBeInTheDocument()
  })

  it('a map failure stays on the map page; the brief is untouched', async () => {
    api.fieldMap.mockRejectedValue(new Error('HTTP 503'))
    render(<DailyMorningBrief />)
    await userEvent.click(await screen.findByRole('button', { name: 'Open Oman Field Map' }))
    expect(await screen.findByText('Unable to load Oman field map.', { exact: false })).toBeInTheDocument()
    await userEvent.click(screen.getByRole('button', { name: '← Back' }))
    expect(await screen.findByText('Well 101')).toBeInTheDocument()
    expect(await screen.findByText('The day in plain words.')).toBeInTheDocument()
  })

  it('a well chosen on the map opens the existing well detail, and Back returns to the map as left', async () => {
    api.fieldMap.mockResolvedValue(FIELDS)
    api.fieldWells.mockResolvedValue(NIMR_WELLS)
    api.wellDetail.mockResolvedValue({
      report_date: '2026-08-01', well_id: 101, task_count: 0, tasks: [], activity: null, open_tasks: [],
    })
    render(<DailyMorningBrief />)

    await userEvent.click(await screen.findByRole('button', { name: 'Open Oman Field Map' }))
    await userEvent.type(await screen.findByLabelText('Search field'), 'NIMR')
    await userEvent.click(within(screen.getByRole('listbox')).getByRole('button', { name: 'NIMR' }))
    await userEvent.click(await screen.findByRole('button', { name: 'Open well 101' }))

    await waitFor(() => expect(api.wellDetail).toHaveBeenCalledWith(101, expect.any(String), expect.anything()))
    expect(screen.getByRole('button', { name: '← Back' })).toBeInTheDocument()

    await userEvent.click(screen.getByRole('button', { name: '← Back' }))
    expect(await screen.findByRole('region', { name: 'Wells in NIMR' })).toBeInTheDocument()
  })
})
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

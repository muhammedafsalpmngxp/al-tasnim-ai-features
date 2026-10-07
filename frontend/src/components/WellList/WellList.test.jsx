/**
<<<<<<< HEAD
 * The front page's well rows: what an operator sees, and what happens when
 * they click it.
=======
 * The front page's well table: what an operator sees in a row, and what
 * happens when they click into it.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
 *
 * Both backend responses are fixed here, so what is asserted is the rendering
 * and the interaction -- never a figure this component worked out for itself,
 * because it never works one out.
 */
import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import WellList from './WellList'
import api from '../../services/api'
<<<<<<< HEAD
=======
import { clearExplainCache } from '../../services/explainCache'
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

vi.mock('../../services/api', () => ({
  default: {
    explain: vi.fn(),
    wellActivity: vi.fn(),
  },
}))

const REPORT_DATE = '2026-08-01'

/** The day's own per-well rollup, as /api/daily/group-details returns it. */
const dayResource = {
  loading: false,
  error: null,
  data: {
    task_count: 2,
    wells: [
      {
        well_id: 101,
        task_count: 2,
        status_counts: { ON_PLAN: 1, BELOW_PLAN: 1 },
      },
    ],
    tasks: [
      {
        well_id: 101,
        task_code: 'FLME1180-101',
        activity_description: 'Pipe Stringing',
        quantity_status: 'ON_PLAN',
      },
      {
        well_id: 101,
        task_code: 'FLME1190-101',
        activity_description: 'Pipe Stringing',
        quantity_status: 'BELOW_PLAN',
      },
    ],
  },
}

/** The task activity, as /api/daily/well-activity returns it. */
const activityResource = {
  loading: false,
  error: null,
  data: {
    wells: [
      {
        // open = incomplete + ongoing, and the two never overlap.
        well_id: 101,
        today_reported_task_count: 2,
        has_task_on_report_date: true,
        open_task_count: 6,
        incomplete_task_count: 2,
        ongoing_task_count: 4,
        not_started_task_count: 1,
        ended_not_completed_task_count: 1,
        completed_task_count: 3,
        logical_task_count: 9,
        task_state_counts: {},
        last_task_date: '2026-08-01',
      },
      {
        // The case the front page exists to surface: open work, nothing
        // reported on the selected date, last seen weeks ago.
        well_id: 777,
        today_reported_task_count: 0,
        has_task_on_report_date: false,
        open_task_count: 5,
        incomplete_task_count: 3,
        ongoing_task_count: 2,
        not_started_task_count: 3,
        ended_not_completed_task_count: 0,
        completed_task_count: 1,
        logical_task_count: 6,
        task_state_counts: {},
        last_task_date: '2026-07-14',
      },
    ],
  },
}

function renderList(overrides = {}) {
  const props = {
    resource: dayResource,
    activityResource,
    reportDate: REPORT_DATE,
    onSelectWell: vi.fn(),
    ...overrides,
  }
  return { ...render(<WellList {...props} />), props }
}

<<<<<<< HEAD
/** The card for one well, so an assertion can never drift to another row. */
function wellCard(wellId) {
  return screen.getByText(`Well ${wellId}`).closest('article')
=======
/**
 * One well's own row group, so an assertion can never drift to another row.
 * Each well is its own <tbody>, which is what keeps everything it expands --
 * its task list, its AI summary -- inside it.
 */
function wellCard(wellId) {
  return screen.getByText(`Well ${wellId}`).closest('tbody')
}

/** The Tasks column: the first numeric cell of the row. */
function taskCountCell(card) {
  return card.querySelector('td.well-table__num')
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
}

beforeEach(() => {
  api.wellActivity.mockReset()
  api.explain.mockReset()
<<<<<<< HEAD
=======
  // The explanation cache lives as long as the page does, so a test must not
  // inherit the previous one's answers.
  clearExplainCache()
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  api.wellActivity.mockResolvedValue({
    wells: [],
    tasks: [
      {
        well_id: 777,
        schedule_id: 12,
        task_code: 'FLME1030-777',
        task_state: 'ONGOING',
        actual_start: '2026-07-02',
        actual_end: null,
        last_task_date: '2026-07-14',
        activity_description: 'Pipe Stringing',
      },
      {
        well_id: 777,
        schedule_id: 12,
        task_code: 'FLME1040-777',
        task_state: 'NOT_STARTED',
        actual_start: null,
        actual_end: null,
        last_task_date: '2026-07-10',
        activity_description: null,
      },
    ],
  })
  api.explain.mockResolvedValue({
    available: true,
    cached: false,
    explanation: 'A plain explanation.',
    evidence: { well_id: 101, task_activity: { ongoing_task_count: 4 } },
    evidence_withheld_from_model: [],
  })
})

describe('the task-activity figures on a well row', () => {
  it('shows every well its open, incomplete, ongoing, reported-today and last task date', () => {
    renderList()
    const card = wellCard(101)
    expect(within(card).getByText('Open').previousSibling).toHaveTextContent('6')
    expect(within(card).getByText('Incomplete').previousSibling).toHaveTextContent('2')
    expect(within(card).getByText('Ongoing').previousSibling).toHaveTextContent('4')
    expect(within(card).getByText('Reported today').previousSibling).toHaveTextContent('2')
<<<<<<< HEAD
    expect(within(card).getByText(/Last task date/)).toHaveTextContent('2026-08-01')
=======
    expect(within(card).getByText('2026-08-01')).toBeInTheDocument()
  })

  it('names every column once, in the header rather than on every row', () => {
    renderList()
    const headers = screen
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent.trim())
    expect(headers).toEqual([
      'Well',
      'Activity',
      'Tasks',
      'Validation status',
      'Open',
      'Incomplete',
      'Ongoing',
      'Reported today',
      'Last task date',
      'AI summary',
    ])
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  })

  it('shows incomplete and ongoing as two halves that add up to the total', () => {
    // The whole point of the change: the two figures beside the total do not
    // overlap, so a reader can add them and get the number on the left.
    renderList()
    for (const wellId of [101, 777]) {
      const card = wellCard(wellId)
      const read = (label) => Number(within(card).getByText(label).previousSibling.textContent)
      expect(read('Incomplete') + read('Ongoing')).toBe(read('Open'))
    }
  })

  it('keeps the existing task count, activity and status counts on the row', () => {
    renderList()
    const card = wellCard(101)
<<<<<<< HEAD
    expect(within(card).getByText(/tasks$/)).toHaveTextContent('2 tasks')
=======
    expect(taskCountCell(card)).toHaveTextContent('2')
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    expect(within(card).getByText(/Pipe Stringing/)).toBeInTheDocument()
    expect(within(card).getByText('On Plan')).toBeInTheDocument()
    expect(within(card).getByText('Below Plan')).toBeInTheDocument()
  })

  it('lists a well that reported nothing on the date, and says so', () => {
    renderList()
    const card = wellCard(777)
    expect(within(card).getByText('No task reported on this date')).toBeInTheDocument()
    expect(within(card).getByText('Open').previousSibling).toHaveTextContent('5')
    expect(within(card).getByText('Reported today').previousSibling).toHaveTextContent('0')
<<<<<<< HEAD
    expect(within(card).getByText(/Last task date/)).toHaveTextContent('2026-07-14')
  })

  it('opens the well itself when the row is clicked', async () => {
=======
    expect(within(card).getByText('2026-07-14')).toBeInTheDocument()
  })

  it('opens the well itself when the well id is clicked', async () => {
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    const { props } = renderList()
    await userEvent.click(screen.getByText('Well 101'))
    expect(props.onSelectWell).toHaveBeenCalledWith(101)
  })
})

describe('expanding a figure to see the tasks behind it', () => {
  it('shows the tasks inside the same well card', async () => {
    renderList()
    const card = wellCard(777)
    await userEvent.click(within(card).getByText('Ongoing'))

    await waitFor(() =>
      expect(within(card).getByText('Ongoing tasks on well 777')).toBeInTheDocument(),
    )
    expect(api.wellActivity).toHaveBeenCalledWith(
      { date: REPORT_DATE, wellId: 777 },
      expect.anything(),
    )
    expect(within(card).getByText('FLME1030-777')).toBeInTheDocument()
  })

  it('shows only the ongoing tasks under the ongoing figure', async () => {
    renderList()
    const card = wellCard(777)
    await userEvent.click(within(card).getByText('Ongoing'))

    await waitFor(() => expect(within(card).getByText('FLME1030-777')).toBeInTheDocument())
    expect(within(card).queryByText('FLME1040-777')).not.toBeInTheDocument()
  })

  it('leaves the ongoing tasks out of the incomplete figure', async () => {
    renderList()
    const card = wellCard(777)
    await userEvent.click(within(card).getByText('Incomplete'))

    await waitFor(() => expect(within(card).getByText('FLME1040-777')).toBeInTheDocument())
    // The ongoing one belongs to the ongoing figure, not this one.
    expect(within(card).queryByText('FLME1030-777')).not.toBeInTheDocument()
  })

  it('shows every open task under the total', async () => {
    renderList()
    const card = wellCard(777)
    await userEvent.click(within(card).getByText('Open'))

    await waitFor(() => expect(within(card).getByText('FLME1030-777')).toBeInTheDocument())
    expect(within(card).getByText('FLME1040-777')).toBeInTheDocument()
    expect(within(card).getByText('Open tasks on well 777')).toBeInTheDocument()
  })

  it('never opens one well s detail inside another well s card', async () => {
    renderList()
    const card = wellCard(777)
    await userEvent.click(within(card).getByText('Ongoing'))

    await waitFor(() =>
      expect(within(card).getByText('Ongoing tasks on well 777')).toBeInTheDocument(),
    )
    expect(within(wellCard(101)).queryByText(/tasks on well/)).not.toBeInTheDocument()
  })

  it('lists the day s own tasks under the reported-today figure, without a request', async () => {
    renderList()
    const card = wellCard(101)
    await userEvent.click(within(card).getByText('Reported today'))

    expect(await within(card).findByText('FLME1180-101')).toBeInTheDocument()
    expect(api.wellActivity).not.toHaveBeenCalled()
  })

  it('cannot be expanded when the figure is zero', () => {
    renderList()
    const card = wellCard(777)
    expect(within(card).getByText('Reported today').closest('button')).toBeDisabled()
  })
})

describe('the AI summary stays with its own well', () => {
<<<<<<< HEAD
  it('renders inside the well card it was opened from', async () => {
=======
  it('renders inside the well row it was opened from', async () => {
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    renderList()
    const card = wellCard(101)
    await userEvent.click(within(card).getByRole('button', { name: 'AI summary' }))

    expect(
      await within(card).findByText(/AI explanation — well 101/i),
    ).toBeInTheDocument()
    expect(api.explain).toHaveBeenCalledWith({
      report_date: REPORT_DATE,
      scope: 'well',
      well_id: 101,
    })
  })

<<<<<<< HEAD
=======
  it('is the last cell of the row, and asks only about that one well', async () => {
    renderList()
    const row = wellCard(101).querySelector('tr.well-table__row')
    const lastCell = row.lastElementChild

    expect(within(lastCell).getByRole('button', { name: 'AI summary' })).toBeInTheDocument()

    await userEvent.click(within(lastCell).getByRole('button', { name: 'AI summary' }))
    await within(wellCard(101)).findByText(/AI explanation/i)

    // Never the table's summary, and never the neighbouring well's.
    expect(api.explain).toHaveBeenCalledTimes(1)
    expect(api.explain.mock.calls[0][0]).toEqual({
      report_date: REPORT_DATE,
      scope: 'well',
      well_id: 101,
    })
    expect(within(wellCard(777)).queryByText(/AI explanation/i)).not.toBeInTheDocument()
  })

  it('reuses the answer it already has when a row is reopened', async () => {
    renderList()
    const card = wellCard(101)
    await userEvent.click(within(card).getByRole('button', { name: 'AI summary' }))
    await within(card).findByText(/AI explanation/i)

    await userEvent.click(within(card).getByRole('button', { name: 'Hide AI summary' }))
    await userEvent.click(within(card).getByRole('button', { name: 'AI summary' }))

    // Straight back from the session cache: no spinner, no second request.
    expect(within(card).getByText('A plain explanation.')).toBeInTheDocument()
    expect(api.explain).toHaveBeenCalledTimes(1)
  })

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  it('closes when the operator moves to another report date', async () => {
    const { rerender } = renderList()
    const card = wellCard(101)
    await userEvent.click(within(card).getByRole('button', { name: 'AI summary' }))
    await within(card).findByText(/AI explanation/i)

    // An open explanation describes one specific date; it must not sit there
    // describing a date the operator has navigated away from.
    rerender(
      <WellList
        resource={dayResource}
        activityResource={activityResource}
        reportDate="2026-08-02"
        onSelectWell={vi.fn()}
      />,
    )

    expect(screen.queryByText(/AI explanation/i)).not.toBeInTheDocument()
    expect(within(wellCard(101)).getByRole('button', { name: 'AI summary' })).toBeInTheDocument()
  })

  it('closes an expanded task list when the report date changes', async () => {
    const { rerender } = renderList()
    const card = wellCard(777)
    await userEvent.click(within(card).getByText('Ongoing'))
    await waitFor(() =>
      expect(within(card).getByText('Ongoing tasks on well 777')).toBeInTheDocument(),
    )

    rerender(
      <WellList
        resource={dayResource}
        activityResource={activityResource}
        reportDate="2026-08-02"
        onSelectWell={vi.fn()}
      />,
    )

    expect(screen.queryByText('Ongoing tasks on well 777')).not.toBeInTheDocument()
  })

  it('asks for the explanation once, however often the list re-renders', async () => {
    const { rerender } = renderList()
    const card = wellCard(101)
    await userEvent.click(within(card).getByRole('button', { name: 'AI summary' }))
    await within(card).findByText(/AI explanation/i)

    rerender(
      <WellList
        resource={dayResource}
        activityResource={activityResource}
        reportDate={REPORT_DATE}
        onSelectWell={vi.fn()}
      />,
    )

    await waitFor(() => expect(api.explain).toHaveBeenCalledTimes(1))
  })
})

describe('which wells the page lists', () => {
  it('lists reporting wells and wells with open work by default', () => {
    renderList()
    expect(wellCard(101)).toBeTruthy()
    expect(wellCard(777)).toBeTruthy()
  })

  it('can narrow to the wells that reported on the selected date', async () => {
    renderList()
    await userEvent.click(screen.getByRole('button', { name: /Reported on this date/ }))
    expect(screen.getByText('Well 101')).toBeInTheDocument()
    expect(screen.queryByText('Well 777')).not.toBeInTheDocument()
  })

  it('finds a well by id', async () => {
    renderList()
    await userEvent.type(screen.getByLabelText('Find a well by ID'), '777')
    expect(screen.getByText('Well 777')).toBeInTheDocument()
    expect(screen.queryByText('Well 101')).not.toBeInTheDocument()
  })

  it('reports a failed task-activity load without hiding the day s own figures', () => {
    renderList({
      activityResource: { loading: false, error: new Error('boom'), data: null },
    })
    expect(screen.getByText(/Task-activity figures could not be loaded/)).toBeInTheDocument()
    expect(screen.getByText('Well 101')).toBeInTheDocument()
    expect(within(wellCard(101)).getByText('On Plan')).toBeInTheDocument()
  })
})

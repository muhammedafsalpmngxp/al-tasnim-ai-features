import { render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import AskAgent from './AskAgent'
import { AgentVerdict } from '../ExplainPanel/ExplainPanel'
import api from '../../services/api'

vi.mock('../../services/api', () => ({ default: { ask: vi.fn() } }))

const ANSWER = {
  kind: 'ask',
  available: true,
  cached: false,
  explanation: 'Well 30349 has 3 open tasks before an upcoming Rig on deadline.',
  verified: true,
  verification: { passed: true, issues: [], checked_numbers: 2 },
  revisions: 0,
  models: { planner: 'gpt-5.6-luna', writer: 'gpt-4o-mini' },
  trace: [
    { node: 'plan', mode: 'planner', duration_s: 0.9 },
    { node: 'tool', tool: 'deadline_pressure', args: {}, ok: true, why: 'deadlines', duration_s: 0.04 },
    { node: 'write', mode: 'draft', duration_s: 1.2 },
    { node: 'verify', passed: true, issues: 0, duration_s: 0.001 },
  ],
  duration_s: 2.14,
}

beforeEach(() => api.ask.mockReset())

describe('AskAgent', () => {
  it('asks the agents about the selected date and shows the verified answer with its steps', async () => {
    api.ask.mockResolvedValue(ANSWER)
    render(<AskAgent reportDate="2026-09-24" />)

    await userEvent.type(screen.getByLabelText('Question for the agents'), 'Which wells need attention?')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))

    expect(api.ask).toHaveBeenCalledWith({ reportDate: '2026-09-24', question: 'Which wells need attention?' })
    expect(await screen.findByText(/3 open tasks before an upcoming Rig on deadline/)).toBeInTheDocument()
    expect(screen.getByText(/Verified — 2 figures checked/)).toBeInTheDocument()
    expect(screen.getByText(/Agent steps — 4 steps, 2.14 s/)).toBeInTheDocument()
    expect(screen.getByText('1.2 s')).toBeInTheDocument()
    expect(screen.getByText('deadline_pressure')).toBeInTheDocument()
  })

  it('a suggestion asks straight away', async () => {
    api.ask.mockResolvedValue(ANSWER)
    render(<AskAgent reportDate="2026-09-24" />)
    await userEvent.click(screen.getByRole('button', { name: /open work before an upcoming deadline/ }))
    expect(api.ask).toHaveBeenCalledTimes(1)
    expect(await screen.findByText(/Verified/)).toBeInTheDocument()
  })

  it('says plainly when the agents could not answer', async () => {
    api.ask.mockRejectedValue(new Error('Cannot reach the API'))
    render(<AskAgent reportDate="2026-09-24" />)
    await userEvent.type(screen.getByLabelText('Question for the agents'), 'anything at all')
    await userEvent.click(screen.getByRole('button', { name: 'Ask' }))
    expect(await screen.findByText('The agents could not answer.')).toBeInTheDocument()
  })
})

describe('AgentVerdict', () => {
  it('an answer that failed verification is marked unverified, with the reasons', () => {
    render(
      <AgentVerdict
        result={{
          available: true,
          verification: { passed: false, issues: ['the figure 47 is not in the evidence'] },
        }}
      />,
    )
    expect(screen.getByText('Not verified.')).toBeInTheDocument()
    expect(screen.getByText('the figure 47 is not in the evidence')).toBeInTheDocument()
  })

  it('shows nothing for a classic answer, which carries no verification', () => {
    const { container } = render(<AgentVerdict result={{ available: true }} />)
    expect(container).toBeEmptyDOMElement()
  })
})

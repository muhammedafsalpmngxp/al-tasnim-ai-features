import { useState } from 'react'
import api from '../../services/api'
import { AgentSteps, AgentVerdict } from '../ExplainPanel/ExplainPanel'
import MarkdownLite from '../ExplainPanel/MarkdownLite'
import { Spinner } from '../common'

/** A few questions the tools can genuinely answer, as one-click starters. */
const SUGGESTIONS = [
  'Which wells have open work before an upcoming deadline?',
  'Which wells did not report today but still have open work?',
  'What data-quality problems are there today?',
]

/**
 * Ask the agents a question about the selected report date.
 *
 * The planner agent picks which read-only tools to call, the writer answers
 * from their results only, and the verifier checks every figure before the
 * answer appears -- all of it visible under "Agent steps". Nothing typed here
 * can change data: every tool is a fixed SELECT behind the same services the
 * rest of the page uses.
 */
export default function AskAgent({ reportDate }) {
  const [question, setQuestion] = useState('')
  const [state, setState] = useState({ loading: false, result: null, error: null, asked: '' })

  const ask = async (text) => {
    const trimmed = (text ?? question).trim()
    if (trimmed.length < 3 || state.loading) return
    setQuestion(trimmed)
    setState({ loading: true, result: null, error: null, asked: trimmed })
    try {
      const result = await api.ask({ reportDate, question: trimmed })
      setState({ loading: false, result, error: null, asked: trimmed })
    } catch (error) {
      setState({ loading: false, result: null, error, asked: trimmed })
    }
  }

  const { loading, result, error, asked } = state

  return (
    <section className="ask-agent" aria-label="Ask the agents">
      <form
        className="ask-agent__form"
        onSubmit={(event) => {
          event.preventDefault()
          ask()
        }}
      >
        <input
          type="text"
          className="ask-agent__input"
          placeholder={`Ask about ${reportDate} — e.g. "Why does well 30349 have open work?"`}
          value={question}
          maxLength={500}
          onChange={(event) => setQuestion(event.target.value)}
          aria-label="Question for the agents"
        />
        <button type="submit" className="btn" disabled={loading || question.trim().length < 3}>
          {loading ? 'Asking…' : 'Ask'}
        </button>
      </form>

      {!result && !loading && !error ? (
        <div className="ask-agent__suggestions">
          {SUGGESTIONS.map((suggestion) => (
            <button
              key={suggestion}
              type="button"
              className="btn btn--ghost ask-agent__suggestion"
              onClick={() => ask(suggestion)}
            >
              {suggestion}
            </button>
          ))}
        </div>
      ) : null}

      {loading ? (
        <div className="explain__body" style={{ color: 'var(--text-muted)' }}>
          <Spinner /> Planning, looking up the data and checking the answer…
        </div>
      ) : null}

      {error ? (
        <div className="explain__body">
          <strong>The agents could not answer.</strong>
          {'\n'}
          {error.message}
        </div>
      ) : null}

      {result ? (
        <div className={result.cached ? 'explain explain--cached' : 'explain'}>
          <div className="explain__head">
            <h3 className="explain__title">{asked}</h3>
            <button
              type="button"
              className="btn btn--ghost"
              onClick={() => setState({ loading: false, result: null, error: null, asked: '' })}
            >
              Close
            </button>
          </div>
          {result.available ? (
            <div className="explain__body">
              <MarkdownLite text={result.explanation} />
            </div>
          ) : (
            <div className="explain__body">
              <strong>No answer.</strong>
              {'\n'}
              {result.error}
            </div>
          )}
          <AgentVerdict result={result} />
          <AgentSteps result={result} />
        </div>
      ) : null}
    </section>
  )
}

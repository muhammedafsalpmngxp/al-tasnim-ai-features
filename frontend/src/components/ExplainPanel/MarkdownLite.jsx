/**
 * Minimal, safe renderer for the explanation text.
 *
 * The model is instructed to write flowing prose paragraphs, not bullets or
 * headings -- but language models format their answers even when asked not
 * to, so the small subset they sometimes reach for anyway (headings, bullets,
 * bold, tables) is still handled here as a fallback. Everything is built as
 * React elements from plain strings: no HTML is ever injected, so model
 * output cannot inject markup into the dashboard.
 *
 * One backtick-wrapped exception is deliberate, not a fallback: the system
 * instruction (`llm_service.SYSTEM_INSTRUCTION`) tells the model to wrap every
 * literal value it copies from the evidence -- a well ID, task code, quantity,
 * unit, date, or status -- in backticks. Rendered in the same colour the rest
 * of the app already uses for a database-sourced number (`--num`) plus
 * italics, that value visually separates from the model's own sentence around
 * it, the same way `Num` already sets a figure apart from its label elsewhere.
 */

function renderInline(text, keyPrefix) {
  // Split on **bold** and `value`, keeping the delimiters' contents.
  const parts = String(text).split(/(\*\*[^*]+\*\*|`[^`]+`)/g)
  return parts.filter(Boolean).map((part, index) => {
    const key = `${keyPrefix}-${index}`
    if (part.startsWith('**') && part.endsWith('**')) {
      return <strong key={key}>{part.slice(2, -2)}</strong>
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <em key={key} className="explain__value">
          {part.slice(1, -1)}
        </em>
      )
    }
    return <span key={key}>{part}</span>
  })
}

export default function MarkdownLite({ text }) {
  if (!text) return null

  const blocks = []
  let bullets = []
  let paragraphLines = []

  const flushBullets = () => {
    if (!bullets.length) return
    blocks.push(
      <ul key={`ul-${blocks.length}`} style={{ margin: '6px 0 10px', paddingLeft: 20 }}>
        {bullets.map((item, index) => (
          <li key={index} style={{ marginBottom: 3 }}>
            {renderInline(item, `li-${blocks.length}-${index}`)}
          </li>
        ))}
      </ul>,
    )
    bullets = []
  }

  // Consecutive plain lines are one paragraph -- a blank line, a bullet, a
  // heading or a table row all end it. This is what turns prose the model
  // wrapped across several lines back into one flowing paragraph instead of
  // one choppy fragment per line.
  const flushParagraph = () => {
    if (!paragraphLines.length) return
    const joined = paragraphLines.join(' ')
    blocks.push(
      <p key={`p-${blocks.length}`}>{renderInline(joined, `p-${blocks.length}`)}</p>,
    )
    paragraphLines = []
  }

  String(text)
    .split('\n')
    .forEach((rawLine) => {
      const line = rawLine.trimEnd()
      const bullet = line.match(/^\s*[-*•]\s+(.*)$/)
      if (bullet) {
        flushParagraph()
        bullets.push(bullet[1])
        return
      }
      flushBullets()

      if (!line.trim()) {
        flushParagraph()
        return
      }

      const heading = line.match(/^#{1,6}\s+(.*)$/)
      if (heading) {
        flushParagraph()
        blocks.push(
          <p key={`h-${blocks.length}`} style={{ fontWeight: 650 }}>
            {renderInline(heading[1], `h-${blocks.length}`)}
          </p>,
        )
        return
      }

      // A markdown table row: keep it aligned rather than dropping the pipes.
      if (line.trim().startsWith('|')) {
        if (/^\s*\|[\s:|-]+\|\s*$/.test(line)) return // separator row
        flushParagraph()
        blocks.push(
          <div
            key={`t-${blocks.length}`}
            style={{ fontFamily: 'var(--mono)', fontSize: 12, whiteSpace: 'pre' }}
          >
            {line
              .split('|')
              .filter((cell) => cell.trim() !== '')
              .map((cell) => cell.trim())
              .join('  ·  ')}
          </div>,
        )
        return
      }

      paragraphLines.push(line.trim())
    })

  flushBullets()
  flushParagraph()
  return <div>{blocks}</div>
}

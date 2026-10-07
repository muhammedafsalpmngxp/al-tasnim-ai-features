import api from '../../services/api'
import { Spinner } from '../common'
import ThemeToggle from '../common/ThemeToggle'

/** Date selector, refresh and export. The date shown is always the date loaded. */
export default function Header({
  reportDate,
  onSelectDate,
  onRefresh,
  loading,
  recentDates,
  health,
  theme,
  onToggleTheme,
<<<<<<< HEAD
=======
  onOpenFieldMap,
  fieldMapOpen = false,
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
}) {
  return (
    <header className="header">
      <div className="header__inner">
        <div>
          <h1 className="header__title">Daily Morning Brief</h1>
          <p className="header__subtitle">Al Tasnim — live well construction, daily entry validation</p>
        </div>

        <div className="header__spacer" />

        <div className="header__controls">
          <div className="date-field">
            <label htmlFor="report-date">Report date</label>
            <input
              id="report-date"
              type="date"
              value={reportDate}
              onChange={(event) => event.target.value && onSelectDate(event.target.value)}
            />
          </div>

          {recentDates?.length ? (
            <div className="date-field">
              <label htmlFor="recent-date">Recent entries</label>
              <select
                id="recent-date"
                value={recentDates.some((d) => d.report_date === reportDate) ? reportDate : ''}
                onChange={(event) => event.target.value && onSelectDate(event.target.value)}
              >
                <option value="">Jump to…</option>
                {recentDates.map((entry) => (
                  <option key={entry.report_date} value={entry.report_date}>
                    {entry.report_date} — {entry.well_count} wells, {entry.row_count} entries
                  </option>
                ))}
              </select>
            </div>
          ) : null}

<<<<<<< HEAD
=======
          {onOpenFieldMap ? (
            <button
              type="button"
              className="btn"
              onClick={onOpenFieldMap}
              aria-current={fieldMapOpen ? 'page' : undefined}
              disabled={fieldMapOpen}
            >
              Oman Field Map
            </button>
          ) : null}

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
          <button type="button" className="btn" onClick={onRefresh} disabled={loading}>
            {loading ? <Spinner /> : null}
            Refresh
          </button>

          <ThemeToggle theme={theme} onToggle={onToggleTheme} />

          <a
            className="btn btn--primary"
            href={api.exportUrl(reportDate)}
            download
            title={`Download daily_morning_brief_${reportDate}.xlsx`}
          >
            Export Excel
          </a>
        </div>
      </div>

      <div className="statusbar">
        <span>
          <span
            className={`statusbar__dot statusbar__dot--${
              health === null ? 'idle' : health?.database?.connected ? 'ok' : 'bad'
            }`}
          />
          {health === null
            ? 'Checking database…'
            : health?.database?.connected
              ? `Database ${health.database.database_name} · read-only`
              : 'Database unavailable'}
        </span>
        <span>
          <span
            className={`statusbar__dot statusbar__dot--${
              health === null ? 'idle' : health?.llm?.configured ? 'ok' : 'idle'
            }`}
          />
          {health?.llm?.configured
            ? 'AI explanation available'
            : 'AI explanation not configured — the dashboard works without it'}
        </span>
        <span>Viewing {reportDate}</span>
      </div>
    </header>
  )
}

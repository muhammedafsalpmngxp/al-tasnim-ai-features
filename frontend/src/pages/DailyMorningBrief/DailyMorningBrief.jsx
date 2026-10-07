<<<<<<< HEAD
import { useCallback, useEffect, useState } from 'react'
=======
import { useCallback, useEffect, useMemo, useState } from 'react'
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
import Header from '../../components/MorningBrief/Header'
import DayStrip from '../../components/DayStrip/DayStrip'
import WellList from '../../components/WellList/WellList'
import WellDetail from '../../components/WellDetail/WellDetail'
import ExplainPanel from '../../components/ExplainPanel/ExplainPanel'
<<<<<<< HEAD
import MilestonesPage from '../../components/MilestoneBanner/MilestonesPage'
=======
import AskAgent from '../../components/AskAgent/AskAgent'
import FieldMap from '../../components/FieldMap/FieldMap'
import FieldMapEntry from '../../components/FieldMap/FieldMapEntry'
import MilestonesPage from '../../components/MilestoneBanner/MilestonesPage'
import MilestonesSummary from '../../components/MilestoneBanner/MilestonesSummary'
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
import { ErrorState, LoadingBlock } from '../../components/common'
import useApiResource from '../../hooks/useApiResource'
import useDailyBrief from '../../hooks/useDailyBrief'
import useTheme from '../../hooks/useTheme'
import api from '../../services/api'
<<<<<<< HEAD
=======
import { clearExplainCache } from '../../services/explainCache'
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

/**
 * The drill-down journey:
 *
 *   Daily Morning Brief -> Well -> Task -> Explain
 *
<<<<<<< HEAD
 * One row per well is the whole front page; everything else is one click
 * away. This component routes between levels and keeps Back navigation
 * honest; it performs no business calculation.
=======
 * The front page reads top to bottom in the order an operator needs it:
 *
 *   1. lifecycle deadlines  -- what is running out of time, measured against
 *      today rather than the selected report date;
 *   2. the day's AI summary -- opened by the page itself on first load, over
 *      everything the front page can see, so the brief is readable without
 *      asking for it;
 *   3. the wells table      -- one row per well, each row's last cell its own
 *      AI summary.
 *
 * This component routes between levels and keeps Back navigation honest; it
 * performs no business calculation.
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
 */
export default function DailyMorningBrief() {
  const {
    reportDate,
    selectDate,
    refresh,
    refreshToken,
    summary,
    health,
    recentDates,
    current,
    breadcrumbs,
    drillTo,
    goBack,
    goToLevel,
  } = useDailyBrief()

  const { theme, toggle: toggleTheme } = useTheme()

<<<<<<< HEAD
  const [explainRequest, setExplainRequest] = useState(null)
=======
  /**
   * The front page's own summary is open from the start -- the operator should
   * not have to ask for the brief the page exists to give them. Closing it is
   * respected until the report date changes, at which point the page is
   * describing a different day and opens the new day's summary the same way it
   * opened the first.
   *
   * Reopening it costs nothing: the text is held in the session explanation
   * cache, and the backend holds its own evidence-hashed cache behind that, so
   * no model call is made for an answer that already exists.
   */
  const [dayExplainOpen, setDayExplainOpen] = useState(true)

  /**
   * A well's own page opens that well's AI summary the same way: it is the
   * first thing worth reading about the well, and on a day the well reported
   * nothing it is most of what there is to read.
   */
  const [wellExplainOpen, setWellExplainOpen] = useState(true)
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

  // An open explanation always describes one specific selection on one
  // specific date. Changing the date makes every currently-open one stale
  // at a glance -- rather than leave it sitting open describing a date the
  // operator has since navigated away from, close it the moment the date
  // changes, the same way the drill-down path already resets.
  useEffect(() => {
<<<<<<< HEAD
    setExplainRequest(null)
  }, [reportDate])

=======
    setDayExplainOpen(true)
    setWellExplainOpen(true)
  }, [reportDate])

  /**
   * Stable across re-renders so the panel asks once per date rather than on
   * every render of the page around it.
   */
  const dayExplainRequest = useMemo(
    () => ({ report_date: reportDate, scope: 'day' }),
    [reportDate],
  )

  /**
   * The well page's summary request is field-for-field the one the well's
   * row on the front page sends (WellList's WellRow). That is what makes it
   * the same summary: the same key in the session cache, so a summary already
   * read on the front page comes straight back with no request, and the same
   * evidence on the backend, so even a first open here reuses the answer the
   * backend cached for the row. Each well id gets its own request, so one
   * well's summary can never appear on another's page.
   */
  const wellId = current?.type === 'well' ? current.wellId : null
  const wellExplainRequest = useMemo(
    () => (wellId === null ? null : { report_date: reportDate, scope: 'well', well_id: wellId }),
    [reportDate, wellId],
  )

  /**
   * Refresh purges the backend's cached explanations for the date, so the
   * session's copies of that same superseded text go with it -- otherwise the
   * page would keep showing text the operator has just asked to have redone.
   */
  const refreshEverything = useCallback(() => {
    clearExplainCache()
    refresh()
  }, [refresh])

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  const milestones = useApiResource((signal) => api.wellMilestones(undefined, signal), [])

  /**
   * The per-well rollup for the whole day: the same `/group-details` endpoint
   * used everywhere a slice of the day is drilled into, called with no filter
   * at all. That is a well-defined query, not a special case -- it selects
   * every task for the date, from the same cached dataset the summary call
   * already resolved, so this costs a second cheap request rather than a
   * second database round trip.
   *
   * Depends on `refreshToken`, not just `reportDate`: this is the data behind
   * the main dashboard, so pressing "Refresh" must reload it exactly the same
   * way it reloads `summary` -- otherwise Refresh would silently leave the
   * well list showing the previous load while everything else updated.
   */
  const wellsForDay = useApiResource(
    (signal) => api.groupDetails({ date: reportDate, refresh: refreshToken > 0 }, signal),
    [reportDate, refreshToken],
  )

  /**
   * The other half of each well row: its task activity as of the selected
   * date -- incomplete tasks, ongoing tasks, whether it reported anything on
   * the date, and when it was last seen in the task records. Computed
   * entirely by the backend over the live well universe, not over one date's
   * task rows, which is why a well with nothing to report today is still on
   * the page.
   *
   * Depends on `refreshToken` for exactly the same reason `wellsForDay` does:
   * these figures sit on the same rows, so "Refresh" must reload both or the
   * two halves of a row would be from different loads.
   */
  const wellActivity = useApiResource(
    (signal) => api.wellActivity({ date: reportDate, refresh: refreshToken > 0 }, signal),
    [reportDate, refreshToken],
  )

  const openWell = useCallback(
<<<<<<< HEAD
    (wellId) => {
      setExplainRequest(null)
      drillTo({ type: 'well', label: `Well ${wellId}`, wellId })
=======
    (id) => {
      setWellExplainOpen(true)
      drillTo({ type: 'well', label: `Well ${id}`, wellId: id })
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
    },
    [drillTo],
  )

  const openMilestones = useCallback(() => {
<<<<<<< HEAD
    setExplainRequest(null)
    drillTo({ type: 'milestones', label: 'Upcoming Milestones' })
  }, [drillTo])

  const explainCurrent = useCallback(() => {
    const request = { report_date: reportDate, scope: 'day' }
    if (current?.type === 'well') {
      Object.assign(request, { scope: 'well', well_id: current.wellId })
    }
    setExplainRequest(request)
  }, [current, reportDate])
=======
    drillTo({ type: 'milestones', label: 'Upcoming Milestones' })
  }, [drillTo])

  /**
   * The Oman Field Map is its own level, one step below the front page, from
   * wherever it is opened -- so Back from it always returns to the brief, and
   * Back from a well opened on it returns to the map. Its selection, filters
   * and basemap are kept here so that return finds the map as it was left.
   */
  const [fieldMapView, setFieldMapView] = useState(null)
  const openFieldMap = useCallback(() => {
    goToLevel(-1)
    drillTo({ type: 'field-map', label: 'Oman Field Map' })
  }, [goToLevel, drillTo])
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)

  return (
    <div className="app">
      <Header
        reportDate={reportDate}
        onSelectDate={selectDate}
<<<<<<< HEAD
        onRefresh={refresh}
=======
        onRefresh={refreshEverything}
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        loading={summary.loading}
        recentDates={recentDates.data?.dates}
        health={health.loading ? null : health.data}
        theme={theme}
        onToggleTheme={toggleTheme}
<<<<<<< HEAD
      />

      <main className="app__main">
        {!current && summary.data ? (
          <DayStrip
            totals={summary.data.totals}
            milestones={milestones}
            onOpenMilestones={openMilestones}
          />
        ) : null}

=======
        onOpenFieldMap={openFieldMap}
        fieldMapOpen={current?.type === 'field-map'}
      />

      <main className="app__main">
        {/* 1. What is running out of time, before anything about today. */}
        {!current ? (
          <MilestonesSummary resource={milestones} onOpenMilestones={openMilestones} />
        ) : null}

        {!current && summary.data ? <DayStrip totals={summary.data.totals} /> : null}

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        <Breadcrumbs crumbs={breadcrumbs} onNavigate={goToLevel} />

        <div style={{ display: 'flex', gap: 10, marginBottom: 16, flexWrap: 'wrap' }}>
          {current ? (
            <button type="button" className="btn" onClick={goBack}>
              ← Back
            </button>
          ) : null}
<<<<<<< HEAD
          {current?.type !== 'milestones' ? (
            <button type="button" className="btn" onClick={explainCurrent}>
              Explain this view
=======
          {/*
            The day's summary and a well's summary both open by themselves, so
            this button is only ever a way back to one after closing it.
          */}
          {!current ? (
            dayExplainOpen ? null : (
              <button type="button" className="btn" onClick={() => setDayExplainOpen(true)}>
                Show AI summary of this view
              </button>
            )
          ) : current.type === 'well' && !wellExplainOpen ? (
            <button type="button" className="btn" onClick={() => setWellExplainOpen(true)}>
              AI summary of well {current.wellId}
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
            </button>
          ) : null}
        </div>

<<<<<<< HEAD
        <ExplainPanel request={explainRequest} onClose={() => setExplainRequest(null)} />

=======
        {/*
          2. The day's AI summary, over everything the front page can see.
          Remounted when Refresh is pressed (the token is part of the key), so
          a refreshed page regenerates it instead of redisplaying the text the
          operator just asked to have redone.
        */}
        {!current && dayExplainOpen ? (
          <ExplainPanel
            key={`day-${reportDate}-${refreshToken}`}
            request={dayExplainRequest}
            onClose={() => setDayExplainOpen(false)}
          />
        ) : null}

        {/*
          2b. Ask the agents: an open question about the selected date, planned
          by the planner agent over the same read-only tools. Keyed by date so
          an answer about one day never sits open beside another day's page.
        */}
        {!current ? <AskAgent key={`ask-${reportDate}-${refreshToken}`} reportDate={reportDate} /> : null}

        {wellExplainRequest && wellExplainOpen ? (
          <ExplainPanel
            key={`well-${wellId}-${reportDate}-${refreshToken}`}
            request={wellExplainRequest}
            onClose={() => setWellExplainOpen(false)}
          />
        ) : null}

        {/* 3. One row per well, each row carrying its own AI summary. */}
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
        {!current ? (
          <SummaryLevel
            resource={wellsForDay}
            activityResource={wellActivity}
            reportDate={reportDate}
            onSelectWell={openWell}
          />
        ) : current.type === 'milestones' ? (
          <MilestonesPage resource={milestones} />
<<<<<<< HEAD
        ) : (
          <WellLevel step={current} reportDate={reportDate} />
        )}
=======
        ) : current.type === 'field-map' ? (
          <FieldMap
            onSelectWell={openWell}
            refreshToken={refreshToken}
            initialView={fieldMapView}
            onViewChange={setFieldMapView}
          />
        ) : (
          <WellLevel step={current} reportDate={reportDate} />
        )}

        {/*
          4. The way into the Oman Field Map. The map itself is its own level
          (above): an independent, read-only page -- if it fails, only it says
          so -- and a well opens through the same openWell as the table.
        */}
        {!current ? <FieldMapEntry onOpen={openFieldMap} /> : null}
>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
      </main>
    </div>
  )
}

function Breadcrumbs({ crumbs, onNavigate }) {
  return (
    <nav className="breadcrumbs" aria-label="Drill-down path">
      {crumbs.map((crumb, index) => {
        const isLast = index === crumbs.length - 1
        return (
          <span key={`${crumb.label}-${index}`} style={{ display: 'inline-flex', alignItems: 'center' }}>
            {index > 0 ? <span className="breadcrumbs__sep">›</span> : null}
            <button
              type="button"
              className={`breadcrumbs__crumb${isLast ? ' breadcrumbs__crumb--current' : ''}`}
              onClick={isLast ? undefined : () => onNavigate(crumb.level)}
              aria-current={isLast ? 'page' : undefined}
            >
              {crumb.label}
            </button>
          </span>
        )
      })}
    </nav>
  )
}

function SummaryLevel({ resource, activityResource, reportDate, onSelectWell }) {
  if (resource.loading) return <LoadingBlock />
  if (resource.error) return <ErrorState error={resource.error} onRetry={resource.reload} />
  if (!resource.data) return null

  // The day's own rollup decides whether this level can render at all; the
  // task-activity call is additive, so its own loading/error state is reported
  // inside the list rather than replacing the whole page with an error.
  return (
    <WellList
      resource={resource}
      activityResource={activityResource}
      reportDate={reportDate}
      onSelectWell={onSelectWell}
    />
  )
}

function WellLevel({ step, reportDate }) {
  const resource = useApiResource(
    (signal) => api.wellDetail(step.wellId, reportDate, signal),
    [reportDate, step.wellId],
  )

  if (resource.loading) return <LoadingBlock message="Loading well detail…" />
  if (resource.error) return <ErrorState error={resource.error} onRetry={resource.reload} />
  if (!resource.data) return null

  return <WellDetail result={resource.data} />
}

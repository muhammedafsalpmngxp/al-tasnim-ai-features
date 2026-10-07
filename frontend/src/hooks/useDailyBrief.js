import { useCallback, useMemo, useState } from 'react'
import api from '../services/api'
import useApiResource from './useApiResource'

/** Today in the browser's local calendar, as YYYY-MM-DD. Never a fixed date. */
export function today() {
  const now = new Date()
  const month = String(now.getMonth() + 1).padStart(2, '0')
  const day = String(now.getDate()).padStart(2, '0')
  return `${now.getFullYear()}-${month}-${day}`
}

/**
 * Owns the report date and the drill-down path.
 *
 * The path is a simple stack, which is what gives the dashboard reliable Back
 * navigation at every level:
 *   summary -> group -> well -> task
 */
export function useDailyBrief() {
  const [reportDate, setReportDate] = useState(today)
  const [path, setPath] = useState([])
  const [refreshToken, setRefreshToken] = useState(0)

  const summary = useApiResource(
    (signal) => api.summary(reportDate, { refresh: refreshToken > 0, signal }),
    [reportDate, refreshToken],
  )

  const health = useApiResource((signal) => api.health(signal), [refreshToken])

  const recentDates = useApiResource((signal) => api.recentDates(30, signal), [refreshToken])

  const selectDate = useCallback((nextDate) => {
    setReportDate(nextDate)
    setPath([])
  }, [])

  const refresh = useCallback(() => setRefreshToken((token) => token + 1), [])

  const drillTo = useCallback((step) => setPath((current) => [...current, step]), [])

  const goBack = useCallback(() => setPath((current) => current.slice(0, -1)), [])

  const goToLevel = useCallback((index) => {
    setPath((current) => (index < 0 ? [] : current.slice(0, index + 1)))
  }, [])

  const current = path.length ? path[path.length - 1] : null

  const breadcrumbs = useMemo(
    () => [{ label: 'Daily Morning Brief', level: -1 }].concat(
      path.map((step, index) => ({ label: step.label, level: index })),
    ),
    [path],
  )

  return {
    reportDate,
    selectDate,
    refresh,
    refreshToken,
    summary,
    health,
    recentDates,
    path,
    current,
    breadcrumbs,
    drillTo,
    goBack,
    goToLevel,
  }
}

export default useDailyBrief

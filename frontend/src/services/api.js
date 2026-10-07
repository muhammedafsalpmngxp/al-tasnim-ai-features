/**
 * The only place the frontend talks to the backend.
 *
 * Presentation components never call fetch directly, and nothing here
 * interprets or recalculates a business value: every count, total and status
 * arrives already classified by the backend.
 */

const BASE_URL = (import.meta.env.VITE_API_BASE_URL || '').replace(/\/$/, '')

export class ApiError extends Error {
  constructor(message, { status, kind } = {}) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    // 'database' when the backend could not reach SQL Server, so the dashboard
    // can tell an outage apart from a day that simply has no records.
    this.kind = kind || (status === 503 ? 'database' : 'api')
  }
}

function buildUrl(path, params = {}) {
  const url = new URL(`${BASE_URL}${path}`, window.location.origin)
  Object.entries(params).forEach(([key, value]) => {
    if (value === undefined || value === null) return
    url.searchParams.set(key, String(value))
  })
  return url.toString().replace(window.location.origin, '')
}

async function request(path, { params, method = 'GET', body, signal } = {}) {
  let response
  try {
    response = await fetch(buildUrl(path, params), {
      method,
      signal,
      headers: body ? { 'Content-Type': 'application/json' } : undefined,
      body: body ? JSON.stringify(body) : undefined,
    })
  } catch (error) {
    if (error.name === 'AbortError') throw error
    throw new ApiError(
      'Cannot reach the Daily Morning Brief API. Check that the backend is running.',
      { kind: 'network' },
    )
  }

  if (!response.ok) {
    let detail = `Request failed with status ${response.status}.`
    try {
      const payload = await response.json()
      if (payload?.detail) detail = payload.detail
    } catch {
      /* keep the default message */
    }
    throw new ApiError(detail, { status: response.status })
  }

  return response.json()
}

export const api = {
  health: (signal) => request('/api/health', { signal }),

<<<<<<< HEAD
=======
  /**
   * The real DYNAMIC_DB bootstrap state: whether the schema has been
   * inspected, fingerprinted and validated, and every capability the report
   * depends on is ready to serve. The backend runs this in the background at
   * its own startup and never fakes progress -- this call reflects exactly
   * what dynamic_db.bootstrap reports, at the moment it is called.
   */
  bootstrapStatus: (signal) => request('/api/bootstrap/status', { signal }),

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  summary: (date, { refresh = false, signal } = {}) =>
    request('/api/daily/summary', { params: { date, refresh: refresh || undefined }, signal }),

  details: (date, signal) => request('/api/daily/details', { params: { date }, signal }),

  /**
   * Wells and tasks behind one summary figure. The filtering is done by the
   * backend against its own classifications -- React never decides which rows
   * belong to a status.
   */
  groupDetails: ({ date, status, wbs, activityCode, uom, refresh = false }, signal) =>
    request('/api/daily/group-details', {
      params: { date, status, wbs, activity_code: activityCode, uom, refresh: refresh || undefined },
      signal,
    }),

  /**
   * Per-well task activity as of the selected date: incomplete and ongoing
   * task counts, whether the well reported anything on the date, and the last
   * date it appeared in the task records. Every figure is computed by the
   * backend; passing `wellId` additionally returns the incomplete tasks
   * behind that one well's counts, for its expandable detail.
   */
  wellActivity: ({ date, wellId, refresh = false }, signal) =>
    request('/api/daily/well-activity', {
      params: { date, well_id: wellId, refresh: refresh || undefined },
      signal,
    }),

  wellDetail: (wellId, date, signal) =>
    request(`/api/daily/well/${wellId}`, { params: { date }, signal }),

  recentDates: (limit, signal) =>
    request('/api/daily/dates', { params: { limit }, signal }),

  /**
   * Live wells approaching (or past) a pegging / FLAF / rig-on / rig-off
   * deadline. Evaluated against today, independent of the selected report date.
   */
  wellMilestones: (windowDays, signal) =>
    request('/api/daily/milestones', { params: { window_days: windowDays }, signal }),

<<<<<<< HEAD
  explain: (payload, signal) =>
    request('/api/daily/explain', { method: 'POST', body: payload, signal }),

=======
  /**
   * The dashboard's AI summaries (day / well / task), produced by the agent
   * graph: fixed tool plan, writer, deterministic verifier. Same request body
   * and the same response fields as the classic endpoint, plus the plan, the
   * trace and the verifier's verdict.
   */
  explain: (payload, signal) =>
    request('/api/agent/brief', { method: 'POST', body: payload, signal }),

  /** The classic single-call summary, kept as a fallback and for comparison. */
  explainClassic: (payload, signal) =>
    request('/api/daily/explain', { method: 'POST', body: payload, signal }),

  /** An open question about one report date, planned by the planner agent. */
  ask: ({ reportDate, question }, signal) =>
    request('/api/agent/ask', {
      method: 'POST',
      body: { report_date: reportDate, question },
      signal,
    }),

  /**
   * The Oman Petroleum Field Map: every geographical field with its
   * approximate position and well counts, all computed by the backend.
   * Fetched once per page load (and on Refresh), never while panning.
   */
  fieldMap: ({ refresh = false } = {}, signal) =>
    request('/api/field-map', { params: { refresh: refresh || undefined }, signal }),

  /**
   * One field's wells, each once, with the status the backend decided.
   * Optionally narrowed by status (live / completed / all) and by category,
   * function, completionType and rig -- each an option `value` the backend
   * itself offered ('none' for nothing recorded). The response carries every
   * filter's options and counts; the browser counts nothing.
   */
  fieldWells: (field, filters = {}, signal) => {
    // The original two-argument form, fieldWells(field, signal), still works.
    if (filters instanceof AbortSignal) {
      signal = filters
      filters = {}
    }
    const { status, category, function: wellFunction, completionType, rig } = filters || {}
    return request(`/api/field-map/${encodeURIComponent(field)}/wells`, {
      params: { status, category, function: wellFunction, completion_type: completionType, rig },
      signal,
    })
  },

>>>>>>> 8e557ed (Update Daily Report Agentic implementation)
  exportUrl: (date) => buildUrl('/api/daily/export', { date }),
}

export default api

/**
 * The session's AI explanations, kept in memory only.
 *
 * WHY THIS EXISTS. The front page now asks for the day's AI summary by itself
 * on first load, and every well row can ask for its own. Without a client-side
 * cache, drilling into a well and pressing Back would ask for the day summary
 * again, and reopening a row's summary would ask for that well again -- each
 * one a fresh request for text the operator has already read on this screen.
 *
 * WHY IT IS DELIBERATELY NOT localStorage OR sessionStorage. The cache must not
 * outlive the page: a reload or closing the project is exactly when an operator
 * expects to be looking at freshly generated text, not yesterday's sentence
 * about a well whose records have moved on since. A module-level Map is emptied
 * by the page itself -- there is nothing to expire, invalidate or clean up, and
 * no way for it to survive a refresh by accident.
 *
 * This is NOT the backend's explanation cache (in the API process's memory,
 * keyed by a hash of the deterministic evidence). That one decides whether a
 * *model call* is needed at all, and lasts until Refresh or until the
 * application stops. This one only decides whether an HTTP request is needed,
 * within one open page.
 */

const cache = new Map()

/** The stable key for one explain request. Field order never affects it. */
export function explainKey(request) {
  if (!request) return null
  return JSON.stringify(
    Object.keys(request)
      .sort()
      .reduce((ordered, field) => {
        if (request[field] !== undefined) ordered[field] = request[field]
        return ordered
      }, {}),
  )
}

export function getCachedExplanation(key) {
  return key ? cache.get(key) : undefined
}

export function setCachedExplanation(key, result) {
  if (key) cache.set(key, result)
}

/**
 * Empties the cache. Called when the operator presses Refresh -- that already
 * purges the backend's own cached explanations for the date, so keeping a
 * client copy of the superseded text would directly contradict it.
 */
export function clearExplainCache() {
  cache.clear()
}

/** Test-only visibility into the cache's size. Never used by the UI. */
export function cachedExplanationCount() {
  return cache.size
}

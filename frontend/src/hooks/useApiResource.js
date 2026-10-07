import { useCallback, useEffect, useRef, useState } from 'react'

/**
 * Loads one API resource and tracks loading / error / empty state.
 *
 * `loader` receives an AbortSignal. A request that is superseded by a newer one
 * is aborted, so switching date quickly can never leave stale data on screen.
 */
export function useApiResource(loader, deps, { enabled = true } = {}) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)
  const [loading, setLoading] = useState(Boolean(enabled))
  const [reloadToken, setReloadToken] = useState(0)
  const loaderRef = useRef(loader)
  loaderRef.current = loader

  useEffect(() => {
    if (!enabled) {
      setData(null)
      setError(null)
      setLoading(false)
      return undefined
    }

    const controller = new AbortController()
    let active = true

    setLoading(true)
    setError(null)

    loaderRef
      .current(controller.signal)
      .then((result) => {
        if (!active) return
        setData(result)
        setLoading(false)
      })
      .catch((caught) => {
        if (!active || caught.name === 'AbortError') return
        setError(caught)
        setData(null)
        setLoading(false)
      })

    return () => {
      active = false
      controller.abort()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, enabled, reloadToken])

  const reload = useCallback(() => setReloadToken((token) => token + 1), [])

  return { data, error, loading, reload }
}

export default useApiResource

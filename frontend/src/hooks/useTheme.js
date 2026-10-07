import { useCallback, useEffect, useState } from 'react'

const STORAGE_KEY = 'daily-brief-theme'

/** The dashboard's default look, unchanged from before the toggle existed. */
const DEFAULT_THEME = 'dark'

function readStoredTheme() {
  try {
    const stored = window.localStorage.getItem(STORAGE_KEY)
    return stored === 'light' || stored === 'dark' ? stored : null
  } catch {
    // Private browsing / storage disabled: fall back to the default silently.
    return null
  }
}

function systemPrefersLight() {
  try {
    return window.matchMedia('(prefers-color-scheme: light)').matches
  } catch {
    return false
  }
}

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme
}

/**
 * Manual dark/light toggle, persisted per browser via localStorage.
 *
 * An explicit choice always wins and is remembered. With no stored choice yet,
 * the OS preference decides the first render; the dashboard's own default
 * (dark) is the fallback beyond that. This is a pure presentation setting --
 * it never touches any daily task data or calculation.
 */
export function useTheme() {
  const [theme, setTheme] = useState(() => readStoredTheme() || (systemPrefersLight() ? 'light' : DEFAULT_THEME))

  useEffect(() => {
    applyTheme(theme)
  }, [theme])

  const setThemeAndPersist = useCallback((next) => {
    setTheme(next)
    try {
      window.localStorage.setItem(STORAGE_KEY, next)
    } catch {
      // Nothing to do if storage is unavailable -- the toggle still works
      // for the rest of the session via component state.
    }
  }, [])

  const toggle = useCallback(() => {
    setThemeAndPersist(theme === 'dark' ? 'light' : 'dark')
  }, [theme, setThemeAndPersist])

  return { theme, toggle, setTheme: setThemeAndPersist }
}

export default useTheme

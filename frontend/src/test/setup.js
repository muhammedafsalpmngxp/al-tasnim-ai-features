/**
 * Component-test setup.
 *
 * Adds the DOM matchers and makes sure no test can reach the network by
 * accident: every test stubs `services/api` explicitly, so a call that slips
 * through would be a bug in the test rather than a silent HTTP request.
 */
import '@testing-library/jest-dom/vitest'
import { afterEach, vi } from 'vitest'
import { cleanup } from '@testing-library/react'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

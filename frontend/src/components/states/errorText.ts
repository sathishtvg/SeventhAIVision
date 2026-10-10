/**
 * What to tell somebody when a request did not work, in words that say which
 * kind of not working it was: the server was not reached, it took too long,
 * it refused, or it broke. The server's own sentence is used when it sent one
 * meant for a person (a 4xx with a `detail`); a 5xx's detail is the server
 * talking to itself and is not shown.
 */
export function errorText(err: unknown): string {
  const e = err as {
    response?: { status?: number; data?: { detail?: unknown } }
    code?: string
    message?: string
  } | null | undefined
  if (!e) return 'Something went wrong.'

  const status = e.response?.status
  if (status === undefined) {
    if (e.code === 'ECONNABORTED' || e.code === 'ETIMEDOUT' || /timeout/i.test(e.message ?? '')) {
      return 'The server took too long to answer.'
    }
    if (e.code === 'ERR_NETWORK' || e.message === 'Network Error') {
      return 'The server could not be reached. Check the connection.'
    }
    if (e.code === 'ERR_CANCELED') return 'The request was cancelled.'
    return e.message || 'Something went wrong.'
  }

  if (status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  if (status >= 500) return 'The server had a problem answering. It has been recorded; try again in a moment.'

  const detail = e.response?.data?.detail
  if (typeof detail === 'string' && detail.trim()) return detail
  if (Array.isArray(detail)) {
    const said = detail.map((x: { msg?: string }) => x?.msg ?? String(x)).filter(Boolean).join('; ')
    if (said) return said
  }
  if (status === 401) return 'Your session has ended. Sign in again.'
  if (status === 403) return 'You do not have permission to see this.'
  if (status === 404) return 'This is no longer there.'
  return 'The request was not accepted.'
}

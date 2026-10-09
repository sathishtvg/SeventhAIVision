/**
 * The server's reason for a refusal, in its own words: it usually says what to
 * do instead. A refusal with no words of its own — no signal, a timeout — is
 * said plainly rather than shown as a status code.
 */
export function apiErrorText(err: unknown): string {
  const e = err as { response?: { status?: number; data?: { detail?: unknown } }; message?: string }
  if (e?.response?.status === 429) return 'Too many requests in a short time. Wait a minute and try again.'
  const d = e?.response?.data?.detail
  if (typeof d === 'string') return d
  if (d && typeof d === 'object' && !Array.isArray(d) && typeof (d as { message?: unknown }).message === 'string') {
    return (d as { message: string }).message
  }
  if (Array.isArray(d)) return d.map((x: { msg?: string }) => x.msg ?? String(x)).join('; ')
  if (!e?.response) return 'Could not reach the server. Check the connection and try again.'
  return e?.message ?? 'Something went wrong.'
}

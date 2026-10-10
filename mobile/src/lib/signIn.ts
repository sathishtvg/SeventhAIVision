/**
 * The rules of signing in, apart from the screen so they can be tested with
 * nothing mounted: what a typed code is reduced to, and the words for each way
 * the server can refuse.
 *
 * An account with two-factor on is not signed in by its password: the server
 * answers a correct password with a challenge, good for five minutes, and
 * signs in only when the six digits from the authenticator are sent with it.
 */

export const CODE_LENGTH = 6

/** What was typed, as the six digits the server reads: spaces and dashes go. */
export function digitsOnly(typed: string): string {
  return typed.replace(/\D/g, '').slice(0, CODE_LENGTH)
}

export const isWholeCode = (code: string): boolean => code.length === CODE_LENGTH

interface Refused { response?: { status?: number; data?: { detail?: unknown } } }

/** Why the password step was refused, in words that say what to do next. */
export function loginRefusal(err: unknown): { title: string; message: string } {
  const res = (err as Refused)?.response
  const detail = res?.data?.detail
  if (res === undefined) {
    return { title: 'Cannot reach the server', message: 'Check the server URL and your connection.' }
  }
  // A locked account: the server says until when. Retyping the password is not the answer.
  if (res.status === 403 && typeof detail === 'string') return { title: 'Account locked', message: detail }
  if (res.status === 429) {
    return { title: 'Too many attempts', message: 'Wait a minute and try again.' }
  }
  if (detail && typeof detail === 'object' && (detail as { code?: string }).code === '2fa_setup_required') {
    return { title: 'Two-factor is required',
             message: 'This account must set up two-factor authentication first. Ask your administrator.' }
  }
  return { title: 'Login failed', message: 'Check your organisation, email and password.' }
}

/**
 * Why a code was refused. `expired` is true when the five minutes ran out: the
 * challenge cannot be renewed, so the password is asked for again.
 */
export function codeRefusal(err: unknown): { title: string; message: string; expired: boolean } {
  const status = (err as Refused)?.response?.status
  if (status === 401) {
    return { title: 'Sign in again', message: 'That took longer than five minutes.', expired: true }
  }
  if (status === 429) {
    return { title: 'Too many attempts', message: 'Wait a minute and try again.', expired: false }
  }
  if (status === undefined) {
    return { title: 'Cannot reach the server', message: 'Check your connection and try again.', expired: false }
  }
  return { title: 'Code not accepted',
           message: 'Codes change every 30 seconds. Enter the one showing now.', expired: false }
}

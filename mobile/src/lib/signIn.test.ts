/**
 * Signing in: the code, and the words for a refusal.
 *
 * WHY THIS MATTERS
 *   The platform owner's account has two-factor on, and neither the web nor
 *   the phone had anywhere to enter the code: a correct password was answered
 *   with "login failed". And an account locked after ten wrong passwords was
 *   told to "check your credentials", which sent its owner back to type the
 *   same password at a door that would not open for half an hour.
 */
import { CODE_LENGTH, codeRefusal, digitsOnly, isWholeCode, loginRefusal } from './signIn'

const refused = (status: number, detail?: unknown) => ({ response: { status, data: { detail } } })

test('a typed code is reduced to its six digits', () => {
  expect(digitsOnly('123456')).toBe('123456')
  expect(digitsOnly('123 456')).toBe('123456')
  expect(digitsOnly('12-34-56')).toBe('123456')
  expect(digitsOnly('1234567890')).toBe('123456')
  expect(digitsOnly('abc')).toBe('')
  expect(CODE_LENGTH).toBe(6)
})

test('a code is whole only at six digits', () => {
  expect(isWholeCode('12345')).toBe(false)
  expect(isWholeCode('123456')).toBe(true)
})

test('a wrong password is said to be the credentials', () => {
  expect(loginRefusal(refused(401, 'Invalid credentials'))).toEqual({
    title: 'Login failed', message: 'Check your organisation, email and password.' })
})

test('a locked account is told so in the server\'s words, not sent back to its password', () => {
  const said = loginRefusal(refused(403, 'Account locked. Try again after 08:18 UTC or contact your administrator.'))
  expect(said.title).toBe('Account locked')
  expect(said.message).toContain('Try again after 08:18 UTC')
  expect(said.message).not.toMatch(/password/i)
})

test('too many attempts, a missing two-factor set-up and no connection each say what they are', () => {
  expect(loginRefusal(refused(429)).title).toBe('Too many attempts')
  expect(loginRefusal(refused(403, { code: '2fa_setup_required' })).title).toBe('Two-factor is required')
  expect(loginRefusal(new Error('Network Error')).title).toBe('Cannot reach the server')
})

test('a refused code says to enter the one showing now, and keeps the person on the code', () => {
  const said = codeRefusal(refused(400, 'Invalid TOTP code'))
  expect(said.title).toBe('Code not accepted')
  expect(said.expired).toBe(false)
})

test('a challenge that has run out sends the person back to the password', () => {
  expect(codeRefusal(refused(401, 'Invalid or expired challenge token'))).toEqual({
    title: 'Sign in again', message: 'That took longer than five minutes.', expired: true })
})

test('too many codes and no connection do not end the challenge', () => {
  expect(codeRefusal(refused(429)).expired).toBe(false)
  expect(codeRefusal(new Error('Network Error'))).toMatchObject({ title: 'Cannot reach the server', expired: false })
})

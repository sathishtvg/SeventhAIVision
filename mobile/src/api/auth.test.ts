/**
 * Signing in: what goes on the wire, and what is made of the answer.
 *
 * WHY THIS MATTERS
 *   An account with two-factor on is answered with a challenge, not with
 *   tokens. The phone read a token out of that answer anyway and failed, so
 *   such an account could not sign in at all. The code is then sent as
 *   `totp_code` with `challenge_token`; under any other name the server
 *   refuses the request whole.
 */
import { signIn, verifyTwoFactor } from './auth'
import { apiClient } from './client'

jest.mock('./client', () => ({ apiClient: { get: jest.fn(), post: jest.fn() } }))

const mockPost = apiClient.post as jest.Mock

beforeEach(() => mockPost.mockReset())

test('a password that signs in gives the tokens', async () => {
  mockPost.mockResolvedValue({ data: { access_token: 'a1', refresh_token: 'r1', token_type: 'bearer' } })
  await expect(signIn('ops@demo.local', 'pw', 'demo')).resolves.toEqual({
    kind: 'tokens', accessToken: 'a1', refreshToken: 'r1' })
  expect(mockPost).toHaveBeenCalledWith('/api/v1/auth/login', {
    email: 'ops@demo.local', password: 'pw', tenant_slug: 'demo' })
})

test('a password on a two-factor account gives the challenge, and no tokens', async () => {
  mockPost.mockResolvedValue({ data: { requires_2fa: true, challenge_token: 'c1' } })
  await expect(signIn('owner@example.com', 'pw', 'seventhaivision')).resolves.toEqual({
    kind: 'code', challengeToken: 'c1' })
})

test('the code is sent with the challenge under the names the server reads', async () => {
  mockPost.mockResolvedValue({ data: { access_token: 'a2', refresh_token: 'r2' } })
  await expect(verifyTwoFactor('c1', '123456')).resolves.toEqual({
    kind: 'tokens', accessToken: 'a2', refreshToken: 'r2' })
  expect(mockPost).toHaveBeenCalledWith('/api/v1/auth/2fa-verify', { challenge_token: 'c1', totp_code: '123456' })
})

test('a refusal is passed on as the server gave it', async () => {
  const refused = Object.assign(new Error('x'), { response: { status: 400, data: { detail: 'Invalid TOTP code' } } })
  mockPost.mockRejectedValue(refused)
  await expect(verifyTwoFactor('c1', '000000')).rejects.toBe(refused)
})

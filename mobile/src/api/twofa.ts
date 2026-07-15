import { apiClient } from './client'

export interface TwoFAStatus {
  enabled: boolean
}

export interface TwoFASetup {
  secret: string
  qr_code_uri: string
  manual_entry_key: string
  instructions: string
}

export const get2FAStatus = () =>
  apiClient.get<TwoFAStatus>('/api/v1/2fa/status').then((r) => r.data)

export const setup2FA = () =>
  apiClient.get<TwoFASetup>('/api/v1/2fa/setup').then((r) => r.data)

export const enable2FA = (totp_code: string) =>
  apiClient.post('/api/v1/2fa/enable', { totp_code }).then((r) => r.data)

export const disable2FA = (totp_code: string) =>
  apiClient.delete('/api/v1/2fa', { data: { totp_code } }).then((r) => r.data)

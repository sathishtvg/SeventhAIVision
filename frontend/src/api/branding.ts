import { apiClient } from './client'

export interface TenantBranding {
  name: string
  branding: Record<string, string>
  timezone: string
}

export const getBranding = (): Promise<TenantBranding> =>
  apiClient.get<TenantBranding>('/api/v1/branding').then((r) => r.data)

export const updateBranding = (branding: Record<string, string>): Promise<{ branding: Record<string, string> }> =>
  apiClient.put('/api/v1/branding', { branding }).then((r) => r.data)

/** Well-known branding keys written and read by the frontend */
export const BRANDING_KEYS = {
  logoUrl: 'logo_url',
  primaryColor: 'primary_color',
  primaryDark: 'primary_dark',
  companyName: 'company_name',
} as const

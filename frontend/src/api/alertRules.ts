import { apiClient } from './client'

/** One module×trigger rule as the server resolves it: the tenant's override if
 *  one exists, otherwise the shipped default. `is_overridden` is what lets the
 *  editor show "changed from default" without a second request. */
export interface EffectiveAlertRule {
  module_type: string
  trigger_key: string
  severity: string
  create_incident: boolean
  incident_severity: string | null
  is_enabled: boolean
  is_overridden: boolean
  default_severity: string
  default_create_incident: boolean
  default_incident_severity: string | null
}

/** Served rather than hardcoded so a new AI module's triggers show up in the
 *  editor without a matching TypeScript edit. */
export interface AlertRuleCatalogue {
  modules: Record<string, string[]>
  severities: string[]
}

export interface AlertRuleUpsert {
  severity: string
  create_incident: boolean
  incident_severity?: string | null
  is_enabled?: boolean
}

export const listAlertRules = () =>
  apiClient.get<EffectiveAlertRule[]>('/api/v1/alert-rules').then((r) => r.data)

export const getAlertRuleCatalogue = () =>
  apiClient.get<AlertRuleCatalogue>('/api/v1/alert-rules/catalogue').then((r) => r.data)

export const upsertAlertRule = (
  moduleType: string,
  triggerKey: string,
  body: AlertRuleUpsert,
) =>
  apiClient
    .put(`/api/v1/alert-rules/${moduleType}/${triggerKey}`, body)
    .then((r) => r.data)

/** Drop the tenant override so the rule falls back to the shipped default. */
export const resetAlertRule = (moduleType: string, triggerKey: string) =>
  apiClient
    .delete(`/api/v1/alert-rules/${moduleType}/${triggerKey}`)
    .then((r) => r.data)

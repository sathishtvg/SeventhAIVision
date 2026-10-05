/**
 * The intelligence screens' non-component helpers: the words for each code,
 * the colours, time formatting and the live-update hook. Kept apart from
 * intelUi.tsx so that file exports components only (fast refresh).
 */
import { useEffect } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useWsStore } from '@/store/websocket'
import type {
  DecisionAction, DecisionBasis, DecisionState, DecisionStatus, IncidentState, RiskLevel, SourceType,
} from '@/api/securityIntelligence'

/** The same five colours as the drone screens, so HIGH looks like HIGH everywhere. */
export const RISK_COLOR: Record<RiskLevel, string> = {
  INFO: '#8ea0b8', LOW: '#00c48c', MEDIUM: '#ffb020', HIGH: '#ff7a45', CRITICAL: '#ff3b5c',
}
/** What the layer says is one colour; what a person decided is another. Always. */
export const AI_COLOR = '#7c8cff'
export const HUMAN_COLOR = '#00c48c'

export const pretty = (s: string | null | undefined) =>
  (s ?? '').replace(/_/g, ' ').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())

export const DECISION_LABEL: Record<DecisionAction, string> = {
  MONITOR: 'Monitor', VERIFY: 'Verify', VIEW_CAMERA: 'View CCTV', VERIFY_WITH_DRONE: 'Verify with drone',
  DISPATCH_GUARD: 'Dispatch guard', ESCALATE: 'Escalate', INVESTIGATE: 'Investigate', CONTACT_SITE: 'Contact site',
  CREATE_INCIDENT: 'Create incident', ACKNOWLEDGE: 'Acknowledge', CONFIRM_INCIDENT: 'Confirm incident',
  REQUEST_ASSISTANCE: 'Request assistance', FALSE_POSITIVE: 'False positive', RESOLVE: 'Resolve',
}

export const SOURCE_LABEL: Record<SourceType, string> = {
  CCTV_AI: 'CCTV', DRONE_PATROL: 'Drone', VIRTUAL_PATROL: 'Virtual patrol', LPR: 'Number plate',
  FACE_RECOGNITION: 'Face recognition', ACCESS_CONTROL: 'Access control', ALARM: 'Alarm', GUARD: 'Guard',
  SENSOR: 'Sensor', SYSTEM: 'System', OTHER: 'Other',
}

export const STATUS_LABEL: Record<DecisionStatus, string> = {
  AWAITING: 'Awaiting a decision', ACKNOWLEDGED: 'Acknowledged', IN_HAND: 'In hand',
  PENDING_APPROVAL: 'Waiting for approval', ASSISTANCE_REQUESTED: 'Assistance requested',
  RESOLVED: 'Resolved', FALSE_POSITIVE: 'False positive',
}

export const BASIS_LABEL: Record<DecisionBasis, string> = {
  FOLLOWED: 'Followed the suggestion', OVERRIDE: 'Override', CLOSING: 'Closed the situation',
  INDEPENDENT: 'Own decision',
}

export const STATE_LABEL: Record<DecisionState, string> = {
  EFFECTIVE: 'In effect', PENDING_APPROVAL: 'Waiting for approval', APPROVED: 'Approved', REJECTED: 'Rejected',
}

export const INCIDENT_LABEL: Record<IncidentState, string> = {
  NONE: 'No incident — an AI event only',
  PRELIMINARY: 'Preliminary incident — opened by the platform, not confirmed by a person',
  CONFIRMED: 'Incident confirmed by a person',
}

export const ROLE_LABEL: Record<number, string> = {
  1: 'Platform owner', 2: 'Admin', 3: 'Supervisor', 4: 'Operator', 5: 'Guard', 6: 'Viewer', 7: 'Client', 8: 'Manager',
}

/** What each step of an action is, in words an officer would use. */
export const STEP_LABEL: Record<string, string> = {
  ALERT_ACKNOWLEDGE: 'Alerts acknowledged', ALERT_FALSE_POSITIVE: 'Alert marked false',
  ALERT_DISMISS: 'Alerts closed', ALERT_ASSIGN: 'Alerts assigned', INCIDENT_CREATE: 'Incident opened',
  INCIDENT_CONFIRM: 'Incident confirmed', INCIDENT_DISPATCH: 'Guard dispatched',
  INCIDENT_ASSIGN: 'Incident assigned', INCIDENT_RESOLVE: 'Incident resolved', NONE: 'Recorded',
}

export function fmt(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' })
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return '—'
  return new Date(iso).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

/** A confidence as a percentage, or a dash when the source gave none. */
export const pct = (v: number | null | undefined) => (v == null ? '—' : `${Math.round(v * 100)}%`)

/** One per press: sent again with a retry, so that one press is one decision. */
export function newClientRef(): string {
  if (typeof crypto !== 'undefined' && 'randomUUID' in crypto) return crypto.randomUUID()
  return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0
    return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16)
  })
}

/** Refresh the given queries when the server announces an intelligence change. */
export function useIntelRealtime(keys: unknown[][], filter?: (type: string, payload: Record<string, unknown>) => boolean) {
  const last = useWsStore((s) => s.lastMessage)
  const qc = useQueryClient()
  useEffect(() => {
    if (!last) return
    try {
      const msg = JSON.parse(last) as { event_type?: string; payload?: Record<string, unknown> }
      const type = msg.event_type ?? ''
      if (!type.startsWith('intel_')) return
      if (filter && !filter(type, msg.payload ?? {})) return
      keys.forEach((k) => qc.invalidateQueries({ queryKey: k }))
    } catch {
      /* not JSON: not ours */
    }
    // keys are literals at each call site
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [last])
}

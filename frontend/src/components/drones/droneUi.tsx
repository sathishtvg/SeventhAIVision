/**
 * Shared components of the drone screens: status and risk chips, the licence
 * banner, and a map base on the same tiles as the rest of the app. The
 * non-component helpers they use live in droneFormat.ts.
 */
import { useEffect, type ReactNode } from 'react'
import { Alert, Box, Chip, LinearProgress, Tooltip, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { MapContainer, TileLayer, useMap } from 'react-leaflet'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import { getEntitlement } from '@/api/drones'
import type { ComponentState, DroneStatus, EventStatus, RiskLevel, SessionStatus } from '@/api/drones'
import { DEFAULT_CENTER, RISK_COLOR, TILE_ATTRIBUTION, TILE_URL, pretty } from './droneFormat'

type ChipColor = 'default' | 'success' | 'error' | 'warning' | 'info' | 'primary' | 'secondary'

const DRONE_COLOR: Record<DroneStatus, ChipColor> = {
  READY: 'success', STANDBY: 'success', CHARGING: 'info', PREPARING: 'info', MISSION_ACTIVE: 'primary',
  RETURNING: 'primary', WARNING: 'warning', COMMUNICATION_LOST: 'error', CRITICAL: 'error',
  OFFLINE: 'default', MAINTENANCE: 'warning', DISABLED: 'default',
}
const SESSION_COLOR: Record<SessionStatus, ChipColor> = {
  SCHEDULED: 'default', PRECHECK: 'info', READY: 'info', LAUNCHING: 'primary', ACTIVE: 'primary',
  PAUSED: 'warning', EVENT_DETECTED: 'warning', RETURNING: 'primary', COMPLETED: 'success',
  FAILED: 'error', ABORTED: 'warning', CANCELLED: 'default', BLOCKED: 'error', MISSED: 'error',
}
export function DroneStatusChip({ status }: { status: DroneStatus }) {
  return <Chip size="small" color={DRONE_COLOR[status] ?? 'default'} label={pretty(status)} />
}

export function SessionStatusChip({ status }: { status: SessionStatus }) {
  return <Chip size="small" color={SESSION_COLOR[status] ?? 'default'} label={pretty(status)} />
}

const EVENT_COLOR: Record<EventStatus, ChipColor> = {
  NEW: 'error', ACKNOWLEDGED: 'warning', INVESTIGATING: 'info', ESCALATED: 'error', RESOLVED: 'success',
  FALSE_POSITIVE: 'default',
}

export function EventStatusChip({ status }: { status: EventStatus }) {
  return <Chip size="small" color={EVENT_COLOR[status] ?? 'default'} label={pretty(status)} />
}

export function RiskChip({ level, score }: { level: RiskLevel; score?: number | null }) {
  return (
    <Chip size="small" label={score != null ? `${level} · ${score}` : level}
          sx={{ bgcolor: `${RISK_COLOR[level]}22`, color: RISK_COLOR[level], border: `1px solid ${RISK_COLOR[level]}66`,
                fontWeight: 700 }} />
  )
}

/** AI confidence, always labelled — never shown as if it were the risk. */
export function ConfidenceText({ value }: { value: number | null | undefined }) {
  if (value == null) return <Typography variant="body2" color="text.secondary">—</Typography>
  return <Typography variant="body2">AI confidence {Math.round(value * 100)}%</Typography>
}

const DOT: Record<ComponentState, string> = { OK: '#00c48c', WARNING: '#ffb020', FAULT: '#ff3b5c', UNKNOWN: '#6b7a90' }

export function ComponentDot({ label, state }: { label: string; state: ComponentState }) {
  return (
    <Tooltip title={`${label}: ${pretty(state)}`}>
      <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5, mr: 1 }}>
        <Box component="span" sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: DOT[state] ?? DOT.UNKNOWN }} />
        <Typography component="span" variant="caption" color="text.secondary">{label}</Typography>
      </Box>
    </Tooltip>
  )
}

export function BatteryBar({ pct }: { pct: number | null | undefined }) {
  if (pct == null) return <Typography variant="body2" color="text.secondary">—</Typography>
  const color = pct < 25 ? 'error' : pct < 50 ? 'warning' : 'success'
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, minWidth: 90 }}>
      <LinearProgress variant="determinate" value={Math.max(0, Math.min(100, pct))} color={color}
                      sx={{ flex: 1, height: 6, borderRadius: 3 }} />
      <Typography variant="caption">{Math.round(pct)}%</Typography>
    </Box>
  )
}

/** Says why the module cannot be used, when it cannot — reads stay available. */
export function LicenceBanner() {
  const { data } = useQuery({ queryKey: ['drone-entitlement'], queryFn: getEntitlement, staleTime: 60_000 })
  if (!data || data.licensed) return null
  return (
    <Alert severity="warning" sx={{ mb: 2 }}>
      {data.reason ?? 'Drone Patrol is not licensed.'} History stays readable; creating, changing and
      starting flights is disabled.
    </Alert>
  )
}

/** Fits the map to the given points once they are known, and again when they change. */
export function FitTo({ points, padding = 40 }: { points: [number, number][]; padding?: number }) {
  const map = useMap()
  const key = points.map((p) => p.join(',')).join(';')
  useEffect(() => {
    if (!points.length) return
    if (points.length === 1) map.setView(points[0], Math.max(map.getZoom(), 17))
    else map.fitBounds(L.latLngBounds(points), { padding: [padding, padding], maxZoom: 18 })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key])
  return null
}

export function DroneMap({ children, height = 460, center }: { children: ReactNode; height?: number | string
                                                              center?: [number, number] }) {
  return (
    <Box sx={{ height, borderRadius: 2, overflow: 'hidden', border: '1px solid rgba(255,255,255,0.12)' }}>
      <MapContainer center={center ?? DEFAULT_CENTER} zoom={16} style={{ height: '100%', width: '100%' }}
                    scrollWheelZoom>
        <TileLayer url={TILE_URL} attribution={TILE_ATTRIBUTION} />
        {children}
      </MapContainer>
    </Box>
  )
}

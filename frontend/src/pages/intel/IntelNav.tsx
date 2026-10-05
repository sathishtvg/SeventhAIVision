import { useLocation, useNavigate } from 'react-router-dom'
import { Alert, Tab, Tabs } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { usePermission } from '@/hooks/usePermission'
import { getIntelStatus } from '@/api/securityIntelligence'

const LINKS = [
  { path: '/situations', label: 'Situations', perm: 'intel:read' },
  { path: '/situation-decisions', label: 'Decisions', perm: 'intel:read' },
  { path: '/intelligence-setup', label: 'Setup', perm: 'intel:read' },
]

/** The intelligence screens' own tabs, under the page header of each. */
export function IntelNav() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const can: Record<string, boolean> = { 'intel:read': usePermission('intel:read') }
  const allowed = LINKS.filter((l) => can[l.perm])
  const current = [...allowed].sort((a, b) => b.path.length - a.path.length)
    .find((l) => pathname === l.path || pathname.startsWith(`${l.path}/`))
  return (
    <Tabs value={current?.path ?? false} onChange={(_, v) => navigate(v)} sx={{ mb: 2 }} variant="scrollable">
      {allowed.map((l) => <Tab key={l.path} value={l.path} label={l.label} />)}
    </Tabs>
  )
}

/**
 * Says so when nothing new is being read or assessed — the layer switched off
 * for this organisation, or its runner not running. What is on the screen
 * stays readable; it is just not being added to, and an officer should know.
 */
export function IntelStatusBanner() {
  const { data } = useQuery({ queryKey: ['intel-status'], queryFn: getIntelStatus, staleTime: 30_000,
                              refetchInterval: 60_000 })
  if (!data) return null
  if (!data.enabled) {
    return (
      <Alert severity="info" sx={{ mb: 2 }}>
        Security intelligence is switched off for this organisation, so nothing is being read or assessed.
        An administrator can switch it on under Setup. Alerts, incidents and video work as they always have.
      </Alert>
    )
  }
  if (data.runner.state === 'running') return null
  const words = {
    degraded: 'The intelligence runner is running but its last pass had a failure. Some situations may be late.',
    stopped: 'The intelligence runner is not running. Nothing new is being read or assessed; what is shown is as '
      + 'it was when it stopped.',
    unknown: 'The intelligence runner could not be asked whether it is running, so what is shown may not be current.',
  }[data.runner.state]
  return <Alert severity="warning" sx={{ mb: 2 }}>{words} Alerts, incidents and video are not affected.</Alert>
}

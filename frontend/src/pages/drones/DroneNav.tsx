import { useLocation, useNavigate } from 'react-router-dom'
import { Tab, Tabs } from '@mui/material'
import { usePermission } from '@/hooks/usePermission'

const LINKS = [
  { path: '/drones', label: 'Dashboard', perm: 'drone:read' },
  { path: '/drone-missions', label: 'Missions', perm: 'drone:read' },
  { path: '/drone-patrols', label: 'Patrols & reports', perm: 'drone:read' },
  { path: '/drone-events', label: 'Events', perm: 'drone:event:read' },
  { path: '/drone-zones', label: 'Zones & profiles', perm: 'drone:read' },
]

/** The drone module's own tabs, under the page header of every drone screen. */
export function DroneNav() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const read = usePermission('drone:read')
  const events = usePermission('drone:event:read')
  const allowed = LINKS.filter((l) => (l.perm === 'drone:event:read' ? events : read))
  const current = [...allowed].sort((a, b) => b.path.length - a.path.length)
    .find((l) => pathname === l.path || pathname.startsWith(`${l.path}/`))
  return (
    <Tabs value={current?.path ?? false} onChange={(_, v) => navigate(v)} sx={{ mb: 2 }} variant="scrollable">
      {allowed.map((l) => <Tab key={l.path} value={l.path} label={l.label} />)}
    </Tabs>
  )
}

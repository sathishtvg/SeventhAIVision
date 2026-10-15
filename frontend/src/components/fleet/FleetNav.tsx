/**
 * The fleet's own pages, side by side: the dashboard the GPS page has always
 * been, the vehicles, their groups and their custom fields. One address each,
 * so each can be linked to and comes back where it was.
 */
import { Tab, Tabs } from '@mui/material'
import { Link, useLocation } from 'react-router-dom'

const PAGES = [
  { path: '/gps', label: 'Dashboard' },
  { path: '/gps/map', label: 'Live map' },
  { path: '/gps/vehicles', label: 'Vehicles' },
  { path: '/gps/groups', label: 'Groups' },
  { path: '/gps/fields', label: 'Custom fields' },
  { path: '/gps/providers', label: 'Providers' },
] as const

export function FleetNav() {
  const { pathname } = useLocation()
  // A vehicle's own page is under Vehicles.
  const current = [...PAGES].reverse().find((p) => pathname === p.path || (p.path !== '/gps' && pathname.startsWith(`${p.path}/`)))
  return (
    <Tabs value={current?.path ?? '/gps'} variant="scrollable" allowScrollButtonsMobile aria-label="Fleet pages" sx={{ mb: 2 }}>
      {PAGES.map((p) => <Tab key={p.path} value={p.path} label={p.label} component={Link} to={p.path} />)}
    </Tabs>
  )
}

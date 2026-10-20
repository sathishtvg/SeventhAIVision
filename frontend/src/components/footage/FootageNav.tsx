/**
 * Footage Review's own tabs: the videos, and searching them by description.
 */
import { Tab, Tabs } from '@mui/material'
import { useLocation, useNavigate } from 'react-router-dom'

const LINKS = [
  { path: '/footage', label: 'Videos' },
  { path: '/footage/search', label: 'Search by description' },
]

export function FootageNav() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const current = pathname === '/footage/search' ? '/footage/search' : '/footage'
  return (
    <Tabs value={current} onChange={(_, v) => navigate(v)} sx={{ mb: 2 }} variant="scrollable">
      {LINKS.map((l) => <Tab key={l.path} value={l.path} label={l.label} />)}
    </Tabs>
  )
}

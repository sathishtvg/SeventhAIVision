import { useLocation, useNavigate } from 'react-router-dom'
import { Tab, Tabs } from '@mui/material'

const LINKS = [
  { path: '/investigate', label: 'Search' },
  { path: '/investigations', label: 'Investigations' },
]

/** The investigation screens' own tabs, under the page header of each. Both
 *  need the same permission as the page they are on. */
export function InvestigationNav() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const current = [...LINKS].sort((a, b) => b.path.length - a.path.length)
    .find((l) => pathname === l.path || pathname.startsWith(`${l.path}/`))
  return (
    <Tabs value={current?.path ?? false} onChange={(_, v) => navigate(v)} sx={{ mb: 2 }} variant="scrollable">
      {LINKS.map((l) => <Tab key={l.path} value={l.path} label={l.label} />)}
    </Tabs>
  )
}

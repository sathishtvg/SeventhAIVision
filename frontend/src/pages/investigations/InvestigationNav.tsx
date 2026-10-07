import { useLocation, useNavigate } from 'react-router-dom'
import { Tab, Tabs } from '@mui/material'
import { usePermission } from '@/hooks/usePermission'

const LINKS = [
  { path: '/investigate', label: 'Search', perm: 'investigation:read' },
  { path: '/investigations', label: 'Investigations', perm: 'investigation:read' },
  { path: '/evidence-packages', label: 'Evidence packages', perm: 'evidence:package:read' },
  { path: '/evidence-holds', label: 'Holds', perm: 'evidence:package:read' },
]

/** The tabs the investigation and evidence screens share, under the page
 *  header of each: looking, keeping what was found, and putting the evidence
 *  of it together. Each tab is shown to someone who may open it. */
export function InvestigationNav() {
  const navigate = useNavigate()
  const { pathname } = useLocation()
  const can: Record<string, boolean> = {
    'investigation:read': usePermission('investigation:read'),
    'evidence:package:read': usePermission('evidence:package:read'),
  }
  const allowed = LINKS.filter((l) => can[l.perm])
  const current = [...allowed].sort((a, b) => b.path.length - a.path.length)
    .find((l) => pathname === l.path || pathname.startsWith(`${l.path}/`))
  return (
    <Tabs value={current?.path ?? false} onChange={(_, v) => navigate(v)} sx={{ mb: 2 }} variant="scrollable">
      {allowed.map((l) => <Tab key={l.path} value={l.path} label={l.label} />)}
    </Tabs>
  )
}

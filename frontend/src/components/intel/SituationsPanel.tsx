/**
 * The one thing the intelligence layer adds to the Command Centre: a strip of
 * the open situations, highest risk first, each saying where it stands with
 * the people responsible for it. A card opens the situation.
 *
 * It renders nothing at all — not a heading, not a gap — when the caller may
 * not read the layer or the organisation has not switched it on, so for
 * everyone else the Command Centre is exactly as it was.
 */
import { useNavigate } from 'react-router-dom'
import { Box, Button, Paper, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { usePermission } from '@/hooks/usePermission'
import { getIntelStatus, listSituations } from '@/api/securityIntelligence'
import { AiMark, DecisionStatusChip, RiskChip } from './intelUi'
import { RISK_COLOR, useIntelRealtime } from './intelFormat'

export function SituationsPanel() {
  const navigate = useNavigate()
  const canRead = usePermission('intel:read')
  const { data: status } = useQuery({
    queryKey: ['intel-status'], queryFn: getIntelStatus, enabled: canRead, staleTime: 60_000, retry: false })
  const on = canRead && !!status?.enabled
  const { data } = useQuery({
    queryKey: ['intel-situations', 'command-centre'],
    queryFn: () => listSituations({ open: true, sort: 'risk', limit: 8 }),
    enabled: on, refetchInterval: 20_000, retry: false,
  })
  useIntelRealtime([['intel-situations']])
  if (!on || !data) return null
  return (
    <Paper data-testid="situations-panel" sx={{ p: 1.5, flexShrink: 0 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: data.items.length ? 1 : 0 }}>
        <AiMark>AI security situations</AiMark>
        <Typography variant="caption" color="text.secondary">
          {data.total ? `${data.total} open — assessed by the layer, decided by people` : 'None open'}
        </Typography>
        <Button size="small" sx={{ ml: 'auto', minWidth: 0, py: 0 }} onClick={() => navigate('/situations')}>All</Button>
      </Box>
      {!!data.items.length && (
        <Box sx={{ display: 'flex', gap: 1, overflowX: 'auto', pb: 0.5 }}>
          {data.items.map((s) => {
            const color = s.risk_level ? RISK_COLOR[s.risk_level] : '#8ea0b8'
            return (
              <Box key={s.id} role="button" tabIndex={0} onClick={() => navigate(`/situations/${s.id}`)}
                   onKeyDown={(e) => { if (e.key === 'Enter') navigate(`/situations/${s.id}`) }}
                   sx={{ minWidth: 230, maxWidth: 280, p: 1, borderRadius: 2, cursor: 'pointer',
                         border: `1px solid ${color}55`, bgcolor: `${color}12`,
                         '&:hover': { borderColor: color } }}>
                <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center', mb: 0.5, flexWrap: 'wrap' }}>
                  <RiskChip level={s.risk_level} score={s.risk_score} />
                  <DecisionStatusChip status={s.decision_status} />
                </Box>
                <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>{s.title}</Typography>
                <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
                  {[s.site_name, s.primary_camera_name].filter(Boolean).join(' · ') || s.situation_number}
                </Typography>
              </Box>
            )
          })}
        </Box>
      )}
    </Paper>
  )
}

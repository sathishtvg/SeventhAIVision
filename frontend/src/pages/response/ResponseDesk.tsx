/**
 * The response desk: every open incident, who has been sent to it, where that
 * guard's response stands, and the clocks it is running against.
 *
 * The desk shows and suggests. Sending a guard is a person's act, made here
 * through the dispatch the platform has always had; standing one down is a
 * person's act too. A clock that runs out tells people — it does not send,
 * reassign or close anything.
 */
import { useState } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, MenuItem, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { SeverityChip } from '@/components/common/SeverityChip'
import { getSites } from '@/api/sites'
import { apiError, getDesk } from '@/api/incidentResponses'
import type { DeskItem, DeskView } from '@/api/incidentResponses'
import { ClockChips, RecommendDialog, ResponseDetailDialog, StandDownDialog } from '@/components/response/ResponseDialogs'
import { NEEDS_LABEL, STATE_COLOUR, STATE_LABEL, overBecause, since } from '@/components/response/responseFormat'

/** How often the desk asks again. The clocks on it are the server's, not the browser's. */
const REFRESH_MS = 15_000

const VIEWS: { key: DeskView; label: string }[] = [
  { key: 'active', label: 'All open' }, { key: 'waiting', label: 'Nobody sent' }, { key: 'sent', label: 'Guard sent' },
  { key: 'late', label: 'Late' },
]

function Count({ label, value, tone }: { label: string; value: number; tone?: 'warning' | 'error' }) {
  return (
    <GlassCard sx={{ p: 1.5, minWidth: 150, flex: 1 }}>
      <Typography variant="h5" sx={{ fontWeight: 700 }} color={value && tone ? `${tone}.main` : 'text.primary'}>{value}</Typography>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
    </GlassCard>
  )
}

export default function ResponseDesk() {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState('')
  const [view, setView] = useState<DeskView>('active')
  const [sending, setSending] = useState<DeskItem | null>(null)
  const [standing, setStanding] = useState<DeskItem | null>(null)
  const [reading, setReading] = useState<DeskItem | null>(null)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data, isLoading, error } = useQuery({
    queryKey: ['response-desk', siteId, view], queryFn: () => getDesk({ site_id: siteId || undefined, view }),
    refetchInterval: REFRESH_MS,
  })
  const again = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['response-desk'] }), qc.invalidateQueries({ queryKey: ['response-detail'] }),
    qc.invalidateQueries({ queryKey: ['incidents'] })])
  const items = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Response Desk"
                  subtitle="Open incidents: who has been sent, where each response stands, and the clocks it is running against" />
      {data && !data.sla_enabled && (
        <Alert severity="info" sx={{ mb: 2 }}
               action={data.can_manage ? <Button component={RouterLink} to="/response-settings" size="small">Response settings</Button> : undefined}>
          The response clocks are not switched on. The times are shown here as they stand; nothing is recorded as late
          and nobody is told.
        </Alert>)}
      <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', mb: 2 }}>
        <Count label="Nobody sent" value={data?.counts.waiting ?? 0} tone="warning" />
        <Count label="Sent, not yet answered" value={data?.counts.unanswered ?? 0} tone="warning" />
        <Count label="Guard on the way" value={data?.counts.on_the_way ?? 0} />
        <Count label="Late" value={data?.counts.late ?? 0} tone="error" />
      </Stack>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 220 }}
                     slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">Every site</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <ToggleButtonGroup size="small" exclusive value={view} onChange={(_, v: DeskView | null) => v && setView(v)}>
            {VIEWS.map((v) => <ToggleButton key={v.key} value={v.key}>{v.label}</ToggleButton>)}
          </ToggleButtonGroup>
          <Box sx={{ flex: 1 }} />
          {data && <Typography variant="caption" color="text.secondary">{data.note}</Typography>}
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {!!error && <Alert severity="error">{apiError(error)}</Alert>}
        {isLoading ? <Skeleton height={220} /> : !items.length && !error ? (
          <Alert severity="info">
            {view === 'late' ? 'Nothing is late.' : view === 'waiting' ? 'No open incident is waiting for a guard.'
              : view === 'sent' ? 'No guard is out on an incident.'
                : `No incident has been opened in the last ${data?.hours ?? 24} hours, and no guard is out on one.`}
          </Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Incident</TableCell><TableCell>Response</TableCell><TableCell>Clocks</TableCell>
                  <TableCell>Told</TableCell><TableCell />
                </TableRow>
              </TableHead>
              <TableBody>
                {items.map((i) => {
                  const over = !i.response ? overBecause(i.last_response) : null
                  return (
                    <TableRow key={i.id} data-testid="desk-row" hover>
                      <TableCell sx={{ maxWidth: 320 }}>
                        <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>{i.title}</Typography>
                          <SeverityChip severity={i.severity} />
                        </Stack>
                        <Typography variant="caption" color="text.secondary">
                          {[i.site_name ?? 'No site', i.camera_name, `opened ${since(i.created_at, data!.as_of)}`]
                            .filter(Boolean).join(' · ')}
                        </Typography>
                      </TableCell>
                      <TableCell sx={{ maxWidth: 300 }}>
                        {i.response ? (
                          <>
                            <Chip size="small" color={STATE_COLOUR[i.response.state]} label={STATE_LABEL[i.response.state]} />
                            <Typography variant="body2" sx={{ mt: 0.5 }}>
                              {i.response.guard_name ?? 'A guard'} · sent {since(i.response.dispatched_at, data!.as_of)}
                            </Typography>
                          </>
                        ) : (
                          <>
                            <Chip size="small" variant="outlined" color={i.needs === 'DISPATCH' ? 'warning' : 'default'}
                                  label={NEEDS_LABEL[i.needs]} />
                            {over && <Typography variant="body2" color="warning.main" sx={{ mt: 0.5 }}>{over}</Typography>}
                          </>
                        )}
                      </TableCell>
                      <TableCell><ClockChips clocks={i.clocks} quiet={!i.judged} /></TableCell>
                      <TableCell>
                        {i.escalations ? <Chip size="small" color="error" variant="outlined"
                                               label={`${i.escalations} ${i.escalations === 1 ? 'time' : 'times'}`} /> : '—'}
                      </TableCell>
                      <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                        {data!.can_dispatch && !i.response && (
                          <Button size="small" variant={i.needs === 'DISPATCH' ? 'contained' : 'text'}
                                  onClick={() => setSending(i)}>Who to send</Button>)}
                        {i.may.stand_down && (
                          <Button size="small" color="warning" onClick={() => setStanding(i)}>Stand down</Button>)}
                        <Button size="small" onClick={() => setReading(i)}>Open</Button>
                      </TableCell>
                    </TableRow>
                  )
                })}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        {data?.has_more && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            There are more than are shown. Choose a site, or a narrower view.</Typography>)}
      </GlassCard>
      <RecommendDialog incident={sending} onClose={() => setSending(null)} onSent={again} />
      <StandDownDialog incident={standing} guardName={standing?.response?.guard_name ?? null}
                       onClose={() => setStanding(null)} onDone={again} />
      <ResponseDetailDialog incident={reading} onClose={() => setReading(null)} />
    </Box>
  )
}

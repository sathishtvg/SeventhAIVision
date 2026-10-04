/**
 * Drone Patrol — the reporting side of "Patrols & reports": a period's summary,
 * who is emailed which report and how often, and what became of each email.
 *
 * Reading is `drone:report:read`; taking data out — the workbook, and deciding
 * who is emailed — is `drone:report:export`.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Grid, IconButton, MenuItem,
  Skeleton, Switch, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip,
  Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import DownloadIcon from '@mui/icons-material/Download'
import ReplayIcon from '@mui/icons-material/Replay'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import {
  REPORT_FREQUENCIES, RISK_LEVELS, apiError, blobApiError, createRecipient, deleteRecipient, downloadPeriodSummary,
  getPeriodSummary, listDeliveries, listMissions, listRecipients, retryDelivery, updateRecipient,
} from '@/api/drones'
import type { ReportDelivery, ReportFrequency, ReportRecipient } from '@/api/drones'
import { RiskChip } from '@/components/drones/droneUi'
import { fmt, pretty } from '@/components/drones/droneFormat'
import { formatDistance } from '@/components/drones/geo'

const FREQUENCY_HELP: Record<ReportFrequency, string> = {
  IMMEDIATE: 'The PDF of each flight, a few minutes after it ends',
  DAILY: "A workbook of yesterday's flights, each morning",
  WEEKLY: "A workbook of last week's flights, each Monday",
  MONTHLY: "A workbook of last month's flights, on the 1st",
}
const FREQUENCY_LABEL: Record<ReportFrequency, string> = {
  IMMEDIATE: 'After each flight', DAILY: 'Daily', WEEKLY: 'Weekly', MONTHLY: 'Monthly',
}

const covers = (r: ReportRecipient) =>
  r.scope === 'mission' ? `Mission: ${r.mission_name ?? '—'}` : r.scope === 'site' ? `Site: ${r.site_name ?? '—'}` : 'All sites'

const isoDay = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`

// ── Summary ──────────────────────────────────────────────────────────────────

export function SummaryPanel() {
  const canExport = usePermission('drone:report:export')
  const today = new Date()
  const [from, setFrom] = useState(isoDay(new Date(today.getTime() - 6 * 86_400_000)))
  const [to, setTo] = useState(isoDay(today))
  const [siteId, setSiteId] = useState('')
  const query = { from, to, site_id: siteId || undefined }
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data, isLoading, error } = useQuery({
    queryKey: ['drone-report-summary', query], queryFn: () => getPeriodSummary(query), enabled: !!from && !!to,
    retry: false,
  })
  const download = useMutation({
    // A refused download carries its reason as a Blob; read it into the error.
    mutationFn: () => downloadPeriodSummary(query).catch(async (e) => { throw new Error(await blobApiError(e)) }),
  })
  const t = data?.totals
  const hours = t ? t.flight_seconds / 3600 : 0
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField size="small" type="date" label="From" value={from} slotProps={{ inputLabel: { shrink: true } }}
                     onChange={(e) => setFrom(e.target.value)} />
          <TextField size="small" type="date" label="To" value={to} slotProps={{ inputLabel: { shrink: true } }}
                     onChange={(e) => setTo(e.target.value)} />
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 180 }}
                     onChange={(e) => setSiteId(e.target.value)}>
            <MenuItem value="">All sites</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <Box sx={{ flex: 1 }} />
          {canExport && (
            <Button startIcon={<DownloadIcon />} variant="outlined" disabled={download.isPending || !t?.flights}
                    onClick={() => download.mutate()}>{download.isPending ? 'Preparing…' : 'Download workbook'}</Button>
          )}
        </Stack>
        {data && <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          {pretty(data.scope)}, whole days in {data.timezone}. The daily, weekly and monthly emails carry these numbers.
        </Typography>}
        {download.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(download.error)}</Alert>}
      </GlassCard>
      {error ? <Alert severity="error">{apiError(error)}</Alert> : isLoading || !t ? <Skeleton height={160} /> : (
        <>
          <Grid container spacing={2} sx={{ mb: 2 }}>
            {([
              ['Flights', t.flights], ['Completed', t.completed], ['Did not complete', t.did_not_complete],
              ['Time in the air', `${hours.toFixed(1)} h`], ['Distance', formatDistance(t.distance_m)],
              ['Events', t.events], ['Suspicious', t.suspicious], ['Incidents', t.incidents],
              ['False positives', t.false_positives],
            ] as [string, string | number][]).map(([label, value]) => (
              <Grid key={label} size={{ xs: 6, sm: 4, md: 'grow' }}>
                <GlassCard sx={{ p: 1.5 }}>
                  <Typography variant="caption" color="text.secondary">{label}</Typography>
                  <Typography variant="h6" sx={{ fontWeight: 700 }}>{value}</Typography>
                </GlassCard>
              </Grid>
            ))}
          </Grid>
          <GlassCard sx={{ p: 2 }}>
            {!t.flights ? <Alert severity="info">No flights in this period.</Alert> : (
              <Stack direction="row" sx={{ gap: 4, flexWrap: 'wrap' }}>
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>Flights by outcome</Typography>
                  <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
                    {Object.entries(t.by_status).map(([status, n]) => (
                      <Chip key={status} size="small" label={`${pretty(status)} · ${n}`} />))}
                  </Stack>
                </Box>
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 1 }}>Events by risk</Typography>
                  <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
                    {[...RISK_LEVELS].reverse().filter((l) => t.by_risk[l]).map((l) => (
                      <RiskChip key={l} level={l} score={t.by_risk[l]} />))}
                    {!t.events && <Typography variant="body2" color="text.secondary">None</Typography>}
                  </Stack>
                </Box>
              </Stack>
            )}
          </GlassCard>
        </>
      )}
    </>
  )
}

// ── Recipients ───────────────────────────────────────────────────────────────

export function RecipientsPanel() {
  const qc = useQueryClient()
  const canChange = usePermission('drone:report:export')
  const [adding, setAdding] = useState(false)
  const { data, isLoading } = useQuery({ queryKey: ['drone-report-recipients'], queryFn: listRecipients })
  const done = () => qc.invalidateQueries({ queryKey: ['drone-report-recipients'] })
  const toggle = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) => updateRecipient(id, { is_active }),
    onSuccess: done,
  })
  const remove = useMutation({ mutationFn: (id: string) => deleteRecipient(id), onSuccess: done })
  const problem = toggle.error ?? remove.error
  return (
    <GlassCard sx={{ p: 2 }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 1, gap: 2 }}>
        <Typography variant="body2" color="text.secondary">
          Who is emailed drone patrol reports. Each address receives the flights it covers — every site, one site
          or one mission — as often as it says.</Typography>
        {canChange && <Button startIcon={<AddIcon />} variant="contained" sx={{ flexShrink: 0 }}
                              onClick={() => setAdding(true)}>Add recipient</Button>}
      </Stack>
      {problem && <Alert severity="error" sx={{ mb: 1 }}>{apiError(problem)}</Alert>}
      {isLoading ? <Skeleton height={160} /> : !(data ?? []).length ? (
        <Alert severity="info">Nobody is emailed reports yet. They can still be downloaded from each flight.</Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Email</TableCell><TableCell>Covers</TableCell><TableCell>How often</TableCell>
                <TableCell>Added by</TableCell><TableCell>Sending</TableCell><TableCell /></TableRow>
            </TableHead>
            <TableBody>
              {(data ?? []).map((r) => (
                <TableRow key={r.id}>
                  <TableCell>{r.email}</TableCell>
                  <TableCell>{covers(r)}</TableCell>
                  <TableCell><Tooltip title={FREQUENCY_HELP[r.frequency]}><span>{FREQUENCY_LABEL[r.frequency]}</span></Tooltip></TableCell>
                  <TableCell>{r.created_by_name ?? '—'}</TableCell>
                  <TableCell>
                    <Switch size="small" checked={r.is_active} disabled={!canChange || toggle.isPending}
                            slotProps={{ input: { 'aria-label': `Send to ${r.email}` } }}
                            onChange={(_, v) => toggle.mutate({ id: r.id, is_active: v })} />
                  </TableCell>
                  <TableCell align="right">
                    {canChange && (
                      <IconButton size="small" aria-label={`Remove ${r.email}`} disabled={remove.isPending}
                                  onClick={() => { if (window.confirm(`Stop sending reports to ${r.email}?`)) remove.mutate(r.id) }}>
                        <DeleteIcon fontSize="small" /></IconButton>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      {adding && <RecipientDialog onClose={(saved) => { setAdding(false); if (saved) done() }} />}
    </GlassCard>
  )
}

function RecipientDialog({ onClose }: { onClose: (saved: boolean) => void }) {
  const [email, setEmail] = useState('')
  const [frequency, setFrequency] = useState<ReportFrequency>('IMMEDIATE')
  const [scope, setScope] = useState<'organisation' | 'site' | 'mission'>('organisation')
  const [siteId, setSiteId] = useState('')
  const [missionId, setMissionId] = useState('')
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data: missions } = useQuery({ queryKey: ['drone-missions'], queryFn: () => listMissions(),
                                        enabled: scope === 'mission' })
  const save = useMutation({
    mutationFn: () => createRecipient({ email: email.trim(), frequency,
                                        site_id: scope === 'site' ? siteId : null,
                                        mission_id: scope === 'mission' ? missionId : null }),
    onSuccess: () => onClose(true),
  })
  const ready = /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(email.trim())
    && (scope === 'organisation' || (scope === 'site' ? !!siteId : !!missionId))
  return (
    <Dialog open onClose={() => onClose(false)} maxWidth="sm" fullWidth>
      <DialogTitle>Add a report recipient</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, pt: 1 }}>
          <TextField label="Email" type="email" value={email} autoFocus onChange={(e) => setEmail(e.target.value)} />
          <TextField select label="Covers" value={scope}
                     onChange={(e) => setScope(e.target.value as typeof scope)}>
            <MenuItem value="organisation">Every flight, at all sites</MenuItem>
            <MenuItem value="site">One site's flights</MenuItem>
            <MenuItem value="mission">One mission's flights</MenuItem>
          </TextField>
          {scope === 'site' && (
            <TextField select label="Site" value={siteId} onChange={(e) => setSiteId(e.target.value)}>
              {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
          )}
          {scope === 'mission' && (
            <TextField select label="Mission" value={missionId} onChange={(e) => setMissionId(e.target.value)}>
              {(missions?.items ?? []).map((m) => <MenuItem key={m.id} value={m.id}>{m.name} · {m.site_name}</MenuItem>)}
            </TextField>
          )}
          <TextField select label="How often" value={frequency} helperText={FREQUENCY_HELP[frequency]}
                     onChange={(e) => setFrequency(e.target.value as ReportFrequency)}>
            {REPORT_FREQUENCIES.map((f) => <MenuItem key={f} value={f}>{FREQUENCY_LABEL[f]}</MenuItem>)}
          </TextField>
          {save.error && <Alert severity="error">{apiError(save.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose(false)}>Cancel</Button>
        <Button variant="contained" disabled={!ready || save.isPending} onClick={() => save.mutate()}>Add</Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Deliveries ───────────────────────────────────────────────────────────────

const DELIVERY_COLOR: Record<ReportDelivery['status'], 'default' | 'info' | 'success' | 'error'> = {
  PENDING: 'default', PROCESSING: 'info', SENT: 'success', FAILED: 'error',
}

function deliveryNote(d: ReportDelivery): string {
  if (d.status === 'SENT') return `Sent ${fmt(d.sent_at)}`
  if (d.status === 'FAILED') {
    return d.attempts_left ? `Failed ${d.attempts} time(s); next try ${fmt(d.scheduled_at)}`
      : `Failed ${d.attempts} times and will not be tried again by itself`
  }
  return d.status === 'PROCESSING' ? 'Being sent' : `Waiting since ${fmt(d.created_at)}`
}

export function DeliveriesPanel() {
  const qc = useQueryClient()
  const canRetry = usePermission('drone:report:export')
  const { data, isLoading } = useQuery({ queryKey: ['drone-report-deliveries'], queryFn: () => listDeliveries(),
                                         refetchInterval: 30_000 })
  const retry = useMutation({
    mutationFn: (id: string) => retryDelivery(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['drone-report-deliveries'] }),
  })
  const items = data?.items ?? []
  return (
    <GlassCard sx={{ p: 2 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Every report email, newest first. A failed one is retried by itself up to five times, further apart each
        time; after that it waits here to be sent again.</Typography>
      {retry.error && <Alert severity="error" sx={{ mb: 1 }}>{apiError(retry.error)}</Alert>}
      {isLoading ? <Skeleton height={160} /> : !items.length ? (
        <Alert severity="info">No report has been emailed yet.</Alert>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow><TableCell>Report</TableCell><TableCell>To</TableCell><TableCell>Status</TableCell>
                <TableCell /></TableRow>
            </TableHead>
            <TableBody>
              {items.map((d) => (
                <TableRow key={d.id}>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{d.subject}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {FREQUENCY_LABEL[d.frequency]} · queued {fmt(d.created_at)}</Typography>
                  </TableCell>
                  <TableCell sx={{ maxWidth: 260 }}>{d.recipients.join(', ')}</TableCell>
                  <TableCell>
                    <Chip size="small" color={DELIVERY_COLOR[d.status]} label={pretty(d.status)} />
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{deliveryNote(d)}</Typography>
                    {d.last_error && <Typography variant="caption" color="error" sx={{ display: 'block' }}>
                      {d.last_error}</Typography>}
                  </TableCell>
                  <TableCell align="right">
                    {canRetry && d.status === 'FAILED' && (
                      <Button size="small" startIcon={<ReplayIcon />} disabled={retry.isPending}
                              onClick={() => retry.mutate(d.id)}>Send again</Button>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
    </GlassCard>
  )
}

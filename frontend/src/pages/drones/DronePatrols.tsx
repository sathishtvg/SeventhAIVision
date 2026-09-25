/**
 * Drone Patrol — every flight, searchable, with a summary of the period and a
 * CSV export. A row opens the live view while it flies and the replay after.
 */
import { useMemo, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Grid, MenuItem, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead,
  TablePagination, TableRow, TextField, Typography,
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import { IN_FLIGHT, apiError, listDrones, listSessions } from '@/api/drones'
import type { Session, SessionStatus } from '@/api/drones'
import { LicenceBanner, SessionStatusChip } from '@/components/drones/droneUi'
import { fmt, pretty, useDroneRealtime } from '@/components/drones/droneFormat'
import { formatDistance } from '@/components/drones/geo'
import { DroneNav } from './DroneNav'

const STATUSES: SessionStatus[] = ['ACTIVE', 'RETURNING', 'PAUSED', 'EVENT_DETECTED', 'COMPLETED', 'FAILED',
                                   'ABORTED', 'CANCELLED', 'BLOCKED', 'MISSED', 'SCHEDULED']
const EXPORT_LIMIT = 200   // the API's page ceiling
const EXPORT_MAX = 5000

const dayStart = (d: string) => (d ? new Date(`${d}T00:00:00`).toISOString() : undefined)
const dayEnd = (d: string) => (d ? new Date(new Date(`${d}T00:00:00`).getTime() + 86_400_000).toISOString() : undefined)

function durationMin(s: Session): number | null {
  const a = s.launched_at ?? s.started_at
  if (!a || !s.ended_at) return null
  return Math.max(0, Math.round((new Date(s.ended_at).getTime() - new Date(a).getTime()) / 60_000))
}

function csvCell(v: unknown): string {
  const t = v == null ? '' : String(v)
  return /[",\n]/.test(t) ? `"${t.replace(/"/g, '""')}"` : t
}

export default function DronePatrols() {
  const navigate = useNavigate()
  const canExport = usePermission('drone:report:export')
  const [siteId, setSiteId] = useState('')
  const [droneId, setDroneId] = useState('')
  const [status, setStatus] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState(25)
  useDroneRealtime([['drone-patrols']])

  const filters = { site_id: siteId || undefined, drone_id: droneId || undefined, status: status || undefined,
                    from: dayStart(from), to: dayEnd(to) }
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const { data: drones } = useQuery({ queryKey: ['drones', 'all'], queryFn: () => listDrones() })
  const { data, isLoading } = useQuery({
    queryKey: ['drone-patrols', filters, page, rows],
    queryFn: () => listSessions({ ...filters, limit: rows, offset: page * rows }),
    refetchInterval: 20_000,
  })
  const sessions = useMemo(() => data?.items ?? [], [data])

  const summary = useMemo(() => {
    const done = sessions.filter((s) => s.status === 'COMPLETED').length
    const failed = sessions.filter((s) => ['FAILED', 'ABORTED', 'BLOCKED', 'MISSED'].includes(s.status)).length
    const events = sessions.reduce((n, s) => n + s.event_count, 0)
    const incidents = sessions.reduce((n, s) => n + s.incident_count, 0)
    const metres = sessions.reduce((n, s) => n + (s.distance_m ?? 0), 0)
    return { done, failed, events, incidents, metres }
  }, [sessions])

  const exportCsv = useMutation({
    mutationFn: async () => {
      const all: Session[] = []
      for (let offset = 0; offset < EXPORT_MAX; offset += EXPORT_LIMIT) {
        const p = await listSessions({ ...filters, limit: EXPORT_LIMIT, offset })
        all.push(...p.items)
        if (!p.has_more) break
      }
      const head = ['Session', 'Mission', 'Site', 'Drone', 'Route', 'Profile', 'Trigger', 'Status', 'Scheduled for',
                    'Started', 'Launched', 'Ended', 'Minutes', 'Distance m', 'Events', 'Incidents', 'Reason']
      const lines = all.map((s) => [s.session_number, s.mission_name, s.site_name, s.drone_name, s.route_name,
        s.profile_name, s.triggered_by, s.status, s.scheduled_for, s.started_at, s.launched_at, s.ended_at,
        durationMin(s), s.distance_m, s.event_count, s.incident_count,
        s.blocked_reason ?? s.failure_reason ?? s.abort_reason].map(csvCell).join(','))
      const blob = new Blob([[head.join(','), ...lines].join('\n')], { type: 'text/csv' })
      const a = document.createElement('a')
      a.href = URL.createObjectURL(blob)
      a.download = `drone-patrols-${new Date().toISOString().slice(0, 10)}.csv`
      a.click()
      URL.revokeObjectURL(a.href)
      return all.length
    },
  })

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Patrols & Reports" subtitle="Every drone flight: what flew, where, what it found" />
      <DroneNav />
      <LicenceBanner />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 180 }}
                     onChange={(e) => { setSiteId(e.target.value); setPage(0) }}>
            <MenuItem value="">All sites</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Drone" value={droneId} sx={{ minWidth: 180 }}
                     onChange={(e) => { setDroneId(e.target.value); setPage(0) }}>
            <MenuItem value="">All drones</MenuItem>
            {(drones?.items ?? []).map((d) => <MenuItem key={d.id} value={d.id}>{d.name} ({d.code})</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Status" value={status} sx={{ minWidth: 160 }}
                     onChange={(e) => { setStatus(e.target.value); setPage(0) }}>
            <MenuItem value="">Any</MenuItem>
            {STATUSES.map((s) => <MenuItem key={s} value={s}>{pretty(s)}</MenuItem>)}
          </TextField>
          <TextField size="small" type="date" label="From" value={from} slotProps={{ inputLabel: { shrink: true } }}
                     onChange={(e) => { setFrom(e.target.value); setPage(0) }} />
          <TextField size="small" type="date" label="To" value={to} slotProps={{ inputLabel: { shrink: true } }}
                     onChange={(e) => { setTo(e.target.value); setPage(0) }} />
          <Box sx={{ flex: 1 }} />
          {canExport && (
            <Button startIcon={<DownloadIcon />} variant="outlined" disabled={exportCsv.isPending}
                    onClick={() => exportCsv.mutate()}>{exportCsv.isPending ? 'Exporting…' : 'Export CSV'}</Button>
          )}
        </Stack>
        {exportCsv.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(exportCsv.error)}</Alert>}
      </GlassCard>

      <Grid container spacing={2} sx={{ mb: 2 }}>
        {[
          ['Flights (this page)', sessions.length], ['Completed', summary.done], ['Failed or blocked', summary.failed],
          ['Events', summary.events], ['Incidents', summary.incidents], ['Distance flown', formatDistance(summary.metres)],
        ].map(([label, value]) => (
          <Grid key={label as string} size={{ xs: 6, md: 2 }}>
            <GlassCard sx={{ p: 1.5 }}>
              <Typography variant="caption" color="text.secondary">{label}</Typography>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>{value}</Typography>
            </GlassCard>
          </Grid>
        ))}
      </Grid>

      <GlassCard sx={{ p: 2 }}>
        {isLoading ? <Skeleton height={240} /> : !sessions.length ? (
          <Alert severity="info">No flights match.</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Session</TableCell><TableCell>Mission</TableCell><TableCell>Site</TableCell>
                  <TableCell>Drone</TableCell><TableCell>Status</TableCell><TableCell>Started</TableCell>
                  <TableCell>Minutes</TableCell><TableCell>Distance</TableCell><TableCell>Events</TableCell>
                  <TableCell>Incidents</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {sessions.map((s) => (
                  <TableRow key={s.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/drone-patrols/${s.id}`)}>
                    <TableCell><Typography variant="body2" sx={{ fontFamily: 'monospace' }}>{s.session_number}</Typography>
                      <Typography variant="caption" color="text.secondary">{pretty(s.triggered_by)}</Typography></TableCell>
                    <TableCell>{s.mission_name ?? '—'}</TableCell>
                    <TableCell>{s.site_name ?? '—'}</TableCell>
                    <TableCell>{s.drone_name ?? '—'}</TableCell>
                    <TableCell>
                      <SessionStatusChip status={s.status} />
                      {IN_FLIGHT.includes(s.status) && <Typography variant="caption" color="primary" sx={{ display: 'block' }}>live</Typography>}
                      {(s.blocked_reason || s.failure_reason) && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', maxWidth: 260 }}>
                          {s.blocked_reason ?? s.failure_reason}</Typography>)}
                    </TableCell>
                    <TableCell>{fmt(s.launched_at ?? s.started_at)}</TableCell>
                    <TableCell>{durationMin(s) ?? '—'}</TableCell>
                    <TableCell>{s.distance_m != null ? formatDistance(s.distance_m) : '—'}</TableCell>
                    <TableCell>{s.event_count}</TableCell>
                    <TableCell>{s.incident_count}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        <TablePagination component="div" count={data?.total ?? 0} page={page} rowsPerPage={rows}
                         rowsPerPageOptions={[25, 50, 100]} onPageChange={(_, p) => setPage(p)}
                         onRowsPerPageChange={(e) => { setRows(Number(e.target.value)); setPage(0) }} />
        {exportCsv.data != null && (
          <Typography variant="caption" color="text.secondary">Exported {exportCsv.data} flight(s).</Typography>
        )}
      </GlassCard>
    </Box>
  )
}

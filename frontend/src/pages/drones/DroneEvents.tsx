/**
 * Drone Patrol — what the drones found. Open events first; a row opens the
 * investigation screen.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Chip, FormControlLabel, MenuItem, Skeleton, Switch, Table, TableBody, TableCell, TableContainer,
  TableHead, TablePagination, TableRow, TextField, Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { getSites } from '@/api/sites'
import { RISK_LEVELS, listEvents } from '@/api/drones'
import type { EventStatus } from '@/api/drones'
import { ConfidenceText, EventStatusChip, LicenceBanner, RiskChip } from '@/components/drones/droneUi'
import { fmt, pretty, useDroneRealtime } from '@/components/drones/droneFormat'
import { DroneNav } from './DroneNav'

const STATUSES: EventStatus[] = ['NEW', 'ACKNOWLEDGED', 'INVESTIGATING', 'ESCALATED', 'RESOLVED', 'FALSE_POSITIVE']

export default function DroneEvents() {
  const navigate = useNavigate()
  const [siteId, setSiteId] = useState('')
  const [risk, setRisk] = useState('')
  const [status, setStatus] = useState('')
  const [openOnly, setOpenOnly] = useState(true)
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState(25)
  useDroneRealtime([['drone-events']])
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const params = { site_id: siteId || undefined, risk_level: risk || undefined, status: status || undefined,
                   open_only: openOnly && !status }
  const { data, isLoading } = useQuery({
    queryKey: ['drone-events', params, page, rows],
    queryFn: () => listEvents({ ...params, limit: rows, offset: page * rows }),
    refetchInterval: 15_000,
  })
  const events = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Drone Events" subtitle="What the drones saw, how serious it is, and what was done about it" />
      <DroneNav />
      <LicenceBanner />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 180 }}
                     onChange={(e) => { setSiteId(e.target.value); setPage(0) }}>
            <MenuItem value="">All sites</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Risk" value={risk} sx={{ minWidth: 140 }}
                     onChange={(e) => { setRisk(e.target.value); setPage(0) }}>
            <MenuItem value="">Any</MenuItem>
            {RISK_LEVELS.map((r) => <MenuItem key={r} value={r}>{pretty(r)}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Status" value={status} sx={{ minWidth: 160 }}
                     onChange={(e) => { setStatus(e.target.value); setPage(0) }}>
            <MenuItem value="">Any</MenuItem>
            {STATUSES.map((s) => <MenuItem key={s} value={s}>{pretty(s)}</MenuItem>)}
          </TextField>
          <FormControlLabel control={<Switch checked={openOnly} disabled={!!status}
                                             onChange={(_, v) => { setOpenOnly(v); setPage(0) }} />} label="Open only" />
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {isLoading ? <Skeleton height={240} /> : !events.length ? (
          <Alert severity="info">{openOnly && !status ? 'No open drone events.' : 'No events match.'}</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Detected</TableCell><TableCell>What</TableCell><TableCell>Risk</TableCell>
                  <TableCell>AI</TableCell><TableCell>Where</TableCell><TableCell>Drone</TableCell>
                  <TableCell>Verified</TableCell><TableCell>Status</TableCell><TableCell>Incident</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {events.map((e) => (
                  <TableRow key={e.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/drone-events/${e.id}`)}>
                    <TableCell>{fmt(e.detected_at)}</TableCell>
                    <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{pretty(e.module_type)}</Typography>
                      {e.label && <Typography variant="caption" color="text.secondary">{e.label}</Typography>}
                      {e.detection_count > 1 && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        seen {e.detection_count}×</Typography>}</TableCell>
                    <TableCell><RiskChip level={e.risk_level} score={e.risk_score} /></TableCell>
                    <TableCell><ConfidenceText value={e.ai_confidence} /></TableCell>
                    <TableCell>{e.site_name ?? '—'}
                      {e.zone_name && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {e.zone_name} ({pretty(e.zone_type)})</Typography>}</TableCell>
                    <TableCell>{e.drone_name ?? '—'}
                      {e.session_number && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {e.session_number}</Typography>}</TableCell>
                    <TableCell>{pretty(e.verification_state)}</TableCell>
                    <TableCell><EventStatusChip status={e.status} /></TableCell>
                    <TableCell>{e.incident_id ? <Chip size="small" color="error" variant="outlined" label="Linked" /> : '—'}</TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
        <TablePagination component="div" count={data?.total ?? 0} page={page} rowsPerPage={rows}
                         rowsPerPageOptions={[25, 50, 100]} onPageChange={(_, p) => setPage(p)}
                         onRowsPerPageChange={(e) => { setRows(Number(e.target.value)); setPage(0) }} />
      </GlassCard>
    </Box>
  )
}

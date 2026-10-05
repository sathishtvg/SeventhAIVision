/**
 * Security Intelligence — the situations that want attention. One row is one
 * matter, however many alerts fed it. Highest risk first, those a person has
 * closed left out unless asked for. A row opens the situation.
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
import { DECISION_STATUSES, RISK_LEVELS, listSituations } from '@/api/securityIntelligence'
import type { SituationFilters } from '@/api/securityIntelligence'
import { DecisionStatusChip, RiskChip } from '@/components/intel/intelUi'
import { SOURCE_LABEL, STATUS_LABEL, fmt, pretty, useIntelRealtime } from '@/components/intel/intelFormat'
import { IntelNav, IntelStatusBanner } from './IntelNav'

export default function Situations() {
  const navigate = useNavigate()
  const [siteId, setSiteId] = useState('')
  const [risk, setRisk] = useState('')
  const [stands, setStands] = useState('')
  const [openOnly, setOpenOnly] = useState(true)
  const [sort, setSort] = useState<'risk' | 'recent'>('risk')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState(25)
  useIntelRealtime([['intel-situations']])
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const params: SituationFilters = {
    site_id: siteId || undefined, risk_level: risk || undefined, decision_status: stands || undefined,
    open: openOnly && !stands ? true : undefined, sort,
  }
  const { data, isLoading } = useQuery({
    queryKey: ['intel-situations', params, page, rows],
    queryFn: () => listSituations({ ...params, limit: rows, offset: page * rows }),
    refetchInterval: 15_000,
  })
  const situations = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Security Situations"
                  subtitle="What the platform's sources reported, joined into matters, assessed — and waiting for a person to decide" />
      <IntelNav />
      <IntelStatusBanner />
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
          <TextField select size="small" label="Stands" value={stands} sx={{ minWidth: 200 }}
                     onChange={(e) => { setStands(e.target.value); setPage(0) }}>
            <MenuItem value="">Any</MenuItem>
            {DECISION_STATUSES.map((s) => <MenuItem key={s} value={s}>{STATUS_LABEL[s]}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Order" value={sort} sx={{ minWidth: 170 }}
                     onChange={(e) => { setSort(e.target.value as 'risk' | 'recent'); setPage(0) }}>
            <MenuItem value="risk">Highest risk first</MenuItem>
            <MenuItem value="recent">Most recent first</MenuItem>
          </TextField>
          <FormControlLabel control={<Switch checked={openOnly} disabled={!!stands}
                                             onChange={(_, v) => { setOpenOnly(v); setPage(0) }} />} label="Open only" />
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {isLoading ? <Skeleton height={240} /> : !situations.length ? (
          <Alert severity="info">{openOnly && !stands ? 'No open situations.' : 'No situations match.'}</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Risk (AI)</TableCell><TableCell>Situation</TableCell><TableCell>Where</TableCell>
                  <TableCell>Sources</TableCell><TableCell>Events</TableCell><TableCell>Last heard</TableCell>
                  <TableCell>Stands (people)</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {situations.map((s) => (
                  <TableRow key={s.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/situations/${s.id}`)}>
                    <TableCell><RiskChip level={s.risk_level} score={s.risk_score} /></TableCell>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{s.title}</Typography>
                      <Typography variant="caption" color="text.secondary">{s.situation_number}</Typography>
                    </TableCell>
                    <TableCell>{s.site_name ?? '—'}
                      {(s.primary_camera_name || s.location_label) && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {s.primary_camera_name ?? s.location_label}</Typography>)}</TableCell>
                    <TableCell>
                      <Stack direction="row" sx={{ gap: 0.5, flexWrap: 'wrap' }}>
                        {s.source_types.map((t) => <Chip key={t} size="small" variant="outlined" label={SOURCE_LABEL[t] ?? t} />)}
                      </Stack>
                    </TableCell>
                    <TableCell>{s.event_count}
                      {s.duplicate_count > 0 && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {s.duplicate_count} repeat(s) folded</Typography>)}</TableCell>
                    <TableCell>{fmt(s.last_event_at)}</TableCell>
                    <TableCell><DecisionStatusChip status={s.decision_status} /></TableCell>
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

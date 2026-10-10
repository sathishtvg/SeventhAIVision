/**
 * Smart Investigation — the investigations: who opened each, about what, and
 * whether it is still open. Most recently opened first. A row opens the file.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, FormControlLabel, MenuItem, Skeleton, Switch, Table, TableBody, TableCell, TableContainer,
  TableHead, TablePagination, TableRow, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import { listInvestigations } from '@/api/investigations'
import { OpenDialog } from '@/components/investigations/InvestigationDialogs'
import { fmt } from '@/components/investigations/investigationFormat'
import { InvestigationNav } from './InvestigationNav'
import { ErrorState } from '@/components/states'

export default function Investigations() {
  const navigate = useNavigate()
  const canManage = usePermission('investigation:manage')
  const [status, setStatus] = useState<'' | 'OPEN' | 'CLOSED'>('OPEN')
  const [siteId, setSiteId] = useState('')
  const [mine, setMine] = useState(false)
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState(25)
  const [opening, setOpening] = useState(false)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const words = q.trim().length >= 2 ? q.trim() : undefined
  const { data, isLoading, isLoadingError: dataFailed, error: dataError, refetch: refetchData } = useQuery({
    queryKey: ['investigations', status, siteId, mine, words, page, rows],
    queryFn: () => listInvestigations({
      status: status || undefined, site_id: siteId || undefined, mine: mine || undefined, q: words, limit: rows,
      offset: page * rows }),
  })
  const files = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Investigations"
                  subtitle="What somebody set out to find, why, and the records they put together to find it"
                  action={canManage ? (
                    <Button variant="contained" startIcon={<AddIcon />} onClick={() => setOpening(true)}>
                      Open an investigation</Button>) : undefined} />
      <InvestigationNav />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Status" value={status} sx={{ minWidth: 140 }}
                     onChange={(e) => { setStatus(e.target.value as '' | 'OPEN' | 'CLOSED'); setPage(0) }}>
            <MenuItem value="">Any</MenuItem>
            <MenuItem value="OPEN">Open</MenuItem>
            <MenuItem value="CLOSED">Closed</MenuItem>
          </TextField>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 180 }}
                     onChange={(e) => { setSiteId(e.target.value); setPage(0) }}>
            <MenuItem value="">All sites</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField size="small" label="Title or number" value={q} sx={{ minWidth: 220 }}
                     onChange={(e) => { setQ(e.target.value); setPage(0) }} />
          <FormControlLabel control={<Switch checked={mine} onChange={(_, v) => { setMine(v); setPage(0) }} />}
                            label="Opened by me" />
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {isLoading ? <Skeleton height={240} /> : dataFailed ? <ErrorState compact error={dataError} onRetry={refetchData} /> : !files.length ? (
          <Alert severity="info">{status === 'OPEN' && !siteId && !mine && !words
            ? 'No investigation is open.' : 'No investigations match.'}</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Investigation</TableCell><TableCell>Site</TableCell><TableCell>Records</TableCell>
                  <TableCell>Opened</TableCell><TableCell>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {files.map((f) => (
                  <TableRow key={f.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/investigations/${f.id}`)}>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{f.title}</Typography>
                      <Typography variant="caption" color="text.secondary">{f.investigation_number}</Typography>
                    </TableCell>
                    <TableCell>{f.site_name ?? 'More than one site'}</TableCell>
                    <TableCell>{f.records}</TableCell>
                    <TableCell>{fmt(f.opened_at)}
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {f.opened_by_name ?? '—'}</Typography></TableCell>
                    <TableCell>
                      <Chip size="small" color={f.status === 'OPEN' ? 'primary' : 'default'}
                            variant={f.status === 'OPEN' ? 'filled' : 'outlined'}
                            label={f.status === 'OPEN' ? 'Open' : 'Closed'} />
                      {f.closed_at && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {fmt(f.closed_at)}</Typography>)}
                    </TableCell>
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
      <OpenDialog open={opening} onClose={() => setOpening(false)}
                  onOpened={(id) => { setOpening(false); navigate(`/investigations/${id}`) }} />
    </Box>
  )
}

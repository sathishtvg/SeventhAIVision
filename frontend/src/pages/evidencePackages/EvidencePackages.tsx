/**
 * Evidence packages — what was kept about a matter, put together for somebody.
 * Most recently created first. A row opens the package.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, MenuItem, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead,
  TablePagination, TableRow, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import LockIcon from '@mui/icons-material/Lock'
import { useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import { listPackages } from '@/api/evidencePackages'
import type { PackageStatus } from '@/api/evidencePackages'
import { CreatePackageDialog } from '@/components/evidence/EvidenceDialogs'
import { fmt } from '@/components/evidence/evidenceFormat'
import { InvestigationNav } from '@/pages/investigations/InvestigationNav'
import { ErrorState } from '@/components/states'

export default function EvidencePackages() {
  const navigate = useNavigate()
  const canManage = usePermission('evidence:package:manage')
  const [status, setStatus] = useState<'' | PackageStatus>('')
  const [siteId, setSiteId] = useState('')
  const [q, setQ] = useState('')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState(25)
  const [creating, setCreating] = useState(false)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const words = q.trim().length >= 2 ? q.trim() : undefined
  const { data, isLoading, isLoadingError: dataFailed, error: dataError, refetch: refetchData } = useQuery({
    queryKey: ['evidence-packages', status, siteId, words, page, rows],
    queryFn: () => listPackages({
      status: status || undefined, site_id: siteId || undefined, q: words, limit: rows, offset: page * rows }),
  })
  const packages = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Evidence Packages"
                  subtitle="The frames, clips and recordings of a matter — sealed, held past retention, and accounted for"
                  action={canManage ? (
                    <Button variant="contained" startIcon={<AddIcon />} onClick={() => setCreating(true)}>
                      Put evidence together</Button>) : undefined} />
      <InvestigationNav />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Status" value={status} sx={{ minWidth: 140 }}
                     onChange={(e) => { setStatus(e.target.value as '' | PackageStatus); setPage(0) }}>
            <MenuItem value="">Any</MenuItem>
            <MenuItem value="DRAFT">Draft</MenuItem>
            <MenuItem value="SEALED">Sealed</MenuItem>
          </TextField>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 180 }}
                     onChange={(e) => { setSiteId(e.target.value); setPage(0) }}>
            <MenuItem value="">All sites</MenuItem>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField size="small" label="Title or number" value={q} sx={{ minWidth: 220 }}
                     onChange={(e) => { setQ(e.target.value); setPage(0) }} />
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {isLoading ? <Skeleton height={240} /> : dataFailed ? <ErrorState compact error={dataError} onRetry={refetchData} /> : !packages.length ? (
          <Alert severity="info">{status || siteId || words ? 'No packages match.'
            : 'No evidence has been put together yet. A package is made for an investigation or an incident.'}</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Package</TableCell><TableCell>Evidence of</TableCell><TableCell>Site</TableCell>
                  <TableCell>Items</TableCell><TableCell>Created</TableCell><TableCell>Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {packages.map((p) => (
                  <TableRow key={p.id} hover sx={{ cursor: 'pointer' }} onClick={() => navigate(`/evidence-packages/${p.id}`)}>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{p.title}</Typography>
                      <Typography variant="caption" color="text.secondary">{p.package_number}</Typography>
                    </TableCell>
                    <TableCell>{p.investigation_number ?? (p.incident_id ? 'An incident' : '—')}</TableCell>
                    <TableCell>{p.site_name ?? 'More than one site'}</TableCell>
                    <TableCell>{p.items}
                      {p.holds_in_force > 0 && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {p.holds_in_force} held</Typography>)}</TableCell>
                    <TableCell>{fmt(p.created_at)}
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {p.created_by_name ?? '—'}</Typography></TableCell>
                    <TableCell>
                      {p.status === 'SEALED'
                        ? <Chip size="small" color="primary" icon={<LockIcon />} label="Sealed" />
                        : <Chip size="small" variant="outlined" label="Draft" />}
                      {p.sealed_at && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {fmt(p.sealed_at)}</Typography>)}
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
      <CreatePackageDialog open={creating} onClose={() => setCreating(false)}
                           onCreated={(id) => { setCreating(false); navigate(`/evidence-packages/${id}`) }} />
    </Box>
  )
}

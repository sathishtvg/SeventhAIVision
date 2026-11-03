/**
 * Evidence packages due for removal, and the record of those removed (3 November 2026; Phase 2 of the known limits).
 *
 * The organisation's period (Data Retention screen) makes a package due; nothing removes it but a person with
 * evidence:package:dispose, one at a time, with a reason and the package's number said back. The server checks it
 * is due, and so does the database again (backend/app/routers/evidence_disposal.py, migration 0176). What is kept of
 * a removed package, and its chain of custody, is listed under Removed.
 */
import { useState } from 'react'
import { Link as RouterLink } from 'react-router-dom'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Link, Skeleton, Table, TableBody, TableCell,
  TableContainer, TableHead, TablePagination, TableRow, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { getDueForRemoval, getRemovedCustody, listRemoved, removePackage } from '@/api/evidencePackages'
import type { DuePackage, RemovedPackage } from '@/api/evidencePackages'
import { usePermission } from '@/hooks/usePermission'
import { ErrorState, errorText } from '@/components/states'
import { STEP_LABEL, fmt, roleName } from '@/components/evidence/evidenceFormat'

const days = (n: number) => `${n} day${n === 1 ? '' : 's'}`

function RemoveDialog({ pkg, note, onClose }: { pkg: DuePackage; note: string; onClose: () => void }) {
  const qc = useQueryClient()
  const [reason, setReason] = useState('')
  const [number, setNumber] = useState('')
  const remove = useMutation({
    mutationFn: () => removePackage({ package_id: pkg.id, package_number: number.trim(), reason: reason.trim() }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['evidence-due'] })
      qc.invalidateQueries({ queryKey: ['evidence-removed'] })
      qc.invalidateQueries({ queryKey: ['evidence-packages'] })
      onClose()
    },
  })
  const ready = reason.trim().length >= 5 && number.trim().toUpperCase() === pkg.package_number.toUpperCase()
  return (
    <>
      <DialogTitle>Remove {pkg.package_number}?</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 1 }}>{pkg.title}</Typography>
        <Alert severity="warning" sx={{ mb: 2 }}>{note}</Alert>
        <TextField fullWidth multiline minRows={2} size="small" label="Why it is removed" value={reason}
                   onChange={(e) => setReason(e.target.value)} disabled={remove.isPending} sx={{ mb: 2 }}
                   helperText="Kept with the record of the removal, for good." />
        <TextField fullWidth size="small" label={`Type ${pkg.package_number} to confirm`} value={number}
                   onChange={(e) => setNumber(e.target.value)} disabled={remove.isPending} />
        {remove.error && <Alert severity="error" sx={{ mt: 2 }}>{errorText(remove.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={remove.isPending}>Cancel</Button>
        <Button color="error" variant="contained" disabled={!ready || remove.isPending} onClick={() => remove.mutate()}>
          {remove.isPending ? 'Removing…' : 'Remove the package'}
        </Button>
      </DialogActions>
    </>
  )
}

/** The packages that are due for removal: only for somebody who may remove them. */
export function DueForRemoval() {
  const [removing, setRemoving] = useState<DuePackage | null>(null)
  const { data, isLoading, isLoadingError: dueFailed, error: dueError, refetch: refetchDue } = useQuery({
    queryKey: ['evidence-due'],
    queryFn: getDueForRemoval,
  })
  if (isLoading) return <Skeleton height={160} />
  if (dueFailed || !data) return <ErrorState compact error={dueError} onRetry={refetchDue} />
  if (data.period_days == null) {
    return (
      <Alert severity="info" data-testid="no-period">
        {data.note}{' '}
        <Link component={RouterLink} to="/data-retention">Open Data Retention</Link>
      </Alert>
    )
  }
  return (
    <Box data-testid="due-for-removal">
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        The organisation keeps evidence packages for {days(data.period_days)}. These are past it, nothing in them is
        on hold, and what they were made for is over. {data.note}
      </Typography>
      {!data.packages.length ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>No package is due for removal.</Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Package</TableCell><TableCell>Evidence of</TableCell><TableCell>Site</TableCell>
                <TableCell>Items</TableCell><TableCell>Sealed</TableCell><TableCell>Due since</TableCell><TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {data.packages.map((p) => (
                <TableRow key={p.id}>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{p.package_number}</Typography>
                    <Typography variant="caption" color="text.secondary">{p.title}</Typography>
                  </TableCell>
                  <TableCell>{p.investigation_number ?? (p.incident_id ? 'An incident' : '—')}</TableCell>
                  <TableCell>{p.site_name ?? 'More than one site'}</TableCell>
                  <TableCell>{p.items}</TableCell>
                  <TableCell>{p.sealed_at ? fmt(p.sealed_at) : `Draft, last changed ${fmt(p.updated_at)}`}</TableCell>
                  <TableCell>{fmt(p.due_since)}</TableCell>
                  <TableCell align="right">
                    <Button size="small" color="error" variant="outlined" onClick={() => setRemoving(p)}>Remove…</Button>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <Dialog open={!!removing} onClose={() => setRemoving(null)} maxWidth="sm" fullWidth>
        {removing ? <RemoveDialog pkg={removing} note={data.note} onClose={() => setRemoving(null)} /> : null}
      </Dialog>
    </Box>
  )
}

function CustodyDialog({ record, onClose }: { record: RemovedPackage; onClose: () => void }) {
  const { data, isLoading, isLoadingError: chainFailed, error: chainError, refetch: refetchChain } = useQuery({
    queryKey: ['evidence-removed-custody', record.id],
    queryFn: () => getRemovedCustody(record.id),
  })
  return (
    <>
      <DialogTitle>Chain of custody: {record.package_number}</DialogTitle>
      <DialogContent>
        {isLoading ? <Skeleton height={120} /> : chainFailed || !data ? (
          <ErrorState compact error={chainError} onRetry={refetchChain} />
        ) : (
          <Table size="small">
            <TableBody>
              {data.chain.map((s, n) => (
                <TableRow key={n}>
                  <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmt(s.at)}</TableCell>
                  <TableCell>{STEP_LABEL[s.step] ?? s.step}</TableCell>
                  <TableCell>{[s.actor_name, roleName(s.actor_role)].filter(Boolean).join(', ') || '—'}</TableCell>
                  <TableCell>{s.reason ?? ''}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </>
  )
}

/** What is kept of each removed package, most recent first. */
export function RemovedPackages() {
  const canCustody = usePermission('evidence:custody:read')
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState(25)
  const [custody, setCustody] = useState<RemovedPackage | null>(null)
  const { data, isLoading, isLoadingError: removedFailed, error: removedError, refetch: refetchRemoved } = useQuery({
    queryKey: ['evidence-removed', page, rows],
    queryFn: () => listRemoved({ limit: rows, offset: page * rows }),
  })
  if (isLoading) return <Skeleton height={160} />
  if (removedFailed || !data) return <ErrorState compact error={removedError} onRetry={refetchRemoved} />
  return (
    <Box data-testid="removed-packages">
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        A record of each removed package is kept for good: what it was, the fingerprint of what was sealed, who
        removed it, when and why.
      </Typography>
      {!data.items.length ? (
        <Typography color="text.secondary" sx={{ textAlign: 'center', py: 3 }}>No package has been removed.</Typography>
      ) : (
        <TableContainer>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Package</TableCell><TableCell>Removed</TableCell><TableCell>Why</TableCell>
                <TableCell>Items</TableCell><TableCell>Fingerprint</TableCell><TableCell />
              </TableRow>
            </TableHead>
            <TableBody>
              {data.items.map((r) => (
                <TableRow key={r.id}>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>{r.package_number}</Typography>
                    <Typography variant="caption" color="text.secondary">{r.title}</Typography>
                  </TableCell>
                  <TableCell>
                    {fmt(r.removed_at)}
                    <Typography variant="caption" color="text.secondary" component="div">
                      {r.removed_by_name ?? '—'}{r.period_days ? ` · kept ${days(r.period_days)}` : ''}
                    </Typography>
                  </TableCell>
                  <TableCell>{r.reason}</TableCell>
                  <TableCell>{r.items ?? '—'}</TableCell>
                  <TableCell sx={{ fontFamily: 'monospace', fontSize: 12 }}>
                    {r.manifest_sha256 ? `${r.manifest_sha256.slice(0, 12)}…` : 'Never sealed'}
                  </TableCell>
                  <TableCell align="right">
                    {canCustody && <Button size="small" onClick={() => setCustody(r)}>Chain of custody</Button>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <TablePagination component="div" count={data.total} page={page} rowsPerPage={rows}
                       onPageChange={(_, p) => setPage(p)}
                       onRowsPerPageChange={(e) => { setRows(Number(e.target.value)); setPage(0) }} />
      <Dialog open={!!custody} onClose={() => setCustody(null)} maxWidth="md" fullWidth>
        {custody ? <CustodyDialog record={custody} onClose={() => setCustody(null)} /> : null}
      </Dialog>
    </Box>
  )
}

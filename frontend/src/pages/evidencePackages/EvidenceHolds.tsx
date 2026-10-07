/**
 * Evidence holds — what is being kept past its retention period, and why.
 *
 * A hold in force stops the retention jobs deleting what it is on. Most are
 * placed by sealing a package; one can also be placed on a single item through
 * the API. A hold is lifted by a person, with a reason, and stays on the
 * record as lifted.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Link, MenuItem, Skeleton, Table, TableBody, TableCell, TableContainer, TableHead,
  TablePagination, TableRow, TextField, Typography,
} from '@mui/material'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { listHolds, releaseHold } from '@/api/evidencePackages'
import type { Hold } from '@/api/evidencePackages'
import { WhyDialog } from '@/components/investigations/InvestigationDialogs'
import { KIND_LABEL, fmt } from '@/components/evidence/evidenceFormat'
import { InvestigationNav } from '@/pages/investigations/InvestigationNav'

export default function EvidenceHolds() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const canLift = usePermission('evidence:hold:manage')
  const [inForce, setInForce] = useState(true)
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState(25)
  const [lifting, setLifting] = useState<Hold | null>(null)
  const { data, isLoading } = useQuery({
    queryKey: ['evidence-holds', inForce, page, rows],
    queryFn: () => listHolds({ in_force: inForce, limit: rows, offset: page * rows }),
  })
  const holds = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Evidence Holds"
                  subtitle="What is kept past its retention period because somebody said it must be, and why" />
      <InvestigationNav />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Holds" value={inForce ? 'in' : 'lifted'} sx={{ minWidth: 160 }}
                     onChange={(e) => { setInForce(e.target.value === 'in'); setPage(0) }}>
            <MenuItem value="in">In force</MenuItem>
            <MenuItem value="lifted">Lifted</MenuItem>
          </TextField>
          <Typography variant="caption" color="text.secondary">
            A hold in force stops the retention jobs deleting what it is on. It does not stop anything else.</Typography>
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {isLoading ? <Skeleton height={240} /> : !holds.length ? (
          <Alert severity="info">{inForce ? 'Nothing is under a hold.' : 'No hold has been lifted.'}</Alert>
        ) : (
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>On</TableCell><TableCell>Why</TableCell><TableCell>Site</TableCell>
                  <TableCell>Placed</TableCell><TableCell>{inForce ? '' : 'Lifted'}</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {holds.map((h) => (
                  <TableRow key={h.id} data-testid="hold-row">
                    <TableCell><Chip size="small" label={KIND_LABEL[h.kind]} />
                      {h.package_id && (
                        <Link component="button" variant="caption" sx={{ display: 'block' }}
                              onClick={() => navigate(`/evidence-packages/${h.package_id}`)}>{h.package_number}</Link>)}
                    </TableCell>
                    <TableCell sx={{ maxWidth: 380 }}>{h.reason}</TableCell>
                    <TableCell>{h.site_name ?? '—'}</TableCell>
                    <TableCell>{fmt(h.placed_at)}
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {h.placed_by_name ?? '—'}</Typography></TableCell>
                    <TableCell>
                      {inForce ? (canLift && <Button size="small" onClick={() => setLifting(h)}>Lift</Button>) : (
                        <>
                          {fmt(h.released_at)}
                          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                            {h.released_by_name ?? '—'}: {h.release_reason}</Typography>
                        </>
                      )}
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
      <WhyDialog open={!!lifting} title="Lift this hold" label="Why it is being lifted" confirm="Lift the hold" min={5}
                 hint="What it is on is from then on kept only as long as the retention rules keep anything, and may be deleted. The hold stays on the record as lifted."
                 onClose={() => setLifting(null)}
                 onConfirm={(text) => lifting ? releaseHold(lifting.id, text).then(() =>
                   qc.invalidateQueries({ queryKey: ['evidence-holds'] })) : Promise.resolve()} />
    </Box>
  )
}

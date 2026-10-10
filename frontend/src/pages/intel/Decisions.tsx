/**
 * Security Intelligence — the decisions people have made, most recent first,
 * and the queue of those waiting for a second person. Each row keeps apart
 * what the layer had suggested, what the person decided, and what was then
 * carried out.
 */
import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Skeleton, Switch,
  Table, TableBody, TableCell, TableContainer, TableHead, TablePagination, TableRow, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { apiError, approveDecision, listDecisions, rejectDecision } from '@/api/securityIntelligence'
import type { Decision } from '@/api/securityIntelligence'
import { RiskChip } from '@/components/intel/intelUi'
import {
  BASIS_LABEL, DECISION_LABEL, ROLE_LABEL, STATE_LABEL, STEP_LABEL, fmt, useIntelRealtime,
} from '@/components/intel/intelFormat'
import { IntelNav, IntelStatusBanner } from './IntelNav'
import { ErrorState } from '@/components/states'

export default function Decisions() {
  const navigate = useNavigate()
  const canApprove = usePermission('intel:approve')
  const [waitingOnly, setWaitingOnly] = useState(false)
  const [page, setPage] = useState(0)
  const [rows, setRows] = useState(25)
  const [verdict, setVerdict] = useState<{ decision: Decision; approve: boolean } | null>(null)
  useIntelRealtime([['intel-decisions']])
  const { data, isLoading, error, isLoadingError: dataFailed, refetch: refetchData } = useQuery({
    queryKey: ['intel-decisions', waitingOnly, page, rows],
    queryFn: () => listDecisions({ state: waitingOnly ? 'pending_approval' : undefined, limit: rows, offset: page * rows }),
    refetchInterval: 20_000,
  })
  const { data: waiting } = useQuery({
    queryKey: ['intel-decisions', 'waiting-count'],
    queryFn: () => listDecisions({ state: 'pending_approval', limit: 1 }), refetchInterval: 20_000 })
  const decisions = data?.items ?? []
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Decisions" subtitle="What people decided about each situation, why, and what was then carried out" />
      <IntelNav />
      <IntelStatusBanner />
      {!!waiting?.total && !waitingOnly && (
        <Alert severity="warning" sx={{ mb: 2 }}
               action={<Button color="inherit" size="small" onClick={() => { setWaitingOnly(true); setPage(0) }}>Show</Button>}>
          {waiting.total} decision(s) are waiting for a second person's approval. Nothing has been carried out for them.
        </Alert>)}
      <GlassCard sx={{ p: 2 }}>
        <FormControlLabel sx={{ mb: 1 }} label="Waiting for approval only"
                          control={<Switch checked={waitingOnly} onChange={(_, v) => { setWaitingOnly(v); setPage(0) }} />} />
        {error ? <ErrorState compact error={error} onRetry={refetchData} />
          : isLoading ? <Skeleton height={240} /> : dataFailed ? <ErrorState compact error={error} onRetry={refetchData} /> : !decisions.length ? (
            <Alert severity="info">{waitingOnly ? 'No decision is waiting for approval.' : 'No decisions have been recorded yet.'}</Alert>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>When</TableCell><TableCell>Situation</TableCell><TableCell>Decided by</TableCell>
                    <TableCell>AI had suggested</TableCell><TableCell>Person decided</TableCell>
                    <TableCell>State</TableCell><TableCell>Carried out</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {decisions.map((d) => (
                    <TableRow key={d.id} hover>
                      <TableCell sx={{ whiteSpace: 'nowrap' }}>{fmt(d.decided_at)}</TableCell>
                      <TableCell>
                        <Button size="small" sx={{ p: 0, minWidth: 0, textTransform: 'none', whiteSpace: 'nowrap' }}
                                onClick={() => navigate(`/situations/${d.situation_id}`)}>{d.situation_number}</Button>
                        <Box><RiskChip level={d.risk_level} score={d.risk_score} /></Box>
                      </TableCell>
                      <TableCell>{d.decided_by.name ?? 'A former user'}
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {ROLE_LABEL[d.decided_by.role_id] ?? `Role ${d.decided_by.role_id}`}</Typography></TableCell>
                      <TableCell>{d.suggested_action ? DECISION_LABEL[d.suggested_action] : '—'}</TableCell>
                      <TableCell>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>{DECISION_LABEL[d.action]}</Typography>
                        <Chip size="small" variant="outlined" color={d.is_override ? 'warning' : 'default'}
                              label={BASIS_LABEL[d.basis]} sx={{ height: 18, fontSize: '0.62rem' }} />
                        {d.reason && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          {d.reason}</Typography>}
                      </TableCell>
                      <TableCell>
                        <Chip size="small" variant="outlined" label={STATE_LABEL[d.state]}
                              color={d.state === 'REJECTED' ? 'error' : d.state === 'PENDING_APPROVAL' ? 'warning' : 'success'} />
                        {/* Under the state it answers, so it is never off the edge of a narrow screen. */}
                        {d.state === 'PENDING_APPROVAL' && canApprove && (
                          <Stack direction="row" sx={{ gap: 0.5, mt: 0.75 }}>
                            <Button size="small" variant="contained" onClick={() => setVerdict({ decision: d, approve: true })}>
                              Approve</Button>
                            <Button size="small" onClick={() => setVerdict({ decision: d, approve: false })}>Reject</Button>
                          </Stack>)}
                      </TableCell>
                      <TableCell>
                        {!d.actions.length ? <Typography variant="caption" color="text.secondary">Nothing</Typography>
                          : d.actions.map((a) => (
                            <Typography key={a.sequence} variant="caption" sx={{ display: 'block' }}
                                        color={a.result === 'FAILED' ? 'error.main' : 'text.secondary'}>
                              {STEP_LABEL[a.action] ?? a.action}{a.result === 'FAILED' ? ' — failed' : ''}
                              {a.result === 'SKIPPED' ? ' — nothing to do' : ''}</Typography>))}
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
      {verdict && <VerdictDialog {...verdict} onClose={() => setVerdict(null)} />}
    </Box>
  )
}

function VerdictDialog({ decision, approve, onClose }: { decision: Decision; approve: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [note, setNote] = useState('')
  const save = useMutation({
    mutationFn: () => (approve ? approveDecision(decision.id, note.trim()) : rejectDecision(decision.id, note.trim())),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['intel-decisions'] })
      qc.invalidateQueries({ queryKey: ['intel-situations'] })
      onClose()
    },
  })
  return (
    <Dialog open onClose={save.isPending ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{approve ? 'Approve' : 'Reject'}: {DECISION_LABEL[decision.action]}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 1 }}>
          Proposed by {decision.decided_by.name ?? 'a former user'}
          {' '}({ROLE_LABEL[decision.decided_by.role_id] ?? `Role ${decision.decided_by.role_id}`})
          {' '}for {decision.situation_number}{decision.reason ? ` — ${decision.reason}` : ''}.
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {approve
            ? 'It will be carried out now, under your permissions, and recorded as approved by you. The decision stays theirs.'
            : 'Nothing will be carried out, and the situation goes back to waiting for a decision.'}
        </Typography>
        <TextField fullWidth multiline minRows={2} size="small" value={note} onChange={(e) => setNote(e.target.value)}
                   label={approve ? 'Note (optional)' : 'Why is it rejected? (required)'}
                   slotProps={{ htmlInput: { maxLength: 2000 } }} />
        {save.error && <Alert severity="error" sx={{ mt: 2 }}>{apiError(save.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={save.isPending}>Cancel</Button>
        <Button variant="contained" color={approve ? 'primary' : 'error'} onClick={() => save.mutate()}
                disabled={save.isPending || (!approve && !note.trim())}>
          {save.isPending ? 'Saving…' : approve ? 'Approve and carry out' : 'Reject'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

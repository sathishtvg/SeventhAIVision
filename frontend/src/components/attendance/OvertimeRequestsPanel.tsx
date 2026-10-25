/**
 * Overtime requests waiting for a decision, and time worked past the planned end that no approval covers.
 *
 * Design: docs/plans/2026-10-25-roster-autoclose-overtime-design.md (sections 3 and 5). Only approved overtime is
 * paid, never more than the time worked past the planned end; a rejection says why, and the guard is told.
 * Shown to whoever may decide (attendance:manage).
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Skeleton, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  approveOvertime, getOvertimeRequests, getUnapprovedExtra, rejectOvertime, type OvertimeRequest,
} from '@/api/overtime'
import { GlassCard } from '@/components/common/GlassCard'
import Stack from '@/components/common/Stack'
import { ErrorState, errorText } from '@/components/states'
import { useTenantTimeZone } from '@/lib/tenantTime'

const when = (iso: string | null, timeZone?: string) =>
  iso ? new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short', timeZone }) : '-'

function RejectDialog({ request, onClose }: { request: OvertimeRequest; onClose: () => void }) {
  const queryClient = useQueryClient()
  const [note, setNote] = useState('')
  const reject = useMutation({
    mutationFn: () => rejectOvertime(request.id, note.trim()),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['overtime'] }); onClose() },
  })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Reject {request.hours_requested} h overtime</DialogTitle>
      <DialogContent sx={{ pt: 1 }}>
        <Typography variant="body2" sx={{ mb: 1.5 }}>
          {request.guard_name ?? 'Guard'} asked: "{request.reason}"
        </Typography>
        <TextField
          autoFocus fullWidth multiline minRows={2} label="Why (the guard is told)"
          value={note} onChange={(e) => setNote(e.target.value)}
        />
        {reject.error && <Alert severity="error" sx={{ mt: 1.5 }}>{errorText(reject.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button color="error" variant="contained" disabled={!note.trim() || reject.isPending} onClick={() => reject.mutate()}>
          Reject
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export function OvertimeRequestsPanel() {
  const timeZone = useTenantTimeZone()
  const queryClient = useQueryClient()
  const [rejecting, setRejecting] = useState<OvertimeRequest | null>(null)
  const pending = useQuery({ queryKey: ['overtime', 'pending'], queryFn: () => getOvertimeRequests('pending') })
  const extra = useQuery({ queryKey: ['overtime', 'unapproved-extra'], queryFn: () => getUnapprovedExtra(14) })
  const approve = useMutation({
    mutationFn: (id: string) => approveOvertime(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['overtime'] }),
  })

  return (
    <GlassCard sx={{ p: 2, mt: 2 }}>
      <Typography variant="subtitle2" sx={{ fontWeight: 800, mb: 1 }}>
        Overtime requests{pending.data?.length ? ` (${pending.data.length} waiting)` : ''}
      </Typography>
      {pending.isLoading ? <Skeleton height={56} /> : pending.isError ? (
        <ErrorState compact error={pending.error} onRetry={pending.refetch} />
      ) : !pending.data?.length ? (
        <Typography variant="body2" color="text.secondary">No overtime request is waiting.</Typography>
      ) : (
        <Stack spacing={0.75}>
          {pending.data.map((r) => (
            <Stack key={r.id} direction="row" spacing={1.5} alignItems="center"
                   sx={{ px: 1, py: 0.75, borderRadius: 1, background: 'rgba(255,255,255,0.03)' }}>
              <Chip size="small" color="warning" label={`${r.hours_requested} h`} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>
                  {r.guard_name ?? 'Guard'} · {r.site_name ?? 'No site'}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  "{r.reason}" · shift planned to end {when(r.scheduled_end, timeZone)}
                  {r.shift_status === 'completed' && ` · ended ${when(r.actual_end, timeZone)}`}
                  {r.auto_closed_at && ' (closed by the system)'}
                </Typography>
              </Box>
              <Button size="small" color="success" variant="outlined" disabled={approve.isPending}
                      onClick={() => approve.mutate(r.id)}>
                Approve
              </Button>
              <Button size="small" color="error" onClick={() => setRejecting(r)}>Reject</Button>
            </Stack>
          ))}
        </Stack>
      )}
      {approve.error && <Alert severity="error" sx={{ mt: 1 }}>{errorText(approve.error)}</Alert>}

      {!!extra.data?.length && (
        <Box sx={{ mt: 2 }}>
          <Typography variant="caption" sx={{ fontWeight: 700, display: 'block', mb: 0.5 }}>
            Unapproved extra time, last 14 days (not paid unless overtime is approved)
          </Typography>
          <Stack spacing={0.5}>
            {extra.data.map((x) => (
              <Typography key={x.shift_id} variant="caption" color="text.secondary">
                {x.guard_name ?? 'Guard'} · {x.site_name ?? 'No site'} · {Math.round(x.unapproved_extra_minutes)} min past the
                planned end ({when(x.scheduled_end, timeZone)}){x.auto_closed_at ? ' · closed by the system' : ''}
              </Typography>
            ))}
          </Stack>
        </Box>
      )}
      {rejecting && <RejectDialog request={rejecting} onClose={() => setRejecting(null)} />}
    </GlassCard>
  )
}

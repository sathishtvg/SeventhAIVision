/**
 * Cases that escalate (4 November 2026; Phase 3 of the known limits): a case's escalations, and the organisation's
 * timings. The server decides what is due and who is told (backend/app/services/case_escalation.py); this shows it.
 * Changing the timings is offered only when the server says the reader may (`may_set`).
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle, Skeleton, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { ErrorState, errorText } from '@/components/states'
import { getCaseEscalations, getEscalationSettings, setEscalationSettings } from '@/api/cases'
import type { EscalationSettings, EscalationTimings } from '@/api/cases'
import { fmt } from '@/components/cases/caseFormat'

/** A case's escalations, newest first. */
export function CaseEscalations({ caseId }: { caseId: string }) {
  const { data, isLoading, isLoadingError: escalationsFailed, error: escalationsError, refetch: refetchEscalations } =
    useQuery({ queryKey: ['case-escalations', caseId], queryFn: () => getCaseEscalations(caseId) })
  if (isLoading) return <Skeleton height={40} />
  if (escalationsFailed || !data) return <ErrorState compact error={escalationsError} onRetry={refetchEscalations} />
  if (!data.escalations.length) {
    return <Typography variant="caption" color="text.secondary">{data.note}</Typography>
  }
  return (
    <Box data-testid="case-escalations">
      {data.escalations.map((e) => (
        <Box key={e.id} data-testid="escalation" sx={{ mb: 0.5 }}>
          <Typography variant="caption" color="text.secondary">
            {`${fmt(e.sent_at)} · ${e.kind_label}${e.step === 2 ? ' · to Managers and Admins' : ''}`}
            {e.addressed === 0 ? ' · nobody could be told' : ` · ${e.addressed} told`}
          </Typography>
          <Typography variant="body2">{e.message}</Typography>
        </Box>
      ))}
    </Box>
  )
}

const FIELDS: { key: keyof EscalationTimings; label: string; unit: string }[] = [
  { key: 'quiet_high_days', label: 'A high priority case is quiet after', unit: 'days' },
  { key: 'quiet_normal_days', label: 'A normal priority case is quiet after', unit: 'days' },
  { key: 'quiet_low_days', label: 'A low priority case is quiet after', unit: 'days' },
  { key: 'overdue_hours', label: 'Managers hear of an overdue task after', unit: 'hours' },
  { key: 'close_wait_days', label: 'A close request is waiting too long after', unit: 'days' },
]

function TimingsForm({ data, onClose }: { data: EscalationSettings; onClose: () => void }) {
  const qc = useQueryClient()
  const [values, setValues] = useState<Record<keyof EscalationTimings, string>>(
    () => Object.fromEntries(FIELDS.map((f) => [f.key, String(data[f.key])])) as Record<keyof EscalationTimings, string>)
  const parsed = Object.fromEntries(FIELDS.map((f) => [f.key, Number(values[f.key])])) as unknown as EscalationTimings
  const valid = FIELDS.every((f) => {
    const n = parsed[f.key]
    return /^\d+$/.test(values[f.key].trim()) && n >= data.limits[f.key].least && n <= data.limits[f.key].most
  })
  const save = useMutation({
    mutationFn: () => setEscalationSettings(parsed),
    onSuccess: (saved) => { qc.setQueryData(['case-escalation-settings'], saved); onClose() },
  })
  const editable = data.may_set
  return (
    <>
      <DialogTitle>When cases escalate</DialogTitle>
      <DialogContent>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          An overdue task tells its assignee and the case lead, then Managers and Admins. A quiet case tells its lead,
          then Managers and Admins {data.quiet_second_days} days later. A waiting close request tells whoever may
          approve it. Nothing about a case is changed.
        </Typography>
        <Stack sx={{ gap: 1.5 }}>
          {FIELDS.map((f) => (
            <TextField key={f.key} size="small" label={f.label} value={values[f.key]} disabled={!editable || save.isPending}
                       onChange={(e) => setValues({ ...values, [f.key]: e.target.value })}
                       helperText={`${f.unit}, ${data.limits[f.key].least} to ${data.limits[f.key].most} (default ${data.defaults[f.key]})`}
                       slotProps={{ htmlInput: { inputMode: 'numeric' } }} />
          ))}
        </Stack>
        {data.updated_by_name && data.updated_at && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
            {`Last changed by ${data.updated_by_name}, ${fmt(data.updated_at)}`}
          </Typography>
        )}
        {!editable && (
          <Alert severity="info" sx={{ mt: 2 }}>Only whoever may change the organisation's settings changes these.</Alert>
        )}
        {save.error && <Alert severity="error" sx={{ mt: 2 }}>{errorText(save.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={save.isPending}>{editable ? 'Cancel' : 'Close'}</Button>
        {editable && (
          <Button variant="contained" disabled={!valid || save.isPending} onClick={() => save.mutate()}>Save</Button>
        )}
      </DialogActions>
    </>
  )
}

/** The organisation's escalation timings, in a dialog. */
export function EscalationTimingsDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { data, isLoading, isLoadingError: settingsFailed, error: settingsError, refetch: refetchSettings } = useQuery({
    queryKey: ['case-escalation-settings'], queryFn: getEscalationSettings, enabled: open,
  })
  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      {isLoading ? <DialogContent><Skeleton height={200} /></DialogContent>
        : settingsFailed || !data ? <DialogContent><ErrorState compact error={settingsError} onRetry={refetchSettings} /></DialogContent>
          : <TimingsForm data={data} onClose={onClose} />}
    </Dialog>
  )
}

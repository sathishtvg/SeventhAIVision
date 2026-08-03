import { useState } from 'react'
import {
  Box,
  Button,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Alert as MuiAlert,
  List,
  ListItemButton,
  ListItemText,
  Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import CampaignIcon from '@mui/icons-material/Campaign'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  acknowledgeAlert, addAlertNote, assignAlert, dismissAlert,
  getEscalationTargets, markFalsePositive,
} from '@/api/alerts'

export interface AlertSummary {
  id: string
  title: string
  severity: string
  module_type?: string | null
  site_name?: string | null
  camera_name?: string | null
  created_at?: string | null
}

const SEV_COLOR: Record<string, string> = {
  critical: '#FF4560', high: '#FF9800', medium: '#FFC107', low: '#00E396', info: '#6C63FF',
}

const MODULE_LABEL: Record<string, string> = {
  lpr: 'LPR', face: 'Face', intrusion: 'Intrusion', ppe: 'PPE',
  crowd: 'Crowd', fire_smoke: 'Fire/Smoke', weapon: 'Weapon',
  behavior: 'Behavior', tampering: 'Tamper', abandoned: 'Abandoned', fall: 'Fall',
}

const ROLE_LABEL: Record<number, string> = {
  1: 'Super Admin', 2: 'Admin', 3: 'Supervisor', 4: 'Operator', 5: 'Security Guard',
}

function relativeTime(iso: string | null | undefined): string {
  if (!iso) return 'N/A'
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  return `${Math.floor(diff / 3600)}h ago`
}

interface AlertResponseDialogProps {
  alert: AlertSummary
  onClose: () => void
  /** Called after any successful action (ack / resolve / escalate) so the
   * caller can invalidate its own alert list/feed. Dialog closes itself. */
  onResolved: () => void
}

export function AlertResponseDialog({ alert, onClose, onResolved }: AlertResponseDialogProps) {
  const qc = useQueryClient()
  const [escalating, setEscalating] = useState(false)
  const sevColor = SEV_COLOR[alert.severity] ?? '#6C63FF'

  const invalidateAndClose = () => {
    qc.invalidateQueries({ queryKey: ['alert-notes', alert.id] })
    onResolved()
  }

  const { mutate: ack, isPending: ackPending } = useMutation({
    mutationFn: () => acknowledgeAlert(alert.id),
    onSuccess: invalidateAndClose,
  })
  const { mutate: markFp, isPending: fpPending } = useMutation({
    mutationFn: () => markFalsePositive(alert.id),
    onSuccess: invalidateAndClose,
  })
  const { mutate: resolve, isPending: resolvePending } = useMutation({
    mutationFn: () => dismissAlert(alert.id),
    onSuccess: invalidateAndClose,
  })

  const { data: targets = [], isLoading: targetsLoading } = useQuery({
    queryKey: ['escalation-targets', alert.id],
    queryFn: () => getEscalationTargets(alert.id),
    enabled: escalating,
  })

  const { mutate: escalateTo, isPending: escalatePending } = useMutation({
    mutationFn: async (target: { user_id: string; full_name: string | null; role_id: number }) => {
      await assignAlert(alert.id, target.user_id)
      const label = `${target.full_name ?? 'user'} (${ROLE_LABEL[target.role_id] ?? 'staff'})`
      await addAlertNote(alert.id, `Escalated to ${label}`)
    },
    onSuccess: invalidateAndClose,
  })

  const busy = ackPending || fpPending || resolvePending || escalatePending

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <Box sx={{ width: 10, height: 10, borderRadius: '50%', bgcolor: sevColor, flexShrink: 0 }} />
        Alert Detail
      </DialogTitle>
      <DialogContent>
        <MuiAlert severity={alert.severity === 'critical' || alert.severity === 'high' ? 'error' : 'warning'} sx={{ mb: 2 }}>
          {alert.title}
        </MuiAlert>
        <Stack spacing={0.75}>
          {alert.module_type && (
            <Stack direction="row" justifyContent="space-between">
              <Typography variant="caption" color="text.secondary">Module</Typography>
              <Chip label={MODULE_LABEL[alert.module_type] ?? alert.module_type} size="small" />
            </Stack>
          )}
          <Stack direction="row" justifyContent="space-between">
            <Typography variant="caption" color="text.secondary">Site</Typography>
            <Typography variant="caption">{alert.site_name ?? '—'}</Typography>
          </Stack>
          <Stack direction="row" justifyContent="space-between">
            <Typography variant="caption" color="text.secondary">Camera</Typography>
            <Typography variant="caption">{alert.camera_name ?? '—'}</Typography>
          </Stack>
          <Stack direction="row" justifyContent="space-between">
            <Typography variant="caption" color="text.secondary">Time</Typography>
            <Typography variant="caption">{relativeTime(alert.created_at)}</Typography>
          </Stack>
        </Stack>

        {escalating && (
          <Box sx={{ mt: 2, pt: 2, borderTop: '1px solid rgba(255,255,255,0.08)' }}>
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.5 }}>
              Escalate to on-duty / site staff
            </Typography>
            {targetsLoading ? (
              <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}><CircularProgress size={20} /></Box>
            ) : targets.length === 0 ? (
              <Typography variant="caption" color="text.disabled">No staff available to escalate to.</Typography>
            ) : (
              <List dense sx={{ maxHeight: 200, overflowY: 'auto' }}>
                {targets.map((t) => (
                  <ListItemButton key={t.user_id} disabled={escalatePending} onClick={() => escalateTo(t)}>
                    <ListItemText primary={t.full_name ?? 'Unnamed user'} secondary={ROLE_LABEL[t.role_id] ?? `Role ${t.role_id}`} />
                  </ListItemButton>
                ))}
              </List>
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions sx={{ flexWrap: 'wrap' }}>
        <Button onClick={onClose} disabled={busy}>Close</Button>
        <Button
          color="secondary"
          startIcon={<CampaignIcon />}
          onClick={() => setEscalating((v) => !v)}
          disabled={busy}
        >
          Escalate
        </Button>
        <Button color="warning" onClick={() => markFp()} disabled={busy}>
          Mark False Positive
        </Button>
        <Button color="success" onClick={() => resolve()} disabled={busy}>
          Resolve
        </Button>
        <Button variant="contained" startIcon={<CheckCircleIcon />} onClick={() => ack()} disabled={busy}>
          Acknowledge
        </Button>
      </DialogActions>
    </Dialog>
  )
}

import React, { useState } from 'react'
import {
  Box, Typography, Button, Table, TableBody, TableCell,
  TableHead, TableRow, Chip, Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, MenuItem, Switch, FormControlLabel, IconButton, Tooltip,
  Collapse, Alert, CircularProgress,
} from '@mui/material'
import {
  Add as AddIcon,
  PlayArrow as RunIcon,
  Delete as DeleteIcon,
  ExpandMore as ExpandIcon,
  ExpandLess as CollapseIcon,
} from '@mui/icons-material'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  listSchedules, createSchedule, updateSchedule, deleteSchedule, listDeliveries, runNow,
  REPORT_TYPES, FREQUENCIES, DELIVERY_METHODS, DAYS_OF_WEEK,
} from '@/api/scheduled_reports'
import type { ScheduleCreate } from '@/api/scheduled_reports'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'

const STATUS_COLORS: Record<string, 'success' | 'error' | 'warning'> = {
  success: 'success',
  failed: 'error',
  pending: 'warning',
}

export default function ScheduledReports() {
  const qc = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [expandedId, setExpandedId] = useState<string | null>(null)
  const [successMsg, setSuccessMsg] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const { data: schedules = [], isLoading } = useQuery({
    queryKey: ['scheduled-reports'],
    queryFn: listSchedules,
  })

  const { data: sites = [] } = useQuery({
    queryKey: ['sites'],
    queryFn: () => getSites(),
  })

  const createMut = useMutation({
    mutationFn: createSchedule,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scheduled-reports'] })
      setDialogOpen(false)
      setSuccessMsg('Schedule created.')
    },
    onError: (e: any) => setErrorMsg(e?.response?.data?.detail || 'Create failed'),
  })

  const toggleMut = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      updateSchedule(id, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['scheduled-reports'] }),
  })

  const deleteMut = useMutation({
    mutationFn: deleteSchedule,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['scheduled-reports'] })
      setSuccessMsg('Schedule deleted.')
    },
  })

  const runMut = useMutation({
    mutationFn: runNow,
    onSuccess: () => setSuccessMsg('Report queued for delivery.'),
    onError: (e: any) => setErrorMsg(e?.response?.data?.detail || 'Run failed'),
  })

  return (
    <Box>
      <PageHeader pageKey="scheduled-reports" />
      <Box sx={{ display: 'flex', alignItems: 'center', mb: 2, gap: 2 }}>
        <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setDialogOpen(true)}>
          New Schedule
        </Button>
      </Box>

      {successMsg && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setSuccessMsg(null)}>{successMsg}</Alert>}
      {errorMsg && <Alert severity="error" sx={{ mb: 2 }} onClose={() => setErrorMsg(null)}>{errorMsg}</Alert>}

      <GlassCard>
        {isLoading ? (
          <Box sx={{ p: 4, textAlign: 'center' }}><CircularProgress /></Box>
        ) : (
          <Table>
            <TableHead>
              <TableRow>
                <TableCell />
                <TableCell>Name</TableCell>
                <TableCell>Type</TableCell>
                <TableCell>Frequency</TableCell>
                <TableCell>Delivery</TableCell>
                <TableCell>Next Run</TableCell>
                <TableCell>Last Run</TableCell>
                <TableCell>Active</TableCell>
                <TableCell>Actions</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {schedules.length === 0 && (
                <TableRow>
                  <TableCell colSpan={9} sx={{ textAlign: 'center', color: 'text.secondary', py: 4 }}>
                    No schedules yet. Create one to start automating report delivery.
                  </TableCell>
                </TableRow>
              )}
              {schedules.map((s) => (
                <React.Fragment key={s.id}>
                  <TableRow hover>
                    <TableCell>
                      <IconButton size="small" onClick={() =>
                        setExpandedId(expandedId === s.id ? null : s.id)
                      }>
                        {expandedId === s.id ? <CollapseIcon /> : <ExpandIcon />}
                      </IconButton>
                    </TableCell>
                    <TableCell>{s.name}</TableCell>
                    <TableCell>
                      <Chip label={s.report_type.replace('_', ' ')} size="small" color="primary" variant="outlined" />
                    </TableCell>
                    <TableCell sx={{ textTransform: 'capitalize' }}>{s.frequency}</TableCell>
                    <TableCell>
                      <Chip
                        label={s.delivery_method}
                        size="small"
                        color={s.delivery_method === 'email' ? 'info' : 'secondary'}
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell>
                      {s.next_run_at ? new Date(s.next_run_at).toLocaleString() : '—'}
                    </TableCell>
                    <TableCell>
                      {s.last_run_at ? new Date(s.last_run_at).toLocaleString() : 'Never'}
                    </TableCell>
                    <TableCell>
                      <Switch
                        checked={s.is_active}
                        size="small"
                        onChange={(e) => toggleMut.mutate({ id: s.id, is_active: e.target.checked })}
                      />
                    </TableCell>
                    <TableCell>
                      <Tooltip title="Run now">
                        <IconButton size="small" onClick={() => runMut.mutate(s.id)}
                          disabled={runMut.isPending}>
                          <RunIcon />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete">
                        <IconButton size="small" color="error"
                          onClick={() => { if (confirm(`Delete "${s.name}"?`)) deleteMut.mutate(s.id) }}>
                          <DeleteIcon />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                  <TableRow>
                    <TableCell colSpan={9} sx={{ py: 0 }}>
                      <Collapse in={expandedId === s.id}>
                        <DeliveryHistory scheduleId={s.id} />
                      </Collapse>
                    </TableCell>
                  </TableRow>
                </React.Fragment>
              ))}
            </TableBody>
          </Table>
        )}
      </GlassCard>

      <CreateScheduleDialog
        open={dialogOpen}
        onClose={() => setDialogOpen(false)}
        onSubmit={(data) => createMut.mutate(data)}
        loading={createMut.isPending}
        sites={sites}
      />
    </Box>
  )
}

function DeliveryHistory({ scheduleId }: { scheduleId: string }) {
  const { data: deliveries = [], isLoading } = useQuery({
    queryKey: ['schedule-deliveries', scheduleId],
    queryFn: () => listDeliveries(scheduleId),
    enabled: true,
  })

  if (isLoading) return <Box sx={{ p: 1 }}><CircularProgress size={20} /></Box>
  if (deliveries.length === 0) return (
    <Box sx={{ p: 2, color: 'text.secondary', fontSize: '0.85rem' }}>No delivery history yet.</Box>
  )

  return (
    <Box sx={{ px: 2, py: 1 }}>
      <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600, mb: 1, display: 'block' }}>
        Recent Deliveries
      </Typography>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Status</TableCell>
            <TableCell>Delivered At</TableCell>
            <TableCell>Period</TableCell>
            <TableCell>Error</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {deliveries.slice(0, 10).map((d) => (
            <TableRow key={d.id}>
              <TableCell>
                <Chip
                  label={d.status}
                  size="small"
                  color={STATUS_COLORS[d.status] || 'default'}
                />
              </TableCell>
              <TableCell>
                {d.delivered_at ? new Date(d.delivered_at).toLocaleString() : '—'}
              </TableCell>
              <TableCell sx={{ fontSize: '0.75rem' }}>
                {d.report_period_start
                  ? `${d.report_period_start.slice(0, 10)} → ${d.report_period_end?.slice(0, 10) ?? '?'}`
                  : '—'}
              </TableCell>
              <TableCell sx={{ color: 'error.main', fontSize: '0.75rem', maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                {d.error_message || '—'}
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </Box>
  )
}

interface CreateDialogProps {
  open: boolean
  onClose: () => void
  onSubmit: (data: ScheduleCreate) => void
  loading: boolean
  sites: Array<{ id: string; name: string }>
}

function CreateScheduleDialog({ open, onClose, onSubmit, loading, sites }: CreateDialogProps) {
  const [form, setForm] = useState<ScheduleCreate>({
    name: '',
    report_type: 'site_summary',
    frequency: 'daily',
    hour_utc: 8,
    delivery_method: 'email',
    recipients: [],
    is_active: true,
  })
  const [recipientInput, setRecipientInput] = useState('')

  const set = (k: keyof ScheduleCreate, v: any) => setForm((f) => ({ ...f, [k]: v }))

  const handleSubmit = () => {
    const data = { ...form }
    if (recipientInput.trim()) {
      data.recipients = [...(form.recipients || []), recipientInput.trim()]
    }
    onSubmit(data)
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>New Report Schedule</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
        <TextField label="Name" value={form.name} onChange={(e) => set('name', e.target.value)}
          required fullWidth />

        <TextField label="Report Type" select value={form.report_type}
          onChange={(e) => set('report_type', e.target.value)} fullWidth>
          {REPORT_TYPES.map((t) => (
            <MenuItem key={t} value={t}>{t.replace(/_/g, ' ')}</MenuItem>
          ))}
        </TextField>

        <TextField label="Site (optional)" select value={form.site_id ?? ''}
          onChange={(e) => set('site_id', e.target.value || null)} fullWidth>
          <MenuItem value="">All Sites</MenuItem>
          {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
        </TextField>

        <TextField label="Frequency" select value={form.frequency}
          onChange={(e) => set('frequency', e.target.value)} fullWidth>
          {FREQUENCIES.map((f) => (
            <MenuItem key={f} value={f} sx={{ textTransform: 'capitalize' }}>{f}</MenuItem>
          ))}
        </TextField>

        {form.frequency === 'weekly' && (
          <TextField label="Day of Week" select value={form.day_of_week ?? 0}
            onChange={(e) => set('day_of_week', Number(e.target.value))} fullWidth>
            {DAYS_OF_WEEK.map((d, i) => <MenuItem key={i} value={i}>{d}</MenuItem>)}
          </TextField>
        )}

        {form.frequency === 'monthly' && (
          <TextField label="Day of Month (1-28)" type="number"
            value={form.day_of_month ?? 1}
            onChange={(e) => set('day_of_month', Math.min(28, Math.max(1, Number(e.target.value))))}
 fullWidth slotProps={{ htmlInput: { min: 1, max: 28 } }} />
        )}

        <TextField label="Hour UTC (0-23)" type="number" value={form.hour_utc ?? 8}
          onChange={(e) => set('hour_utc', Math.min(23, Math.max(0, Number(e.target.value))))}
 fullWidth slotProps={{ htmlInput: { min: 0, max: 23 } }} />

        <TextField label="Delivery Method" select value={form.delivery_method}
          onChange={(e) => set('delivery_method', e.target.value)} fullWidth>
          {DELIVERY_METHODS.map((m) => <MenuItem key={m} value={m}>{m}</MenuItem>)}
        </TextField>

        {form.delivery_method === 'email' && (
          <Box>
            <TextField
              label="Recipient Email (press Enter to add)"
              value={recipientInput}
              onChange={(e) => setRecipientInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && recipientInput.trim()) {
                  set('recipients', [...(form.recipients || []), recipientInput.trim()])
                  setRecipientInput('')
                }
              }}
              fullWidth
              helperText={`Recipients: ${(form.recipients || []).join(', ') || 'none'}`}
            />
          </Box>
        )}

        {form.delivery_method === 'webhook' && (
          <TextField label="Webhook URL" value={form.webhook_url ?? ''}
            onChange={(e) => set('webhook_url', e.target.value)} fullWidth />
        )}

        <FormControlLabel
          control={<Switch checked={form.is_active} onChange={(e) => set('is_active', e.target.checked)} />}
          label="Active immediately"
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={handleSubmit} disabled={loading || !form.name}>
          {loading ? <CircularProgress size={18} /> : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

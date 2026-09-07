import { useState } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  Box,
  Chip,
  Tooltip,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Button,
  Skeleton,
  Paper,
  Divider,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Checkbox,
  Drawer,
  IconButton,
  List,
  ListItem,
  ListItemText,
  CircularProgress,
  FormControl,
  InputLabel,
  Select,
  MenuItem,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import CloseIcon from '@mui/icons-material/Close'
import PersonAddIcon from '@mui/icons-material/PersonAdd'
import PhoneIphoneIcon from '@mui/icons-material/PhoneIphone'
import ComputerIcon from '@mui/icons-material/Computer'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import { SeverityChip } from '@/components/common/SeverityChip'
import { StatusChip } from '@/components/common/StatusChip'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { getAlerts, acknowledgeAlert, markFalsePositive, bulkAcknowledgeAlerts, bulkDismissAlerts, getAlertNotes, addAlertNote, assignAlert, unassignAlert } from '@/api/alerts'
import { getUsers } from '@/api/users'
import { getSites } from '@/api/sites'
import type { AlertSeverity } from '@/types/api'
import { MODULE_LABELS } from '@/api/licenses'

const STATUS_FILTERS = ['all', 'open', 'acknowledged', 'resolved', 'dismissed'] as const
type StatusFilter = (typeof STATUS_FILTERS)[number]

// ── Alert detail drawer (notes + assignment) ──────────────────────────────────

function AlertDetailDrawer({ alertId, alert, onClose }: { alertId: string; alert: any; onClose: () => void }) {
  const qc = useQueryClient()
  const [newNote, setNewNote] = useState('')
  const [assignDialog, setAssignDialog] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState('')

  const { data: notes, isLoading: notesLoading } = useQuery({
    queryKey: ['alert-notes', alertId],
    queryFn: () => getAlertNotes(alertId),
    enabled: !!alertId,
  })

  const { data: users = [] } = useQuery({
    queryKey: ['users'],
    queryFn: getUsers,
    enabled: assignDialog,
  })

  const addNote = useMutation({
    mutationFn: () => addAlertNote(alertId, newNote),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['alert-notes', alertId] }); setNewNote('') },
  })

  const assign = useMutation({
    mutationFn: () => assignAlert(alertId, selectedUserId),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['alerts'] }); setAssignDialog(false) },
  })

  const unassign = useMutation({
    mutationFn: () => unassignAlert(alertId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['alerts'] }),
  })

  return (
    <Drawer anchor="right" open={!!alertId} onClose={onClose} slotProps={{ paper: { sx: { width: 400, bgcolor: 'rgba(8,8,24,0.95)', backdropFilter: 'blur(20px)', p: 2 } } }}>
      <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
        <Typography variant="h6" sx={{ fontWeight: 600 }}>Alert Detail</Typography>
        <IconButton onClick={onClose} size="small"><CloseIcon /></IconButton>
      </Stack>

      {alert && (
        <Box sx={{ mb: 2 }}>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>{alert.title}</Typography>
          {alert.message && <Typography variant="caption" color="text.secondary">{alert.message}</Typography>}
          <Stack direction="row" spacing={1} sx={{ mt: 1 }}>
            <SeverityChip severity={alert.severity as AlertSeverity} />
            <StatusChip status={alert.status} />
          </Stack>
          {alert.acknowledged_at && (
            <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 1 }}>
              {alert.acknowledged_via === 'mobile' ? <PhoneIphoneIcon sx={{ fontSize: 14, color: 'text.secondary' }} /> : <ComputerIcon sx={{ fontSize: 14, color: 'text.secondary' }} />}
              <Typography variant="caption" color="text.secondary">
                Acknowledged by {alert.acknowledged_by_name ?? 'unknown'} via {alert.acknowledged_via ?? 'web'} · {new Date(alert.acknowledged_at).toLocaleString()}
              </Typography>
            </Stack>
          )}
          {alert.fp_marked_at && (
            <Stack direction="row" alignItems="center" spacing={0.75} sx={{ mt: 0.5 }}>
              {alert.fp_marked_via === 'mobile' ? <PhoneIphoneIcon sx={{ fontSize: 14, color: 'text.secondary' }} /> : <ComputerIcon sx={{ fontSize: 14, color: 'text.secondary' }} />}
              <Typography variant="caption" color="text.secondary">
                Marked false positive by {alert.fp_marked_by_name ?? 'unknown'} via {alert.fp_marked_via ?? 'web'} · {new Date(alert.fp_marked_at).toLocaleString()}
              </Typography>
            </Stack>
          )}
        </Box>
      )}

      <Divider sx={{ mb: 2 }} />

      {/* Assignment */}
      <Box sx={{ mb: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>Assignment</Typography>
        {alert?.assigned_to_name ? (
          <Stack direction="row" alignItems="center" spacing={1}>
            <Chip label={alert.assigned_to_name} size="small" color="primary" variant="outlined" />
            <PermissionGuard permission="alert:acknowledge">
              <Button size="small" onClick={() => unassign.mutate()} disabled={unassign.isPending}>Unassign</Button>
            </PermissionGuard>
          </Stack>
        ) : (
          <PermissionGuard permission="alert:acknowledge">
            <Button size="small" startIcon={<PersonAddIcon />} variant="outlined" onClick={() => setAssignDialog(true)}>
              Assign to user
            </Button>
          </PermissionGuard>
        )}
      </Box>

      <Divider sx={{ mb: 2 }} />

      {/* Notes */}
      <Typography variant="subtitle2" sx={{ mb: 1 }}>Notes</Typography>
      {notesLoading ? (
        <CircularProgress size={20} />
      ) : (
        <List dense disablePadding sx={{ mb: 2, maxHeight: 300, overflowY: 'auto' }}>
          {notes?.length === 0 && (
            <Typography variant="caption" color="text.secondary">No notes yet</Typography>
          )}
          {notes?.map((n) => (
            <ListItem key={n.id} alignItems="flex-start" disablePadding sx={{ mb: 1 }}>
              <ListItemText
                primary={n.note}
                secondary={`${n.author_name ?? n.author_email ?? 'System'} · ${new Date(n.created_at).toLocaleString()}`}
                slotProps={{ primary: { variant: 'body2' }, secondary: { variant: 'caption' } }}
              />
            </ListItem>
          ))}
        </List>
      )}

      <PermissionGuard permission="alert:acknowledge">
        <Stack direction="row" spacing={1} alignItems="flex-start">
          <TextField
            size="small" multiline rows={2} fullWidth
            placeholder="Add a note…" value={newNote}
            onChange={(e) => setNewNote(e.target.value)}
          />
          <Button variant="contained" size="small" onClick={() => addNote.mutate()} disabled={!newNote.trim() || addNote.isPending} sx={{ mt: 0.5, whiteSpace: 'nowrap' }}>
            Add
          </Button>
        </Stack>
      </PermissionGuard>

      {/* Assign dialog */}
      <Dialog open={assignDialog} onClose={() => setAssignDialog(false)} maxWidth="xs" fullWidth>
        <DialogTitle>Assign Alert</DialogTitle>
        <DialogContent>
          <FormControl fullWidth sx={{ mt: 1 }}>
            <InputLabel>User</InputLabel>
            <Select value={selectedUserId} label="User" onChange={(e) => setSelectedUserId(e.target.value)}>
              {(users as any[]).map((u) => (
                <MenuItem key={u.id} value={u.id}>{u.full_name || u.email}</MenuItem>
              ))}
            </Select>
          </FormControl>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setAssignDialog(false)}>Cancel</Button>
          <Button variant="contained" onClick={() => assign.mutate()} disabled={!selectedUserId || assign.isPending}>Assign</Button>
        </DialogActions>
      </Dialog>
    </Drawer>
  )
}

// Need FormControl/InputLabel/Select in the drawer — add them to imports
// (They were already imported by the FP dialog above, so no import change needed)

export default function Alerts() {
  const [searchParams] = useSearchParams()
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('open')
  const [siteFilter, setSiteFilter] = useState<string>(() => searchParams.get('site_id') ?? '')
  const [moduleFilter, setModuleFilter] = useState<string>('')
  const [fpDialog, setFpDialog] = useState<{ alertId: string } | null>(null)
  const [fpReason, setFpReason] = useState('')
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const [detailAlert, setDetailAlert] = useState<any | null>(null)
  const queryClient = useQueryClient()

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const { data: alerts, isLoading } = useQuery({
    queryKey: ['alerts', statusFilter, siteFilter, moduleFilter],
    queryFn: () =>
      getAlerts(
        statusFilter === 'all' ? undefined : statusFilter,
        siteFilter || undefined,
        moduleFilter || undefined,
      ),
  })

  const { mutate: acknowledge } = useMutation({
    mutationFn: acknowledgeAlert,
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['alerts'] }) },
  })

  const { mutate: flagFP, isPending: fpPending } = useMutation({
    mutationFn: ({ alertId, reason }: { alertId: string; reason: string }) =>
      markFalsePositive(alertId, reason || undefined),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['alerts'] })
      setFpDialog(null)
      setFpReason('')
    },
  })

  const { mutate: bulkAck, isPending: bulkAckPending } = useMutation({
    mutationFn: (ids: string[]) => bulkAcknowledgeAlerts(ids),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['alerts'] }); setSelectedIds(new Set()) },
  })

  const { mutate: bulkDismiss, isPending: bulkDismissPending } = useMutation({
    mutationFn: (ids: string[]) => bulkDismissAlerts(ids),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['alerts'] }); setSelectedIds(new Set()) },
  })

  const allIds = alerts?.items?.map((a: any) => a.id) ?? []
  const allSelected = allIds.length > 0 && allIds.every((id: string) => selectedIds.has(id))
  const toggleAll = () => setSelectedIds(allSelected ? new Set() : new Set(allIds))
  const toggleOne = (id: string) => setSelectedIds(prev => { const s = new Set(prev); if (s.has(id)) s.delete(id); else s.add(id); return s })

  // Filters moved off the page and into the rail. Status keeps 'all' as its
  // neutral value rather than '' — it defaults to 'open', so treating 'open'
  // as neutral would leave the badge reading 0 while the list was in fact
  // narrowed, which is exactly the confusion the badge exists to prevent.
  const filterGroups: FilterGroup[] = [
    {
      key: 'status',
      label: 'Status',
      allValue: 'all',
      value: statusFilter,
      onChange: (v) => setStatusFilter(v as StatusFilter),
      options: STATUS_FILTERS.map((s) => ({
        value: s,
        label: s.charAt(0).toUpperCase() + s.slice(1),
      })),
    },
    ...((sites as any[]).length > 0 ? [{
      key: 'site',
      label: 'Site',
      value: siteFilter,
      onChange: setSiteFilter,
      options: [
        { value: '', label: 'All' },
        ...(sites as any[]).map((s) => ({ value: s.id, label: s.name })),
      ],
    }] : []),
    {
      key: 'module',
      label: 'Module',
      value: moduleFilter,
      onChange: setModuleFilter,
      options: [
        { value: '', label: 'All' },
        ...Object.entries(MODULE_LABELS).map(([value, label]) => ({ value, label })),
      ],
    },
  ]

  return (
    // Rail sits after the content so it lands on the right edge; its panel
    // opens leftward over the grid without moving anything.
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      {/* Bulk action bar */}
      {selectedIds.size > 0 && (
        <PermissionGuard permission="alert:acknowledge">
          <Paper sx={{ mb: 2, p: 1.5, display: 'flex', alignItems: 'center', gap: 1, bgcolor: 'rgba(108,99,255,0.12)', border: '1px solid rgba(108,99,255,0.3)' }}>
            <Typography variant="body2" sx={{ flex: 1 }}>
              {selectedIds.size} alert{selectedIds.size > 1 ? 's' : ''} selected
            </Typography>
            <Button size="small" variant="outlined" disabled={bulkAckPending || bulkDismissPending} onClick={() => bulkAck([...selectedIds])}>
              Ack All
            </Button>
            <Button size="small" variant="outlined" color="warning" disabled={bulkAckPending || bulkDismissPending} onClick={() => bulkDismiss([...selectedIds])}>
              Dismiss All
            </Button>
            <Button size="small" onClick={() => setSelectedIds(new Set())}>Clear</Button>
          </Paper>
        </PermissionGuard>
      )}

      <GlassCard>
        <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell padding="checkbox">
                  <Checkbox size="small" checked={allSelected} indeterminate={selectedIds.size > 0 && !allSelected} onChange={toggleAll} />
                </TableCell>
                <TableCell>Severity</TableCell>
                <TableCell>Title</TableCell>
                <TableCell>Site</TableCell>
                <TableCell>Module</TableCell>
                <TableCell>Assigned</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Time</TableCell>
                <TableCell align="right">Action</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {isLoading
                ? Array.from({ length: 8 }).map((_, i) => (
                    <TableRow key={i}>
                      {Array.from({ length: 9 }).map((__, j) => (
                        <TableCell key={j}><Skeleton /></TableCell>
                      ))}
                    </TableRow>
                  ))
                : !alerts?.items?.length
                ? (
                    <TableRow>
                      <TableCell colSpan={9} align="center" sx={{ py: 4 }}>
                        <Typography color="text.secondary">No alerts found</Typography>
                      </TableCell>
                    </TableRow>
                  )
                : alerts?.items?.map((alert: any) => (
                    <TableRow key={alert.id} hover selected={selectedIds.has(alert.id)} sx={{ cursor: 'pointer' }}>
                      <TableCell padding="checkbox" onClick={(e) => e.stopPropagation()}>
                        <Checkbox size="small" checked={selectedIds.has(alert.id)} onChange={() => toggleOne(alert.id)} />
                      </TableCell>
                      <TableCell onClick={() => setDetailAlert(alert)}>
                        <Stack direction="row" spacing={0.5} alignItems="center">
                          <SeverityChip severity={alert.severity as AlertSeverity} />
                          {alert.escalated_at && (
                            <Chip label="Escalated" size="small" sx={{ bgcolor: 'rgba(255,152,0,0.15)', color: 'warning.main', border: '1px solid', borderColor: 'warning.main', fontSize: '0.65rem' }} />
                          )}
                          {alert.correlation_id && (
                            <Chip label="Correlated" size="small" sx={{ bgcolor: 'rgba(156,39,176,0.15)', color: 'secondary.main', border: '1px solid', borderColor: 'secondary.main', fontSize: '0.65rem' }} />
                          )}
                        </Stack>
                      </TableCell>
                      <TableCell onClick={() => setDetailAlert(alert)}>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>
                          {alert.title}
                        </Typography>
                        {alert.message && (
                          <Typography variant="caption" color="text.secondary">
                            {alert.message}
                          </Typography>
                        )}
                        {alert.escalated_at && alert.original_severity && (
                          <Typography variant="caption" sx={{ display: 'block', color: 'warning.main' }}>
                            Escalated from {alert.original_severity}
                          </Typography>
                        )}
                      </TableCell>
                      <TableCell onClick={() => setDetailAlert(alert)}>
                        {alert.site_name ? (
                          <Chip label={alert.site_name} size="small" color="info" variant="outlined" />
                        ) : (
                          <Typography variant="caption" color="text.disabled">—</Typography>
                        )}
                      </TableCell>
                      <TableCell onClick={() => setDetailAlert(alert)}>
                        <Chip label={MODULE_LABELS[alert.module_type as keyof typeof MODULE_LABELS] ?? alert.module_type} size="small" variant="outlined" />
                      </TableCell>
                      <TableCell onClick={() => setDetailAlert(alert)}>
                        {alert.assigned_to_name ? (
                          <Chip label={alert.assigned_to_name} size="small" color="primary" variant="outlined" />
                        ) : (
                          <Typography variant="caption" color="text.disabled">—</Typography>
                        )}
                      </TableCell>
                      <TableCell onClick={() => setDetailAlert(alert)}>
                        <StatusChip status={alert.status} />
                      </TableCell>
                      <TableCell onClick={() => setDetailAlert(alert)}>
                        <Typography variant="caption" color="text.secondary">
                          {new Date(alert.created_at).toLocaleString()}
                        </Typography>
                      </TableCell>
                      <TableCell align="right" onClick={(e) => e.stopPropagation()}>
                        <PermissionGuard permission="alert:acknowledge">
                          <Stack direction="row" spacing={0.5} justifyContent="flex-end">
                            {/* Spelled out rather than "Ack" / "FP". These are the two
                                most-pressed controls in the product and were previously
                                abbreviations a new operator had no way to decode —
                                "FP" in particular reads as nothing at all. The tooltips
                                state the consequence, since both actions change what the
                                rest of the team sees. */}
                            {alert.status === 'open' && (
                              <Tooltip title="Mark as seen — it stays in the list, assigned to you">
                                {/* aria-label pins the accessible name to the action.
                                    Without it MUI's Tooltip becomes the button's name,
                                    so a screen reader announces the whole explanatory
                                    sentence instead of "Acknowledge". */}
                                <Button size="small" variant="outlined" aria-label="Acknowledge"
                                        onClick={() => acknowledge(alert.id)}>
                                  Acknowledge
                                </Button>
                              </Tooltip>
                            )}
                            {(alert.status === 'open' || alert.status === 'acknowledged') && (
                              <Tooltip title="Not a real event — records why, and stops it counting toward open alerts">
                                <Button
                                  size="small" variant="outlined" color="warning"
                                  aria-label="False positive"
                                  onClick={() => { setFpDialog({ alertId: alert.id }); setFpReason('') }}
                                >
                                  False positive
                                </Button>
                              </Tooltip>
                            )}
                          </Stack>
                        </PermissionGuard>
                      </TableCell>
                    </TableRow>
                  ))}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>

      {/* Alert detail drawer */}
      {detailAlert && (
        <AlertDetailDrawer alertId={detailAlert.id} alert={detailAlert} onClose={() => setDetailAlert(null)} />
      )}

      {/* False Positive Dialog */}
      <Dialog open={!!fpDialog} onClose={() => setFpDialog(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Mark as False Positive</DialogTitle>
        <DialogContent>
          <TextField
            label="Reason (optional)" value={fpReason}
            onChange={(e) => setFpReason(e.target.value)}
            multiline rows={3} fullWidth sx={{ mt: 1 }}
            placeholder="Explain why this alert is a false positive…"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setFpDialog(null)}>Cancel</Button>
          <Button
            variant="contained" color="warning"
            disabled={fpPending}
            onClick={() => fpDialog && flagFP({ alertId: fpDialog.alertId, reason: fpReason })}
          >
            Mark False Positive
          </Button>
        </DialogActions>
      </Dialog>
      </Box>

      <FilterRail groups={filterGroups} storageKey="alerts" />
    </Box>
  )
}

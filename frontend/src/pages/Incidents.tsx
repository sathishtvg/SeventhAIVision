import { useState } from 'react'
import {
  Box, Chip, Stack, Typography, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Drawer, Divider, Button, TextField, Skeleton, Paper,
  MenuItem, Stepper, Step, StepLabel, Tabs, Tab, Checkbox,
} from '@mui/material'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import { SeverityChip } from '@/components/common/SeverityChip'
import { StatusChip } from '@/components/common/StatusChip'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { getIncidents, addIncidentNote, resolveIncident, updateIncidentStatus, getIncidentTimeline, bulkResolveIncidents, bulkUpdateIncidentStatus } from '@/api/incidents'
import { getSites } from '@/api/sites'
import { dispatchGuard, guardArrived } from '@/api/guards'
import { getUsers } from '@/api/users'
import type { Incident, IncidentSeverity } from '@/types/api'
import { MODULE_LABELS } from '@/api/licenses'

const STATUS_STEPS = ['open', 'dispatched', 'en_route', 'on_scene', 'contained', 'resolved'] as const
const NEXT_STATUS: Record<string, string> = {
  open: 'dispatched',
  dispatched: 'en_route',
  en_route: 'on_scene',
  on_scene: 'contained',
  contained: 'resolved',
}

const STATUS_FILTERS = ['all', 'open', 'dispatched', 'en_route', 'on_scene', 'contained', 'investigating', 'resolved', 'closed'] as const
type StatusFilter = (typeof STATUS_FILTERS)[number]

interface IncidentDrawerProps {
  incident: Incident | null
  onClose: () => void
}

function IncidentDrawer({ incident, onClose }: IncidentDrawerProps) {
  const [note, setNote] = useState('')
  const [selectedGuard, setSelectedGuard] = useState('')
  const [dispatchNotes, setDispatchNotes] = useState('')
  const [tab, setTab] = useState(0)
  const queryClient = useQueryClient()

  const { data: users = [] } = useQuery({ queryKey: ['users'], queryFn: () => getUsers() })

  const { data: timeline, isLoading: timelineLoading } = useQuery({
    queryKey: ['incident-timeline', incident?.id],
    queryFn: () => getIncidentTimeline(incident!.id),
    enabled: !!incident && tab === 1,
  })

  const { mutate: submitNote, isPending: notePending } = useMutation({
    mutationFn: ({ id, text }: { id: string; text: string }) => addIncidentNote(id, text),
    onSuccess: () => {
      setNote('')
      queryClient.invalidateQueries({ queryKey: ['incidents'] })
      queryClient.invalidateQueries({ queryKey: ['incident-timeline', incident?.id] })
    },
  })

  const { mutate: resolve } = useMutation({
    mutationFn: resolveIncident,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['incidents'] })
      onClose()
    },
  })

  const { mutate: dispatch, isPending: dispatchPending } = useMutation({
    mutationFn: ({ id, guardId }: { id: string; guardId: string }) =>
      dispatchGuard(id, { guard_user_id: guardId, dispatch_notes: dispatchNotes || undefined }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['incidents'] })
      setSelectedGuard('')
      setDispatchNotes('')
    },
  })

  const { mutate: markArrived } = useMutation({
    mutationFn: (id: string) => guardArrived(id),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['incidents'] }),
  })

  const { mutate: advanceStatus, isPending: advancePending } = useMutation({
    mutationFn: ({ id, status }: { id: string; status: string }) => updateIncidentStatus(id, status),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['incidents'] })
      queryClient.invalidateQueries({ queryKey: ['incident-timeline', incident?.id] })
    },
  })

  const currentStepIndex = STATUS_STEPS.indexOf(incident?.status as any)
  const nextStatus = incident ? NEXT_STATUS[incident.status] : undefined

  return (
    <Drawer anchor="right" open={!!incident} onClose={onClose} slotProps={{ paper: { sx: { width: 460, p: 0, display: 'flex', flexDirection: 'column' } } }}>
      {incident && (
        <>
          <Box sx={{ p: 3, pb: 0 }}>
            <Typography variant="h6" sx={{ fontWeight: 700, mb: 1 }}>
              {incident.title}
            </Typography>
            <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: 'wrap' }}>
              <SeverityChip severity={incident.severity as IncidentSeverity} />
              <StatusChip status={incident.status} />
              {(incident as any).site_name && (
                <Chip label={(incident as any).site_name} size="small" color="info" />
              )}
              {(incident as any).alert_code && (
                <Chip
                  label={MODULE_LABELS[(incident as any).alert_code?.split('.')[0] as keyof typeof MODULE_LABELS] ?? (incident as any).alert_code?.split('.')[0]}
                  size="small"
                  variant="outlined"
                />
              )}
            </Stack>

            {/* Status stepper */}
            <Box sx={{ mb: 2 }}>
              <Stepper activeStep={currentStepIndex} alternativeLabel>
                {STATUS_STEPS.map((s) => (
                  <Step key={s} completed={currentStepIndex > STATUS_STEPS.indexOf(s)}>
                    <StepLabel sx={{ '& .MuiStepLabel-label': { fontSize: '0.6rem' } }}>
                      {s.replace('_', ' ')}
                    </StepLabel>
                  </Step>
                ))}
              </Stepper>
              {nextStatus && !['resolved', 'closed'].includes(incident.status) && (
                <PermissionGuard permission="incident:update">
                  <Box sx={{ display: 'flex', justifyContent: 'center', mt: 1 }}>
                    <Button
                      size="small" variant="outlined" color="primary"
                      disabled={advancePending}
                      onClick={() => advanceStatus({ id: incident.id, status: nextStatus })}
                    >
                      Advance to {nextStatus.replace('_', ' ')}
                    </Button>
                  </Box>
                </PermissionGuard>
              )}
            </Box>

            <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ borderBottom: 1, borderColor: 'rgba(255,255,255,0.1)' }}>
              <Tab label="Details" />
              <Tab label="Timeline" />
            </Tabs>
          </Box>

          <Box sx={{ flex: 1, overflowY: 'auto', p: 3, pt: 2 }}>
            {tab === 0 && (
              <>
                {(incident as any).camera_name && (
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
                    <strong>Camera:</strong> {(incident as any).camera_name}
                  </Typography>
                )}
                {incident.description && (
                  <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
                    {incident.description}
                  </Typography>
                )}
                <Typography variant="caption" color="text.secondary">
                  Created: {new Date(incident.created_at).toLocaleString()}
                </Typography>
                {incident.is_auto_created && (
                  <Chip label="Auto-created" size="small" variant="outlined" sx={{ mt: 1, display: 'block', width: 'fit-content' }} />
                )}

                <Divider sx={{ my: 2, borderColor: 'rgba(255,255,255,0.1)' }} />

                <Typography variant="subtitle2" sx={{ mb: 1 }}>Add Note</Typography>
                <TextField
                  multiline rows={3} fullWidth size="small" placeholder="Write a note…"
                  value={note} onChange={(e) => setNote(e.target.value)} sx={{ mb: 1 }}
                />
                <PermissionGuard permission="incident:update">
                  <Button
                    variant="contained" size="small"
                    disabled={!note.trim() || notePending}
                    onClick={() => submitNote({ id: incident.id, text: note })}
                  >
                    Submit Note
                  </Button>
                </PermissionGuard>

                <Divider sx={{ my: 2, borderColor: 'rgba(255,255,255,0.1)' }} />

                {/* Dispatch Section */}
                <PermissionGuard permission="incident:dispatch">
                  {!(incident as any).dispatched_guard_user_id ? (
                    <>
                      <Typography variant="subtitle2" sx={{ mb: 1 }}>Dispatch Guard</Typography>
                      <TextField
                        select label="Select Guard" value={selectedGuard} fullWidth size="small" sx={{ mb: 1 }}
                        onChange={(e) => setSelectedGuard(e.target.value)}
                      >
                        {(users as any[]).filter((u) => u.role_id === 5).map((u) => (
                          <MenuItem key={u.id} value={u.id}>{u.full_name || u.email}</MenuItem>
                        ))}
                      </TextField>
                      <TextField
                        label="Dispatch Notes" value={dispatchNotes} fullWidth size="small" sx={{ mb: 1 }}
                        onChange={(e) => setDispatchNotes(e.target.value)}
                      />
                      <Button
                        variant="contained" fullWidth size="small"
                        disabled={!selectedGuard || dispatchPending}
                        onClick={() => dispatch({ id: incident.id, guardId: selectedGuard })}
                      >
                        Dispatch
                      </Button>
                    </>
                  ) : (
                    <>
                      <Typography variant="subtitle2" sx={{ mb: 0.5 }}>Guard Dispatched</Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
                        {(incident as any).dispatched_at
                          ? `Dispatched at ${new Date((incident as any).dispatched_at).toLocaleTimeString()}`
                          : 'Guard assigned'}
                      </Typography>
                      {!(incident as any).guard_arrived_at && (
                        <Button variant="outlined" color="success" fullWidth size="small" onClick={() => markArrived(incident.id)}>
                          Mark Guard Arrived
                        </Button>
                      )}
                      {(incident as any).guard_arrived_at && <Chip label="Guard on scene" color="success" size="small" />}
                    </>
                  )}
                </PermissionGuard>

                <Divider sx={{ my: 2, borderColor: 'rgba(255,255,255,0.1)' }} />

                <PermissionGuard permission="incident:update">
                  {incident.status !== 'resolved' && incident.status !== 'closed' && (
                    <Button variant="outlined" color="success" fullWidth onClick={() => resolve(incident.id)}>
                      Mark Resolved
                    </Button>
                  )}
                </PermissionGuard>
              </>
            )}

            {tab === 1 && (
              <>
                {timelineLoading ? (
                  <Box><Skeleton /><Skeleton /><Skeleton /><Skeleton /></Box>
                ) : !(timeline as any[])?.length ? (
                  <Typography color="text.secondary">No timeline entries yet.</Typography>
                ) : (
                  (timeline as any[]).map((entry: any, i: number) => (
                    <Box key={i} sx={{ display: 'flex', mb: 2.5 }}>
                      <Box sx={{ display: 'flex', flexDirection: 'column', alignItems: 'center', mr: 2 }}>
                        <Box sx={{ width: 12, height: 12, borderRadius: '50%', bgcolor: entry.entry_type === 'status_change' ? 'primary.main' : 'text.disabled', mt: 0.5 }} />
                        {i < (timeline as any[]).length - 1 && (
                          <Box sx={{ width: 2, flex: 1, bgcolor: 'rgba(255,255,255,0.1)', mt: 0.5 }} />
                        )}
                      </Box>
                      <Box sx={{ flex: 1 }}>
                        <Typography variant="body2" sx={{ fontWeight: entry.entry_type === 'status_change' ? 700 : 400 }}>
                          {entry.entry_type === 'status_change'
                            ? `Status → ${(entry.status || '').replace('_', ' ')}`
                            : entry.note ?? entry.body}
                        </Typography>
                        {entry.changed_by_name && (
                          <Typography variant="caption" color="text.secondary">{entry.changed_by_name}</Typography>
                        )}
                        <Typography variant="caption" color="text.disabled" sx={{ display: 'block' }}>
                          {new Date(entry.created_at || entry.occurred_at).toLocaleString()}
                        </Typography>
                        {(entry.latitude != null) && (
                          <Typography variant="caption" color="text.secondary">
                            GPS: {entry.latitude.toFixed(5)}, {entry.longitude.toFixed(5)}
                          </Typography>
                        )}
                      </Box>
                    </Box>
                  ))
                )}
              </>
            )}
          </Box>
        </>
      )}
    </Drawer>
  )
}

export default function Incidents() {
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('open')
  const [siteFilter, setSiteFilter] = useState('')
  const [moduleFilter, setModuleFilter] = useState('')
  const [selected, setSelected] = useState<Incident | null>(null)
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set())
  const queryClient = useQueryClient()

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const { data: incidents, isLoading } = useQuery({
    queryKey: ['incidents', statusFilter, siteFilter, moduleFilter],
    queryFn: () =>
      getIncidents(
        statusFilter === 'all' ? undefined : statusFilter,
        siteFilter || undefined,
        moduleFilter || undefined,
      ),
  })

  const { mutate: bulkResolve, isPending: bulkResolvePending } = useMutation({
    mutationFn: (ids: string[]) => bulkResolveIncidents(ids),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['incidents'] }); setSelectedIds(new Set()) },
  })

  const { mutate: bulkStatusUpdate, isPending: bulkStatusPending } = useMutation({
    mutationFn: ({ ids, status }: { ids: string[]; status: string }) => bulkUpdateIncidentStatus(ids, status),
    onSuccess: () => { queryClient.invalidateQueries({ queryKey: ['incidents'] }); setSelectedIds(new Set()) },
  })

  const allIncidentIds = incidents?.items?.map((i: any) => i.id) ?? []
  const allSelected = allIncidentIds.length > 0 && allIncidentIds.every((id: string) => selectedIds.has(id))
  const toggleAll = () => setSelectedIds(allSelected ? new Set() : new Set(allIncidentIds))
  const toggleOne = (id: string) => setSelectedIds(prev => { const s = new Set(prev); if (s.has(id)) s.delete(id); else s.add(id); return s })

  // Same three groups as Alerts, same reasoning for the 'all' sentinel on
  // status: it defaults to 'open', so 'open' is not the neutral value.
  const filterGroups: FilterGroup[] = [
    {
      key: 'status',
      label: 'Status',
      allValue: 'all',
      value: statusFilter,
      onChange: (v) => setStatusFilter(v as StatusFilter),
      options: STATUS_FILTERS.map((s) => ({
        value: s,
        label: s.charAt(0).toUpperCase() + s.slice(1).replace(/_/g, ' '),
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
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      {/* Bulk action bar */}
      {selectedIds.size > 0 && (
        <PermissionGuard permission="incident:resolve">
          <Paper sx={{ mb: 2, p: 1.5, display: 'flex', alignItems: 'center', gap: 1, bgcolor: 'rgba(108,99,255,0.12)', border: '1px solid rgba(108,99,255,0.3)' }}>
            <Typography variant="body2" sx={{ flex: 1 }}>
              {selectedIds.size} incident{selectedIds.size > 1 ? 's' : ''} selected
            </Typography>
            <Button size="small" variant="outlined" color="success" disabled={bulkResolvePending || bulkStatusPending} onClick={() => bulkResolve([...selectedIds])}>
              Resolve All
            </Button>
            <Button size="small" variant="outlined" disabled={bulkResolvePending || bulkStatusPending} onClick={() => bulkStatusUpdate({ ids: [...selectedIds], status: 'closed' })}>
              Close All
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
                <TableCell>Status</TableCell>
                <TableCell>Auto</TableCell>
                <TableCell>Created</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {isLoading
                ? Array.from({ length: 6 }).map((_, i) => (
                    <TableRow key={i}>
                      {Array.from({ length: 7 }).map((__, j) => (
                        <TableCell key={j}><Skeleton /></TableCell>
                      ))}
                    </TableRow>
                  ))
                : !incidents?.items?.length
                ? (
                    <TableRow>
                      <TableCell colSpan={7} align="center" sx={{ py: 4 }}>
                        <Typography color="text.secondary">No incidents found</Typography>
                      </TableCell>
                    </TableRow>
                  )
                : incidents?.items?.map((incident: any) => (
                    <TableRow key={incident.id} hover selected={selectedIds.has(incident.id)} sx={{ cursor: 'pointer' }} onClick={() => setSelected(incident)}>
                      <TableCell padding="checkbox" onClick={(e) => { e.stopPropagation(); toggleOne(incident.id) }}>
                        <Checkbox size="small" checked={selectedIds.has(incident.id)} onChange={() => {}} />
                      </TableCell>
                      <TableCell><SeverityChip severity={incident.severity as IncidentSeverity} /></TableCell>
                      <TableCell>
                        <Typography variant="body2" sx={{ fontWeight: 500 }}>{incident.title}</Typography>
                        {incident.camera_name && (
                          <Typography variant="caption" color="text.secondary">{incident.camera_name}</Typography>
                        )}
                      </TableCell>
                      <TableCell>
                        {incident.site_name ? (
                          <Chip label={incident.site_name} size="small" color="info" variant="outlined" />
                        ) : (
                          <Typography variant="caption" color="text.disabled">—</Typography>
                        )}
                      </TableCell>
                      <TableCell><StatusChip status={incident.status} /></TableCell>
                      <TableCell>
                        {incident.is_auto_created && <Chip label="Auto" size="small" color="secondary" variant="outlined" />}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {new Date(incident.created_at).toLocaleString()}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>

      <IncidentDrawer incident={selected} onClose={() => setSelected(null)} />
      </Box>

      <FilterRail groups={filterGroups} storageKey="incidents" />
    </Box>
  )
}

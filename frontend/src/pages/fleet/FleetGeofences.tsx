/**
 * Fleet geofences: the circles and polygons the vehicles are watched against, what each alerts on, how many
 * vehicles are inside now, and the entries, exits, stays and overspeeds of the last day.
 *
 * A fix that wobbles on the boundary changes nothing (the server's rule: past the line by 15 m or the fix's own
 * accuracy, held for a second fix or 30 seconds). A geofence never opens a barrier, admits anybody or confirms
 * who is driving. Somebody limited to sites sees their sites' geofences and the organisation's own, and their
 * sites' vehicles' events only. A geofence is archived, never deleted.
 */
import { useState } from 'react'
import {
  Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, IconButton, Switch, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/EditOutlined'
import ArchiveIcon from '@mui/icons-material/ArchiveOutlined'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { PageHeader } from '@/components/common/PageHeader'
import { GlassCard } from '@/components/common/GlassCard'
import { SkeletonRows } from '@/components/common/SkeletonRows'
import { FleetNav } from '@/components/fleet/FleetNav'
import { GeofenceDialog } from '@/components/fleet/GeofenceDialog'
import { GeofenceMap } from '@/components/fleet/GeofenceMap'
import { SEVERITY_COLOUR, activeRules, eventWhen, eventWords, ruleWords, shapeWords } from '@/components/fleet/geofenceFormat'
import { SaveStatus, TableEmptyRow, TableErrorRow } from '@/components/states'
import { archiveGeofence, getGeofenceOptions, listFleetGeofenceEvents, listGeofences, vehicleLabel, type Geofence } from '@/api/fleet'

const COLS = 6
const EVENT_COLS = 4

export default function FleetGeofences() {
  const qc = useQueryClient()
  const [archived, setArchived] = useState(false)
  const [editing, setEditing] = useState<Geofence | 'new' | null>(null)
  const [archiving, setArchiving] = useState<Geofence | null>(null)
  const { data, isLoading, isLoadingError: fencesFailed, error: fencesError, refetch: refetchFences } = useQuery({
    queryKey: ['fleet-geofences', archived], queryFn: () => listGeofences(archived),
  })
  const { data: options } = useQuery({ queryKey: ['fleet-geofence-options'], queryFn: getGeofenceOptions, enabled: !!data?.may.manage })
  const {
    data: events, isLoading: eventsLoading, isLoadingError: eventsFailed, error: eventsError, refetch: refetchEvents,
  } = useQuery({ queryKey: ['fleet-geofence-events'], queryFn: () => listFleetGeofenceEvents({ hours: 24 }), refetchInterval: 30_000 })
  const mayManage = !!data?.may.manage
  const fences = data?.items ?? []
  const again = () => {
    qc.invalidateQueries({ queryKey: ['fleet-geofences'] })
    qc.invalidateQueries({ queryKey: ['fleet-geofence-events'] })
  }

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Geofences" subtitle="Where the vehicles are watched, and what is alerted when they come and go" />
      <FleetNav />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 1, flexWrap: 'wrap' }}>
        {mayManage && (
          <Button variant="contained" startIcon={<AddIcon />} disabled={!options} onClick={() => setEditing('new')}>Draw a geofence</Button>
        )}
        <FormControlLabel label="Show archived geofences"
                          control={<Switch size="small" checked={archived} onChange={(e) => setArchived(e.target.checked)} />} />
      </Box>
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: 'minmax(0, 3fr) minmax(0, 2fr)' }, mb: 2 }}>
        <GlassCard sx={{ p: 0 }}>
          <TableContainer>
            <Table size="small" aria-label="Geofences">
              <TableHead>
                <TableRow>
                  <TableCell>Geofence</TableCell><TableCell>Site</TableCell><TableCell>Alerts</TableCell>
                  <TableCell>Inside now</TableCell><TableCell>Status</TableCell><TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {isLoading ? (
                  <SkeletonRows cols={COLS} rows={4} />
                ) : fencesFailed ? (
                  <TableErrorRow cols={COLS} title="The geofences could not be loaded" error={fencesError} onRetry={refetchFences} />
                ) : !fences.length ? (
                  <TableEmptyRow cols={COLS} title={archived ? 'No archived geofence' : 'No geofence yet'}
                                 hint={mayManage && !archived ? 'Draw one with the Draw a geofence button.' : undefined} />
                ) : fences.map((g) => (
                  <TableRow key={g.id} hover sx={{ opacity: g.is_active ? 1 : 0.6 }}>
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{g.name}</Typography>
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                        {shapeWords(g)}{g.groups.length ? ` · groups: ${g.groups.map((x) => x.name).join(', ')}` : ''}
                      </Typography>
                    </TableCell>
                    <TableCell>{g.site_name ?? 'Whole organisation'}</TableCell>
                    <TableCell>
                      {activeRules(g).length ? activeRules(g).map((r) => (
                        <Chip key={r.kind} size="small" variant="outlined" color={SEVERITY_COLOUR[r.severity]} label={ruleWords(r)} sx={{ mr: 0.5, mb: 0.5 }} />
                      )) : <Typography variant="caption" color="text.secondary">Recorded only</Typography>}
                    </TableCell>
                    <TableCell>{g.inside_now}</TableCell>
                    <TableCell>{g.archived ? `Archived: ${g.archive_reason ?? ''}` : g.is_active ? 'Looked at' : 'Switched off'}</TableCell>
                    <TableCell align="right">
                      {!g.archived && g.may_change && options && (
                        <Tooltip title="Change"><IconButton size="small" aria-label={`Change ${g.name}`} onClick={() => setEditing(g)}>
                          <EditIcon fontSize="small" /></IconButton></Tooltip>)}
                      {!g.archived && g.may_change && (
                        <Tooltip title="Archive"><IconButton size="small" aria-label={`Archive ${g.name}`} onClick={() => setArchiving(g)}>
                          <ArchiveIcon fontSize="small" /></IconButton></Tooltip>)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        </GlassCard>
        <GeofenceMap fences={fences} focus={`${archived}|${fences.length}`} height={360} />
      </Box>
      <GlassCard sx={{ p: 0 }}>
        <Typography variant="subtitle2" sx={{ px: 2, pt: 1.5 }}>The last 24 hours</Typography>
        <TableContainer>
          <Table size="small" aria-label="Geofence events">
            <TableHead>
              <TableRow><TableCell>When</TableCell><TableCell>Vehicle</TableCell><TableCell>What</TableCell><TableCell>Alert</TableCell></TableRow>
            </TableHead>
            <TableBody>
              {eventsLoading ? (
                <SkeletonRows cols={EVENT_COLS} rows={3} />
              ) : eventsFailed ? (
                <TableErrorRow cols={EVENT_COLS} title="The geofence events could not be loaded" error={eventsError} onRetry={refetchEvents} />
              ) : !events?.items.length ? (
                <TableEmptyRow cols={EVENT_COLS} title="Nothing in the last 24 hours" />
              ) : events.items.map((e) => (
                <TableRow key={e.id}>
                  <TableCell>{eventWhen(e)}</TableCell>
                  <TableCell>{vehicleLabel({ name: e.vehicle_name, plate_number: e.plate_number })}</TableCell>
                  <TableCell>{eventWords(e)} {e.geofence_name}{e.speed != null && e.event_type === 'speed_violation' ? ` at ${Math.round(e.speed)} km/h` : ''}</TableCell>
                  <TableCell>
                    {e.alert_id && e.alert_severity
                      ? <Chip size="small" color={SEVERITY_COLOUR[e.alert_severity]} label={`${e.alert_severity} · ${e.alert_status ?? ''}`} />
                      : <Typography variant="caption" color="text.secondary">Recorded, no alert</Typography>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>
      {editing && options && data && (
        <GeofenceDialog geofence={editing === 'new' ? null : editing} options={options} others={fences}
                        organisationWide={data.may.organisation_wide}
                        onClose={() => setEditing(null)} onSaved={() => { setEditing(null); again() }} />
      )}
      {archiving && <ArchiveDialog geofence={archiving} onClose={() => setArchiving(null)} onDone={() => { setArchiving(null); again() }} />}
    </Box>
  )
}

function ArchiveDialog({ geofence, onClose, onDone }: { geofence: Geofence; onClose: () => void; onDone: () => void }) {
  const [reason, setReason] = useState('')
  const archive = useMutation({ mutationFn: () => archiveGeofence(geofence.id, reason.trim()), onSuccess: onDone })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth aria-labelledby="archive-geofence-title">
      <DialogTitle id="archive-geofence-title">Archive {geofence.name}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 2 }}>
          No fix is looked at against it any more. Its entries, exits and alerts stay readable.
        </Typography>
        <TextField label="Why" required fullWidth size="small" value={reason} onChange={(e) => setReason(e.target.value)}
                   helperText="At least 3 characters, kept with it" />
      </DialogContent>
      <DialogActions sx={{ gap: 1 }}>
        <SaveStatus state={archive.isPending ? 'saving' : archive.isError ? 'failed' : 'idle'} error={archive.error} savingText="Archiving…" />
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" color="warning" disabled={reason.trim().length < 3 || archive.isPending} onClick={() => archive.mutate()}>Archive</Button>
      </DialogActions>
    </Dialog>
  )
}

/**
 * Drone Patrol — security zones (drawn on the site map) and AI security
 * profiles (which detections matter, and how seriously).
 */
import { useMemo, useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel,
  Grid, List, ListItemButton, ListItemText, MenuItem, Skeleton, Switch, Tab, Table, TableBody,
  TableCell, TableHead, TableRow, Tabs, TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import {
  AI_MODULES, RISK_LEVELS, ZONE_TYPES, apiError, createProfile, createZone, deleteZone, getProfile, listProfiles,
  listZones, updateProfile, updateZone,
} from '@/api/drones'
import type { AlertPolicy, Profile, ProfileRule, RiskLevel, Zone, ZoneInput, ZoneShape, ZoneType } from '@/api/drones'
import { DroneMap, FitTo, LicenceBanner, RiskChip } from '@/components/drones/droneUi'
import { ZONE_COLOR, pretty } from '@/components/drones/droneFormat'
import { ZoneDrawer, ZoneLayer } from '@/components/drones/MapEditors'
import type { ZoneDraft } from '@/components/drones/geo'
import { draftComplete, draftFromZone, emptyDraft } from '@/components/drones/geo'
import { DroneNav } from './DroneNav'

const WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

export default function DroneZones() {
  const [tab, setTab] = useState(0)
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Zones & AI Profiles" subtitle="Where on a site matters, and how the drone's AI judges what it sees" />
      <DroneNav />
      <LicenceBanner />
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Security zones" />
        <Tab label="AI security profiles" />
      </Tabs>
      {tab === 0 ? <ZonesTab /> : <ProfilesTab />}
    </Box>
  )
}

// ── Zones ────────────────────────────────────────────────────────────────────

interface ZoneForm {
  name: string
  zone_type: ZoneType
  severity: RiskLevel
  alert_policy: AlertPolicy
  active_from: string
  active_to: string
  active_weekdays: number[]
  plates: string
  detection_threshold: string
  is_active: boolean
}

const blankForm = (): ZoneForm => ({ name: '', zone_type: 'RESTRICTED', severity: 'MEDIUM', alert_policy: 'ALERT',
                                     active_from: '', active_to: '', active_weekdays: [], plates: '',
                                     detection_threshold: '', is_active: true })

function formFromZone(z: Zone): ZoneForm {
  return { name: z.name, zone_type: z.zone_type, severity: z.severity, alert_policy: z.alert_policy,
           active_from: z.active_from?.slice(0, 5) ?? '', active_to: z.active_to?.slice(0, 5) ?? '',
           active_weekdays: z.active_weekdays ?? [], plates: (z.allowed_vehicle_plates ?? []).join(', '),
           detection_threshold: z.detection_threshold != null ? String(z.detection_threshold) : '',
           is_active: z.is_active }
}

function ZonesTab() {
  const qc = useQueryClient()
  const canCreate = usePermission('drone:mission:create')
  const canUpdate = usePermission('drone:mission:update')
  const canDelete = usePermission('drone:mission:update')
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const [chosenSite, setSiteId] = useState('')
  const siteId = chosenSite || sites?.[0]?.id || ''
  const site = sites?.find((s) => s.id === siteId)
  const { data: zones, isLoading } = useQuery({ queryKey: ['drone-zones', siteId], queryFn: () => listZones(siteId),
                                                enabled: !!siteId })

  const [editing, setEditing] = useState<Zone | 'new' | null>(null)
  const [draft, setDraft] = useState<ZoneDraft>(emptyDraft('POLYGON'))
  const [form, setForm] = useState<ZoneForm>(blankForm())
  const [error, setError] = useState<string | null>(null)

  const start = (z: Zone | 'new') => {
    setEditing(z)
    setError(null)
    if (z === 'new') { setDraft(emptyDraft('POLYGON')); setForm(blankForm()) } else { setDraft(draftFromZone(z)); setForm(formFromZone(z)) }
  }
  const editable = editing === 'new' ? canCreate : !!editing && canUpdate
  const others = (zones ?? []).filter((z) => editing === 'new' || !editing || z.id !== editing.id)

  const save = useMutation({
    mutationFn: () => {
      const body: ZoneInput = {
        name: form.name, zone_type: form.zone_type, severity: form.severity, alert_policy: form.alert_policy,
        shape: draft.shape,
        polygon: draft.shape === 'CIRCLE' ? null : draft.points,
        center_latitude: draft.shape === 'CIRCLE' ? draft.center?.lat ?? null : null,
        center_longitude: draft.shape === 'CIRCLE' ? draft.center?.lng ?? null : null,
        radius_m: draft.shape === 'CIRCLE' ? draft.radius_m : null,
        active_from: form.active_from || null, active_to: form.active_to || null,
        active_weekdays: form.active_weekdays.length ? form.active_weekdays : null,
        allowed_vehicle_plates: form.plates.split(/[\s,]+/).filter(Boolean),
        detection_threshold: form.detection_threshold ? Number(form.detection_threshold) : null,
        is_active: form.is_active,
      }
      return editing === 'new' ? createZone({ ...body, site_id: siteId }) : updateZone((editing as Zone).id, body)
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['drone-zones'] }); setEditing(null) },
    onError: (e) => setError(apiError(e)),
  })
  const remove = useMutation({
    mutationFn: (id: string) => deleteZone(id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['drone-zones'] }); setEditing(null) },
    onError: (e) => setError(apiError(e)),
  })

  const fit = useMemo(() => {
    const pts: [number, number][] = []
    ;(zones ?? []).forEach((z) => {
      if (z.center_latitude != null && z.center_longitude != null) pts.push([z.center_latitude, z.center_longitude])
      ;(z.polygon ?? []).forEach((p) => pts.push([p.lat, p.lng]))
    })
    if (!pts.length && site?.latitude != null && site?.longitude != null) pts.push([site.latitude, site.longitude])
    return pts
  }, [zones, site])

  return (
    <Grid container spacing={2}>
      <Grid size={{ xs: 12, lg: 8 }}>
        <GlassCard sx={{ p: 2 }}>
          <Stack direction="row" sx={{ gap: 2, mb: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
            <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 220 }}
                       onChange={(e) => { setSiteId(e.target.value); setEditing(null) }}>
              {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
            {editing && editable && (
              <ToggleButtonGroup size="small" exclusive value={draft.shape}
                                 onChange={(_, v: ZoneShape | null) => v && setDraft(emptyDraft(v))}>
                <ToggleButton value="POLYGON">Polygon</ToggleButton>
                <ToggleButton value="RECTANGLE">Rectangle</ToggleButton>
                <ToggleButton value="CIRCLE">Circle</ToggleButton>
              </ToggleButtonGroup>
            )}
            {editing && editable && <Button size="small" onClick={() => setDraft(emptyDraft(draft.shape))}>Clear shape</Button>}
          </Stack>
          {siteId ? (
            <DroneMap height={560}>
              <ZoneLayer zones={others} />
              {editing && editable && <ZoneDrawer draft={draft} onChange={setDraft} color={ZONE_COLOR[form.zone_type]} />}
              {editing && !editable && editing !== 'new' && <ZoneLayer zones={[editing]} highlight={editing.id} />}
              <FitTo points={fit} />
            </DroneMap>
          ) : <Skeleton height={560} />}
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            {draft.shape === 'CIRCLE' ? 'Click the centre, then a point on the edge.'
              : draft.shape === 'RECTANGLE' ? 'Click two opposite corners.'
                : 'Click each corner in turn; drag a corner to move it.'}
          </Typography>
        </GlassCard>
      </Grid>
      <Grid size={{ xs: 12, lg: 4 }}>
        {editing ? (
          <GlassCard sx={{ p: 2 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1.5 }}>
              {editing === 'new' ? 'New zone' : editing.name}</Typography>
            {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
            <Stack sx={{ gap: 1.5 }}>
              <TextField size="small" label="Name" value={form.name} disabled={!editable}
                         onChange={(e) => setForm({ ...form, name: e.target.value })} />
              <TextField select size="small" label="Type" value={form.zone_type} disabled={!editable}
                         onChange={(e) => setForm({ ...form, zone_type: e.target.value as ZoneType })}>
                {ZONE_TYPES.map((t) => <MenuItem key={t} value={t}>{pretty(t)}</MenuItem>)}
              </TextField>
              <Stack direction="row" sx={{ gap: 1 }}>
                <TextField select size="small" label="Severity" value={form.severity} disabled={!editable} sx={{ flex: 1 }}
                           onChange={(e) => setForm({ ...form, severity: e.target.value as RiskLevel })}>
                  {RISK_LEVELS.map((r) => <MenuItem key={r} value={r}>{pretty(r)}</MenuItem>)}
                </TextField>
                <TextField select size="small" label="On a detection" value={form.alert_policy} disabled={!editable} sx={{ flex: 1 }}
                           onChange={(e) => setForm({ ...form, alert_policy: e.target.value as AlertPolicy })}>
                  <MenuItem value="NONE">Record only</MenuItem>
                  <MenuItem value="ALERT">Raise an alert</MenuItem>
                  <MenuItem value="INCIDENT">Open an incident</MenuItem>
                </TextField>
              </Stack>
              <Stack direction="row" sx={{ gap: 1 }}>
                <TextField size="small" type="time" label="Active from" value={form.active_from} disabled={!editable}
                           sx={{ flex: 1 }} slotProps={{ inputLabel: { shrink: true } }}
                           onChange={(e) => setForm({ ...form, active_from: e.target.value })} />
                <TextField size="small" type="time" label="Active to" value={form.active_to} disabled={!editable}
                           sx={{ flex: 1 }} slotProps={{ inputLabel: { shrink: true } }}
                           onChange={(e) => setForm({ ...form, active_to: e.target.value })} />
              </Stack>
              <ToggleButtonGroup size="small" value={form.active_weekdays} disabled={!editable}
                                 onChange={(_, v: number[]) => setForm({ ...form, active_weekdays: v })}>
                {WEEKDAYS.map((d, i) => <ToggleButton key={d} value={i}>{d}</ToggleButton>)}
              </ToggleButtonGroup>
              <Typography variant="caption" color="text.secondary">
                No times or days means always active. Times are the site's local time.</Typography>
              <TextField size="small" label="Allowed vehicle plates" value={form.plates} disabled={!editable}
                         helperText="Plates the drone should not flag here, separated by commas"
                         onChange={(e) => setForm({ ...form, plates: e.target.value })} />
              <TextField size="small" type="number" label="Detection threshold (0–1)" value={form.detection_threshold}
                         disabled={!editable} helperText="Blank: the profile's confidence decides"
                         onChange={(e) => setForm({ ...form, detection_threshold: e.target.value })} />
              <FormControlLabel control={<Switch checked={form.is_active} disabled={!editable}
                onChange={(_, v) => setForm({ ...form, is_active: v })} />} label="Active" />
              <Stack direction="row" sx={{ gap: 1, justifyContent: 'space-between' }}>
                {editing !== 'new' && canDelete ? (
                  <Button color="error" startIcon={<DeleteIcon />} disabled={remove.isPending}
                          onClick={() => { if (window.confirm(`Delete zone ${editing.name}?`)) remove.mutate(editing.id) }}>
                    Delete</Button>) : <span />}
                <Stack direction="row" sx={{ gap: 1 }}>
                  <Button onClick={() => setEditing(null)}>Cancel</Button>
                  {editable && <Button variant="contained" disabled={!form.name || !draftComplete(draft) || save.isPending}
                                       onClick={() => save.mutate()}>Save</Button>}
                </Stack>
              </Stack>
            </Stack>
          </GlassCard>
        ) : (
          <GlassCard sx={{ p: 2 }}>
            <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
              <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Zones at this site</Typography>
              {canCreate && siteId && <Button size="small" startIcon={<AddIcon />} onClick={() => start('new')}>New zone</Button>}
            </Stack>
            {isLoading ? <Skeleton height={120} /> : !(zones ?? []).length ? (
              <Alert severity="info">No zones yet. Without zones the drone judges every place the same.</Alert>
            ) : (
              <List dense>
                {(zones ?? []).map((z) => (
                  <ListItemButton key={z.id} onClick={() => start(z)}>
                    <Box sx={{ width: 10, height: 10, borderRadius: '2px', bgcolor: ZONE_COLOR[z.zone_type], mr: 1.5 }} />
                    <ListItemText primary={z.name}
                                  secondary={`${pretty(z.zone_type)} · ${pretty(z.shape)}${z.is_active ? '' : ' · off'}`} />
                    <RiskChip level={z.severity} />
                  </ListItemButton>
                ))}
              </List>
            )}
          </GlassCard>
        )}
      </Grid>
    </Grid>
  )
}

// ── Profiles ─────────────────────────────────────────────────────────────────

const defaultRule = (m: ProfileRule['module_type']): ProfileRule =>
  ({ module_type: m, is_enabled: false, min_confidence: null, base_severity: 'MEDIUM', incident_risk_level: 'HIGH' })

function ProfilesTab() {
  const qc = useQueryClient()
  const canCreate = usePermission('drone:mission:create')
  const canUpdate = usePermission('drone:mission:update')
  const { data: profiles, isLoading } = useQuery({ queryKey: ['drone-profiles'], queryFn: listProfiles })
  const [openId, setOpenId] = useState<string | 'new' | null>(null)
  return (
    <GlassCard sx={{ p: 2 }}>
      <Stack direction="row" sx={{ justifyContent: 'space-between', alignItems: 'center', mb: 1 }}>
        <Typography variant="body2" color="text.secondary">
          A profile turns AI modules on or off, sets how confident a detection must be, and how serious each one is.
          The site's zones then raise or lower that.</Typography>
        {canCreate && <Button startIcon={<AddIcon />} variant="contained" onClick={() => setOpenId('new')}>New profile</Button>}
      </Stack>
      {isLoading ? <Skeleton height={160} /> : !(profiles ?? []).length ? (
        <Alert severity="info">No profiles yet. Missions without one use every module at its defaults.</Alert>
      ) : (
        <Table size="small">
          <TableHead><TableRow><TableCell>Profile</TableCell><TableCell>Modules on</TableCell>
            <TableCell>Min. confidence</TableCell><TableCell>Verify for</TableCell><TableCell>Missions</TableCell>
            <TableCell>Active</TableCell></TableRow></TableHead>
          <TableBody>
            {(profiles ?? []).map((p) => (
              <TableRow key={p.id} hover sx={{ cursor: 'pointer' }} onClick={() => setOpenId(p.id)}>
                <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{p.name}</Typography>
                  {p.description && <Typography variant="caption" color="text.secondary">{p.description}</Typography>}</TableCell>
                <TableCell>{p.enabled_rule_count ?? '—'}</TableCell>
                <TableCell>{Math.round(p.min_confidence * 100)}%</TableCell>
                <TableCell>{p.verify_min_seconds}s</TableCell>
                <TableCell>{p.mission_count ?? 0}</TableCell>
                <TableCell>{p.is_active ? <Chip size="small" color="success" label="Active" /> : <Chip size="small" label="Off" />}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {openId && <ProfileDialog id={openId} canEdit={openId === 'new' ? canCreate : canUpdate}
                                onClose={(saved) => { setOpenId(null); if (saved) qc.invalidateQueries({ queryKey: ['drone-profiles'] }) }} />}
    </GlassCard>
  )
}

function ProfileDialog({ id, canEdit, onClose }: { id: string; canEdit: boolean; onClose: (saved: boolean) => void }) {
  const isNew = id === 'new'
  const { data } = useQuery({ queryKey: ['drone-profile', id], queryFn: () => getProfile(id), enabled: !isNew })
  if (!isNew && !data) {
    return <Dialog open onClose={() => onClose(false)} maxWidth="md" fullWidth><DialogContent><Skeleton height={300} /></DialogContent></Dialog>
  }
  return <ProfileForm id={id} initial={data ?? null} canEdit={canEdit} onClose={onClose} />
}

function ProfileForm({ id, initial, canEdit, onClose }: { id: string; initial: Profile | null; canEdit: boolean
                                                          onClose: (saved: boolean) => void }) {
  const isNew = !initial
  const [p, setP] = useState<Omit<Profile, 'id'>>(initial ? { ...initial, rules: initial.rules ?? [] }
    : { name: '', description: null, min_confidence: 0.5, verify_min_seconds: 3, is_active: true, rules: [] })
  const rules = AI_MODULES.map((m) => p.rules?.find((r) => r.module_type === m) ?? defaultRule(m))
  const setRule = (m: string, patch: Partial<ProfileRule>) =>
    setP({ ...p, rules: rules.map((r) => (r.module_type === m ? { ...r, ...patch } : r)) })
  const save = useMutation({
    mutationFn: () => {
      const body = { name: p.name, description: p.description, min_confidence: p.min_confidence,
                     verify_min_seconds: p.verify_min_seconds, is_active: p.is_active, rules }
      return isNew ? createProfile(body) : updateProfile(id, body)
    },
    onSuccess: () => onClose(true),
  })
  return (
    <Dialog open onClose={() => onClose(false)} maxWidth="md" fullWidth>
      <DialogTitle>{isNew ? 'New AI security profile' : p.name}</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, pt: 1 }}>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField label="Name" value={p.name} disabled={!canEdit} sx={{ flex: 2, minWidth: 200 }}
                       onChange={(e) => setP({ ...p, name: e.target.value })} />
            <TextField type="number" label="Min. confidence (0–1)" value={p.min_confidence} disabled={!canEdit}
                       sx={{ flex: 1, minWidth: 150 }} slotProps={{ htmlInput: { step: 0.05, min: 0, max: 1 } }}
                       onChange={(e) => setP({ ...p, min_confidence: Number(e.target.value) })} />
            <TextField type="number" label="Verify for (s)" value={p.verify_min_seconds} disabled={!canEdit}
                       sx={{ flex: 1, minWidth: 120 }}
                       helperText="Seen this long before it counts"
                       onChange={(e) => setP({ ...p, verify_min_seconds: Number(e.target.value) })} />
          </Stack>
          <TextField label="Description" value={p.description ?? ''} disabled={!canEdit}
                     onChange={(e) => setP({ ...p, description: e.target.value || null })} />
          <Table size="small">
            <TableHead><TableRow><TableCell>AI module</TableCell><TableCell>On</TableCell>
              <TableCell>Own min. confidence</TableCell><TableCell>Base severity</TableCell>
              <TableCell>Incident at</TableCell></TableRow></TableHead>
            <TableBody>
              {rules.map((r) => (
                <TableRow key={r.module_type}>
                  <TableCell>{pretty(r.module_type)}</TableCell>
                  <TableCell><Checkbox checked={r.is_enabled} disabled={!canEdit}
                                       onChange={(_, v) => setRule(r.module_type, { is_enabled: v })} /></TableCell>
                  <TableCell><TextField size="small" type="number" value={r.min_confidence ?? ''} disabled={!canEdit}
                                        placeholder="profile's" sx={{ width: 110 }}
                                        slotProps={{ htmlInput: { step: 0.05, min: 0, max: 1 } }}
                                        onChange={(e) => setRule(r.module_type,
                                          { min_confidence: e.target.value ? Number(e.target.value) : null })} /></TableCell>
                  <TableCell><TextField select size="small" value={r.base_severity} disabled={!canEdit} sx={{ width: 120 }}
                                        onChange={(e) => setRule(r.module_type, { base_severity: e.target.value as RiskLevel })}>
                    {RISK_LEVELS.map((l) => <MenuItem key={l} value={l}>{pretty(l)}</MenuItem>)}</TextField></TableCell>
                  <TableCell><TextField select size="small" value={r.incident_risk_level} disabled={!canEdit} sx={{ width: 120 }}
                                        onChange={(e) => setRule(r.module_type, { incident_risk_level: e.target.value as RiskLevel })}>
                    {RISK_LEVELS.map((l) => <MenuItem key={l} value={l}>{pretty(l)}</MenuItem>)}</TextField></TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
          <FormControlLabel control={<Switch checked={p.is_active} disabled={!canEdit}
            onChange={(_, v) => setP({ ...p, is_active: v })} />} label="Active" />
          {save.error && <Alert severity="error">{apiError(save.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={() => onClose(false)}>Close</Button>
        {canEdit && <Button variant="contained" disabled={!p.name || save.isPending} onClick={() => save.mutate()}>Save</Button>}
      </DialogActions>
    </Dialog>
  )
}

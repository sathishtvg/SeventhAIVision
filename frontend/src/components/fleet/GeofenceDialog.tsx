/**
 * Draw a geofence or change one: its name and site, a circle or a polygon clicked on the map (or the site's own
 * boundary), the vehicle groups it applies to, and its rules - each with its severity and the people told.
 *
 * Changing the shape, the site or the groups makes every vehicle's next fix say again which side it is on,
 * without an alert; so the shape and the groups are sent only when they were changed here.
 */
import { useState } from 'react'
import {
  Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Switch,
  TextField, ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { SaveStatus } from '@/components/states'
import {
  changeGeofence, createGeofence, type Geofence, type GeofenceInput, type GeofenceKind, type GeofenceOptions, type LatLon, type Severity,
} from '@/api/fleet'
import { GeofenceMap } from './GeofenceMap'
import {
  KIND_WORDS, KINDS, SEVERITIES, draftOf, draftProblem, ruleDrafts, ruleProblem, rulesInput, shapeInput, type Draft, type RuleDraft,
} from './geofenceFormat'

export function GeofenceDialog({ geofence, options, others, organisationWide, onClose, onSaved }: {
  geofence: Geofence | null
  options: GeofenceOptions
  /** The other geofences, drawn faintly so a new one can be placed beside them. */
  others: Geofence[]
  organisationWide: boolean
  onClose: () => void
  onSaved: (g: Geofence) => void
}) {
  const [name, setName] = useState(geofence?.name ?? '')
  const [description, setDescription] = useState(geofence?.description ?? '')
  const [siteId, setSiteId] = useState(geofence?.site_id ?? (organisationWide ? '' : options.sites[0]?.id ?? ''))
  const [draft, setDraft] = useState<Draft>(() => draftOf(geofence))
  const [drawn, setDrawn] = useState(!geofence)
  const [fromSite, setFromSite] = useState(false)
  const [groups, setGroups] = useState<string[]>(geofence?.groups.map((g) => g.id) ?? [])
  const [groupsChanged, setGroupsChanged] = useState(false)
  const [rules, setRules] = useState(() => ruleDrafts(geofence))
  const [active, setActive] = useState(geofence?.is_active ?? true)

  const site = options.sites.find((s) => s.id === siteId)
  const shapeProblem = fromSite ? null : drawn ? draftProblem(draft, options.limits) : null
  const rulesProblem = KINDS.map((k) => ruleProblem(k, rules[k])).find(Boolean) ?? null
  const siteGroups = options.groups.filter((g) => !siteId || !g.site_id || g.site_id === siteId)

  const redraw = (next: Draft) => { setDraft(next); setDrawn(true); setFromSite(false) }
  const pick = (p: LatLon) => redraw(draft.kind === 'circle' ? { ...draft, center: p } : { ...draft, points: [...draft.points, p] })
  const setRule = (kind: GeofenceKind, change: Partial<RuleDraft>) => setRules((r) => ({ ...r, [kind]: { ...r[kind], ...change } }))

  const save = useMutation({
    mutationFn: () => {
      const body: GeofenceInput = { name: name.trim(), description: description.trim() || null, rules: rulesInput(rules) }
      if (!geofence || siteId !== (geofence.site_id ?? '')) body.site_id = siteId || null
      if (fromSite) body.from_site = true
      else if (drawn) body.shape = shapeInput(draft) ?? undefined
      if (!geofence || groupsChanged) body.group_ids = groups
      if (geofence && active !== geofence.is_active) body.is_active = active
      return geofence ? changeGeofence(geofence.id, body) : createGeofence(body)
    },
    onSuccess: onSaved,
  })
  const ready = !!name.trim() && (organisationWide || !!siteId) && !shapeProblem && !rulesProblem && (fromSite || drawn || !!geofence)

  return (
    <Dialog open onClose={onClose} maxWidth="lg" fullWidth aria-labelledby="geofence-title">
      <DialogTitle id="geofence-title">{geofence ? `Change ${geofence.name}` : 'Draw a geofence'}</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'grid', gap: 2, pt: 1, gridTemplateColumns: { xs: '1fr', md: 'minmax(0, 3fr) minmax(0, 2fr)' } }}>
          <Box sx={{ display: 'grid', gap: 1.5, alignContent: 'start' }}>
            <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', alignItems: 'center' }}>
              <ToggleButtonGroup size="small" exclusive value={draft.kind} aria-label="Shape"
                                 onChange={(_, kind) => kind && redraw(kind === 'circle' ? { kind, center: null, radius: '200' } : { kind, points: [] })}>
                <ToggleButton value="circle">Circle</ToggleButton>
                <ToggleButton value="polygon">Polygon</ToggleButton>
              </ToggleButtonGroup>
              {draft.kind === 'circle' && (
                <TextField size="small" type="number" label="Radius (m)" value={draft.radius} sx={{ width: 130 }}
                           onChange={(e) => redraw({ ...draft, radius: e.target.value })} />
              )}
              {draft.kind === 'polygon' && (
                <>
                  <Button size="small" disabled={!draft.points.length} onClick={() => redraw({ ...draft, points: draft.points.slice(0, -1) })}>Undo last corner</Button>
                  <Button size="small" disabled={!draft.points.length} onClick={() => redraw({ ...draft, points: [] })}>Clear</Button>
                </>
              )}
              <Button size="small" variant={fromSite ? 'contained' : 'outlined'} disabled={!site?.has_boundary}
                      onClick={() => { setFromSite(true); setDrawn(false) }}>
                Use the site's boundary
              </Button>
            </Stack>
            <Typography variant="caption" color={shapeProblem ? 'warning.main' : 'text.secondary'}>
              {fromSite ? `${site?.name}'s own boundary is used, as it is set on the site.`
                : shapeProblem ?? (draft.kind === 'circle' ? 'Click the map to move the centre.' : 'Click the map to add a corner.')}
            </Typography>
            <GeofenceMap fences={others.filter((g) => g.id !== geofence?.id)} draft={fromSite ? null : draft} onPick={pick}
                         focus={geofence?.id ?? 'new'} height={380} />
          </Box>
          <Box sx={{ display: 'grid', gap: 1.5, alignContent: 'start' }}>
            <TextField label="Name" required size="small" value={name} onChange={(e) => setName(e.target.value)} slotProps={{ htmlInput: { maxLength: 120 } }} />
            <TextField label="What it is for" size="small" value={description} onChange={(e) => setDescription(e.target.value)} />
            <TextField select label="Site" size="small" value={siteId} required={!organisationWide}
                       onChange={(e) => { setSiteId(e.target.value); setFromSite(false) }}
                       slotProps={{ select: { displayEmpty: true }, inputLabel: { shrink: true } }}
                       helperText="Without groups, it applies to every vehicle of its site">
              {organisationWide && <MenuItem value="">Whole organisation</MenuItem>}
              {options.sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
            <TextField select size="small" label="Vehicle groups" value={groups}
                       onChange={(e) => { setGroups(e.target.value as unknown as string[]); setGroupsChanged(true) }}
                       slotProps={{ select: { multiple: true, displayEmpty: true,
                         renderValue: (v) => (v as string[]).length
                           ? (v as string[]).map((id) => options.groups.find((g) => g.id === id)?.name ?? id).join(', ')
                           : 'Every vehicle of the site' },
                         inputLabel: { shrink: true } }}>
              {siteGroups.map((g) => <MenuItem key={g.id} value={g.id}>{g.name}</MenuItem>)}
            </TextField>
            {geofence && (
              <FormControlLabel control={<Switch checked={active} onChange={(e) => setActive(e.target.checked)} />}
                                label={active ? 'Looked at for every fix' : 'Switched off: no fix is looked at'} />
            )}
            <Typography variant="subtitle2" sx={{ mt: 1 }}>Alerts</Typography>
            <Typography variant="caption" color="text.secondary">
              Every entry and exit is recorded. An alert goes to the alert queue, and to the people named here when it is below high.
              It never opens a barrier or says who is driving.
            </Typography>
            {KINDS.map((kind) => <RuleRow key={kind} kind={kind} rule={rules[kind]} options={options} onChange={(c) => setRule(kind, c)} />)}
          </Box>
        </Box>
      </DialogContent>
      <DialogActions sx={{ gap: 1 }}>
        <SaveStatus state={save.isPending ? 'saving' : save.isError ? 'failed' : 'idle'} error={save.error} />
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!ready || save.isPending} onClick={() => save.mutate()}>
          {geofence ? 'Save' : 'Make the geofence'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function RuleRow({ kind, rule, options, onChange }: {
  kind: GeofenceKind; rule: RuleDraft; options: GeofenceOptions; onChange: (c: Partial<RuleDraft>) => void
}) {
  const problem = ruleProblem(kind, rule)
  return (
    <Box sx={{ border: 1, borderColor: rule.on ? 'primary.main' : 'divider', borderRadius: 1, p: 1 }}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <FormControlLabel sx={{ mr: 0 }} label={KIND_WORDS[kind]}
                          control={<Checkbox size="small" checked={rule.on} onChange={(e) => onChange({ on: e.target.checked })} />} />
        {(kind === 'dwell_inside' || kind === 'dwell_outside') && (
          <TextField size="small" type="number" label="Minutes" value={rule.minutes} disabled={!rule.on} sx={{ width: 100 }}
                     error={!!problem} onChange={(e) => onChange({ minutes: e.target.value })} />
        )}
        {kind === 'overspeed' && (
          <TextField size="small" type="number" label="km/h" value={rule.speed} disabled={!rule.on} sx={{ width: 100 }}
                     error={!!problem} onChange={(e) => onChange({ speed: e.target.value })} />
        )}
        <TextField select size="small" label="Severity" value={rule.severity} disabled={!rule.on} sx={{ width: 120 }}
                   aria-label={`Severity when it ${KIND_WORDS[kind].toLowerCase()}`}
                   onChange={(e) => onChange({ severity: e.target.value as Severity })}>
          {SEVERITIES.map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
        </TextField>
      </Stack>
      {rule.on && (
        <Stack direction="row" sx={{ gap: 1, mt: 1, flexWrap: 'wrap' }}>
          <TextField select size="small" label="People told" value={rule.user_ids} sx={{ minWidth: 160, flex: 1 }}
                     onChange={(e) => onChange({ user_ids: e.target.value as unknown as string[] })}
                     slotProps={{ select: { multiple: true, displayEmpty: true,
                       renderValue: (v) => (v as string[]).length
                         ? (v as string[]).map((id) => options.people.find((p) => p.id === id)?.full_name ?? id).join(', ') : 'Nobody named' },
                       inputLabel: { shrink: true } }}>
            {options.people.map((p) => <MenuItem key={p.id} value={p.id}>{p.full_name} · {p.role_name}</MenuItem>)}
          </TextField>
          <TextField select size="small" label="Roles told" value={rule.role_ids} sx={{ minWidth: 140, flex: 1 }}
                     onChange={(e) => onChange({ role_ids: e.target.value as unknown as number[] })}
                     slotProps={{ select: { multiple: true, displayEmpty: true,
                       renderValue: (v) => (v as number[]).length
                         ? (v as number[]).map((id) => options.roles.find((r) => r.id === id)?.name ?? id).join(', ') : 'No role' },
                       inputLabel: { shrink: true } }}>
            {options.roles.map((r) => <MenuItem key={r.id} value={r.id}>{r.name}</MenuItem>)}
          </TextField>
        </Stack>
      )}
      {problem && <Typography variant="caption" color="warning.main">{problem}</Typography>}
    </Box>
  )
}

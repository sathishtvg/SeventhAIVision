/**
 * The places of a site — its buildings, floors, gates, doors, muster points and
 * the areas people on foot mean when they say "the yard" — as somebody who
 * knows the site draws them.
 *
 * Optional: a site with no places works exactly as it did, and the map shows
 * what the platform already knew about it. A place is a point, an outline, or
 * — for a floor — a level of a building. A place that no longer exists is
 * retired, not removed.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, MenuItem, Skeleton,
  Switch, Table, TableBody, TableCell, TableContainer, TableHead, TableRow, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { LocationPickerMap } from '@/components/common/LocationPickerMap'
import { getSites } from '@/api/sites'
import { listDoors } from '@/api/access'
import { apiError, changePlace, drawPlace, listPlaces, restorePlace, retirePlace } from '@/api/siteMap'
import type { Feature, PlaceKind } from '@/api/siteMap'
import { PLACE_LABEL, about } from '@/components/securityMap/mapFormat'
import { ErrorState } from '@/components/states'

interface Draft {
  kind: PlaceKind; name: string; parentId: string; level: string; doorId: string; description: string
  latitude: number | null; longitude: number | null; outline: { lat: number; lng: number }[]
}

const blank = (lat: number | null, lng: number | null): Draft => ({
  kind: 'GATE', name: '', parentId: '', level: '', doorId: '', description: '', latitude: lat, longitude: lng, outline: [],
})

function draftOf(place: Feature): Draft {
  const d = place.detail as Record<string, string | number | boolean | null>
  return {
    kind: d.kind as PlaceKind, name: place.label ?? '', parentId: (d.parent_id as string) ?? '',
    level: d.level === null || d.level === undefined ? '' : String(d.level), doorId: (d.door_id as string) ?? '',
    description: (d.description as string) ?? '',
    latitude: d.has_point ? place.latitude : null, longitude: d.has_point ? place.longitude : null,
    outline: (place.outline ?? []).map(([lat, lng]) => ({ lat, lng })),
  }
}

interface PlaceDialogProps {
  open: boolean; siteId: string; siteAt: [number | null, number | null]; places: Feature[]; editing: Feature | null
  kinds: PlaceKind[]; onClose: () => void; onSaved: () => Promise<unknown>
}

function PlaceDialog(props: PlaceDialogProps) {
  return props.open ? <PlaceForm {...props} /> : null
}

function PlaceForm({ siteId, siteAt, places, editing, kinds, onClose, onSaved }: PlaceDialogProps) {
  const [draft, setDraft] = useState<Draft>(editing ? draftOf(editing) : blank(siteAt[0], siteAt[1]))
  const set = <K extends keyof Draft>(key: K, value: Draft[K]) => setDraft((d) => ({ ...d, [key]: value }))
  const { data: doors } = useQuery({ queryKey: ['access-doors', siteId], queryFn: () => listDoors({ site_id: siteId }) })
  const buildings = places.filter((p) => p.detail.kind === 'BUILDING' && p.id !== editing?.id)
  const namesADoor = draft.kind === 'ACCESS_POINT' || draft.kind === 'GATE'
  const outline = draft.outline.length >= 3 ? draft.outline.map((p) => [p.lat, p.lng] as [number, number]) : null
  const aFloorOfSomewhere = draft.kind === 'FLOOR' && !!draft.parentId
  const somewhere = draft.latitude !== null || outline !== null || aFloorOfSomewhere
  const act = useMutation({
    mutationFn: () => {
      const shared = {
        name: draft.name.trim(), parent_id: draft.parentId || null, level: draft.level === '' ? null : Number(draft.level),
        // A floor that is only a level of a building has no position of its own.
        latitude: aFloorOfSomewhere && !outline ? null : draft.latitude,
        longitude: aFloorOfSomewhere && !outline ? null : draft.longitude,
        polygon: outline, door_id: namesADoor ? draft.doorId || null : null, description: draft.description.trim() || null,
      }
      return (editing ? changePlace(editing.id, shared) : drawPlace({ site_id: siteId, kind: draft.kind, ...shared }))
        .then(onSaved)
    },
    onSuccess: onClose,
  })
  return (
    <Dialog open onClose={onClose} fullWidth maxWidth="md">
      <DialogTitle>{editing ? `Change ${editing.label}` : 'Draw a place'}</DialogTitle>
      <DialogContent>
        <Stack sx={{ gap: 2, mt: 0.5 }}>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField select label="What it is" value={draft.kind} disabled={!!editing} sx={{ minWidth: 190 }}
                       helperText={editing ? 'What a place is does not change. Retire it and draw another.' : undefined}
                       onChange={(e) => set('kind', e.target.value as PlaceKind)}>
              {kinds.map((k) => <MenuItem key={k} value={k}>{PLACE_LABEL[k]}</MenuItem>)}
            </TextField>
            <TextField label="Name" value={draft.name} sx={{ flex: 1, minWidth: 220 }}
                       onChange={(e) => set('name', e.target.value)} slotProps={{ htmlInput: { maxLength: 120 } }} />
          </Stack>
          <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap' }}>
            <TextField select label="Part of (a building)" value={draft.parentId} sx={{ minWidth: 220 }}
                       onChange={(e) => set('parentId', e.target.value)}>
              <MenuItem value="">Not part of a building</MenuItem>
              {buildings.map((b) => <MenuItem key={b.id} value={b.id}>{b.label}</MenuItem>)}
            </TextField>
            <TextField label="Level" type="number" value={draft.level} sx={{ width: 120 }}
                       helperText="0 is the ground floor" onChange={(e) => set('level', e.target.value)} />
            {namesADoor && (
              <TextField select label="The door it is (optional)" value={draft.doorId} sx={{ minWidth: 240 }}
                         helperText="Gives that door's events somewhere to appear on the map"
                         onChange={(e) => set('doorId', e.target.value)}>
                <MenuItem value="">No door</MenuItem>
                {(doors ?? []).map((d) => <MenuItem key={d.id} value={d.id}>{d.name}</MenuItem>)}
              </TextField>
            )}
          </Stack>
          <TextField label="Description (optional)" multiline minRows={2} value={draft.description}
                     onChange={(e) => set('description', e.target.value)} slotProps={{ htmlInput: { maxLength: 2000 } }} />
          <Box>
            <Typography variant="body2" sx={{ mb: 0.5 }}>
              Click the map to put it somewhere. For a building or an area, draw its outline as well.
              {draft.kind === 'FLOOR' && ' A floor may have neither, and then says which building it is a level of.'}
            </Typography>
            <LocationPickerMap latitude={draft.latitude} longitude={draft.longitude} height={320} allowPolygon
                               polygon={draft.outline}
                               onChange={(lat, lng) => setDraft((d) => ({ ...d, latitude: lat, longitude: lng }))}
                               onPolygonChange={(points) => set('outline', points)} />
          </Box>
          {act.isError && <Alert severity="error">{apiError(act.error)}</Alert>}
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!draft.name.trim() || !somewhere || act.isPending} onClick={() => act.mutate()}>
          {editing ? 'Save' : 'Draw it'}</Button>
      </DialogActions>
    </Dialog>
  )
}

export default function SitePlaces() {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState('')
  const [retired, setRetired] = useState(false)
  const [asking, setAsking] = useState<{ editing: Feature | null } | null>(null)
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const site = (sites ?? []).find((s) => s.id === siteId) ?? null
  const { data, isLoading, isLoadingError: dataFailed, error: dataError, refetch: refetchData } = useQuery({
    queryKey: ['site-places', siteId, retired], queryFn: () => listPlaces({ site_id: siteId, include_retired: retired }),
    enabled: !!siteId,
  })
  const done = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['site-places'] }), qc.invalidateQueries({ queryKey: ['site-map-features'] })])
  const active = useMutation({
    mutationFn: (place: Feature) => (place.detail.is_active ? retirePlace(place.id) : restorePlace(place.id)).then(done) })
  const places = data?.items ?? []
  const canManage = !!data?.can_manage
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Site Places"
                  subtitle="The buildings, floors, gates, doors and areas of a site, for the security map. Optional: a site with none works as before"
                  action={canManage && siteId ? (
                    <Button variant="contained" startIcon={<AddIcon />} onClick={() => setAsking({ editing: null })}>
                      Draw a place</Button>) : undefined} />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 220 }}
                     onChange={(e) => setSiteId(e.target.value)}>
            {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          {canManage && (
            <FormControlLabel control={<Switch checked={retired} onChange={(_, v) => setRetired(v)} />}
                              label="Show retired places" />)}
        </Stack>
      </GlassCard>
      <GlassCard sx={{ p: 2 }}>
        {!siteId ? <Alert severity="info">Choose a site to see or draw its places.</Alert>
          : isLoading ? <Skeleton height={200} /> : dataFailed ? <ErrorState compact error={dataError} onRetry={refetchData} /> : !places.length ? (
            <Alert severity="info">No places have been drawn for this site. The security map shows its cameras,
              checkpoints and guards all the same.</Alert>
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Place</TableCell><TableCell>What it is</TableCell><TableCell>Drawn as</TableCell>
                    <TableCell /><TableCell />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {places.map((p) => {
                    const live = !!p.detail.is_active
                    return (
                      <TableRow key={p.id} data-testid="place-row" sx={{ opacity: live ? 1 : 0.55 }}>
                        <TableCell>
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>{p.label}</Typography>
                          {!!p.detail.description && (
                            <Typography variant="caption" color="text.secondary">{String(p.detail.description)}</Typography>)}
                        </TableCell>
                        <TableCell>{about(p)}</TableCell>
                        <TableCell>
                          {p.outline ? `An outline of ${p.outline.length} points` : p.latitude !== null ? 'A point'
                            : 'A level of its building'}</TableCell>
                        <TableCell>{!live && <Chip size="small" variant="outlined" label="Retired" />}</TableCell>
                        <TableCell align="right" sx={{ whiteSpace: 'nowrap' }}>
                          {canManage && live && <Button size="small" onClick={() => setAsking({ editing: p })}>Change</Button>}
                          {canManage && (
                            <Button size="small" color={live ? 'warning' : 'primary'} disabled={active.isPending}
                                    onClick={() => active.mutate(p)}>{live ? 'Retire' : 'Restore'}</Button>)}
                        </TableCell>
                      </TableRow>
                    )
                  })}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        {active.isError && <Alert severity="error" sx={{ mt: 1.5 }}>{apiError(active.error)}</Alert>}
      </GlassCard>
      <PlaceDialog open={!!asking} siteId={siteId} siteAt={[site?.latitude ?? null, site?.longitude ?? null]}
                   places={places.filter((p) => p.detail.is_active)} editing={asking?.editing ?? null}
                   kinds={data?.kinds ?? []} onClose={() => setAsking(null)} onSaved={done} />
    </Box>
  )
}

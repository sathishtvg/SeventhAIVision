/**
 * Privacy areas for drones (4 November 2026; Phase 5 of the known limits): places on the map a drone's camera is not
 * to show. While a drone is in one, or within its margin, its whole picture is withheld (the server does it:
 * backend/app/services/drone_privacy.py). Drawn and taken away - never edited - by privacy:manage.
 */
import { useMemo, useState } from 'react'
import {
  Alert, Button, Grid, MenuItem, Skeleton, Table, TableBody, TableCell, TableRow, TextField, ToggleButton,
  ToggleButtonGroup, Typography,
} from '@mui/material'
import { Circle, Polygon, Tooltip } from 'react-leaflet'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import { drawPrivacyArea, listPrivacyAreas, removePrivacyArea } from '@/api/dronePrivacy'
import type { PrivacyArea } from '@/api/dronePrivacy'
import type { ZoneShape } from '@/api/drones'
import { DroneMap, FitTo } from '@/components/drones/droneUi'
import { ZoneDrawer } from '@/components/drones/MapEditors'
import type { ZoneDraft } from '@/components/drones/geo'
import { draftComplete, emptyDraft } from '@/components/drones/geo'
import { ErrorState, errorText } from '@/components/states'

const GREY = '#8a8f98'

/** The areas on a map, grey and hatched-looking, each with its margin as a dashed outline. */
export function PrivacyLayer({ areas }: { areas: PrivacyArea[] }) {
  return (
    <>
      {areas.map((a) => {
        const tip = <Tooltip sticky>{`Privacy area: ${a.name} · picture withheld here and ${a.margin_m} m around`}</Tooltip>
        if (a.shape === 'CIRCLE' && a.center_latitude != null && a.center_longitude != null && a.radius_m) {
          const at: [number, number] = [a.center_latitude, a.center_longitude]
          return (
            <span key={a.id}>
              <Circle center={at} radius={Number(a.radius_m)} pathOptions={{ color: GREY, weight: 2, fillOpacity: 0.35 }}>{tip}</Circle>
              {Number(a.margin_m) > 0 && (
                <Circle center={at} radius={Number(a.radius_m) + Number(a.margin_m)}
                        pathOptions={{ color: GREY, weight: 1, fillOpacity: 0, dashArray: '4 6' }} />)}
            </span>
          )
        }
        const pts = (a.polygon ?? []).map((p) => [p.lat, p.lng] as [number, number])
        if (pts.length < 3) return null
        return (
          <Polygon key={a.id} positions={pts} pathOptions={{ color: GREY, weight: 2, fillOpacity: 0.35, dashArray: '2 4' }}>
            {tip}
          </Polygon>
        )
      })}
    </>
  )
}

export function PrivacyAreasTab() {
  const qc = useQueryClient()
  const canDraw = usePermission('privacy:manage')
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const [siteId, setSiteId] = useState('')
  const site = (sites ?? []).find((s) => s.id === siteId)
  const { data, isLoading, isLoadingError: areasFailed, error: areasError, refetch: refetchAreas } = useQuery({
    queryKey: ['drone-privacy-areas'], queryFn: listPrivacyAreas,
  })
  const [drawing, setDrawing] = useState(false)
  const [draft, setDraft] = useState<ZoneDraft>(emptyDraft('POLYGON'))
  const [name, setName] = useState('')
  const [margin, setMargin] = useState('25')
  const mine = (data?.areas ?? []).filter((a) => a.site_id === siteId)
  const reset = () => { setDrawing(false); setDraft(emptyDraft('POLYGON')); setName(''); setMargin(String(data?.margin_default_m ?? 25)) }
  const draw = useMutation({
    mutationFn: () => drawPrivacyArea({
      site_id: siteId, name: name.trim(), shape: draft.shape,
      polygon: draft.shape === 'CIRCLE' ? null : draft.points,
      center_latitude: draft.shape === 'CIRCLE' ? draft.center?.lat ?? null : null,
      center_longitude: draft.shape === 'CIRCLE' ? draft.center?.lng ?? null : null,
      radius_m: draft.shape === 'CIRCLE' ? draft.radius_m : null, margin_m: Number(margin),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['drone-privacy-areas'] }); reset() },
  })
  const remove = useMutation({
    mutationFn: (id: string) => removePrivacyArea(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['drone-privacy-areas'] }),
  })
  const fit = useMemo(() => {
    const pts: [number, number][] = []
    mine.forEach((a) => {
      if (a.center_latitude != null && a.center_longitude != null) pts.push([a.center_latitude, a.center_longitude])
      ;(a.polygon ?? []).forEach((p) => pts.push([p.lat, p.lng]))
    })
    if (!pts.length && site?.latitude != null && site?.longitude != null) pts.push([site.latitude, site.longitude])
    return pts
  }, [mine, site])
  const most = data?.margin_most_m ?? 200
  const ready = name.trim().length > 0 && draftComplete(draft) && /^\d+(\.\d+)?$/.test(margin) && Number(margin) <= most

  if (isLoading) return <Skeleton height={300} />
  if (areasFailed || !data) return <ErrorState compact error={areasError} onRetry={refetchAreas} />
  return (
    <Grid container spacing={2} data-testid="privacy-areas">
      <Grid size={{ xs: 12, lg: 8 }}>
        <GlassCard sx={{ p: 2 }}>
          <Stack direction="row" sx={{ gap: 2, mb: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
            <TextField select size="small" label="Site" value={siteId} sx={{ minWidth: 220 }}
                       onChange={(e) => { setSiteId(e.target.value); reset() }}>
              {(sites ?? []).map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </TextField>
            {drawing && (
              <ToggleButtonGroup size="small" exclusive value={draft.shape}
                                 onChange={(_, v: ZoneShape | null) => v && setDraft(emptyDraft(v))}>
                <ToggleButton value="POLYGON">Polygon</ToggleButton>
                <ToggleButton value="RECTANGLE">Rectangle</ToggleButton>
                <ToggleButton value="CIRCLE">Circle</ToggleButton>
              </ToggleButtonGroup>
            )}
          </Stack>
          {siteId ? (
            <DroneMap height={480}>
              <PrivacyLayer areas={mine} />
              {drawing && <ZoneDrawer draft={draft} onChange={setDraft} color={GREY} />}
              <FitTo points={fit} />
            </DroneMap>
          ) : <Typography color="text.secondary" sx={{ py: 6, textAlign: 'center' }}>Choose a site to see its privacy areas.</Typography>}
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>{data.note}</Typography>
        </GlassCard>
      </Grid>
      <Grid size={{ xs: 12, lg: 4 }}>
        <GlassCard sx={{ p: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Privacy areas{site ? `: ${site.name}` : ''}</Typography>
          {!siteId ? null : !mine.length ? (
            <Typography variant="body2" color="text.secondary">None drawn for this site.</Typography>
          ) : (
            <Table size="small">
              <TableBody>
                {mine.map((a) => (
                  <TableRow key={a.id} data-testid="privacy-area-row">
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{a.name}</Typography>
                      <Typography variant="caption" color="text.secondary">
                        {`${a.shape.toLowerCase()}, and ${Number(a.margin_m)} m around it`}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      {canDraw && <Button size="small" color="error" disabled={remove.isPending}
                                          onClick={() => remove.mutate(a.id)}>Take away</Button>}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
          {canDraw && siteId && !drawing && (
            <Button size="small" variant="outlined" sx={{ mt: 1.5 }} onClick={() => setDrawing(true)}>Draw an area</Button>)}
          {drawing && (
            <Stack sx={{ gap: 1.5, mt: 1.5 }}>
              <TextField size="small" label="Name" value={name} onChange={(e) => setName(e.target.value)}
                         helperText="What it is, e.g. the neighbour's yard" />
              <TextField size="small" label="Margin around it (m)" value={margin} onChange={(e) => setMargin(e.target.value)}
                         helperText={`0 to ${most}. The picture is withheld this far around the area too.`} />
              <Typography variant="caption" color="text.secondary">
                {draft.shape === 'CIRCLE' ? 'Click the centre, then a point on the edge.'
                  : draft.shape === 'RECTANGLE' ? 'Click two opposite corners.' : 'Click each corner in turn.'}
              </Typography>
              <Stack direction="row" sx={{ gap: 1 }}>
                <Button size="small" onClick={reset} disabled={draw.isPending}>Cancel</Button>
                <Button size="small" variant="contained" disabled={!ready || draw.isPending} onClick={() => draw.mutate()}>
                  Draw it
                </Button>
              </Stack>
            </Stack>
          )}
          {(draw.error || remove.error) && (
            <Alert severity="error" sx={{ mt: 1.5 }}>{errorText(draw.error ?? remove.error)}</Alert>)}
          {!canDraw && (
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1.5 }}>
              Whoever may draw a camera's privacy zones draws these.
            </Typography>)}
        </GlassCard>
      </Grid>
    </Grid>
  )
}

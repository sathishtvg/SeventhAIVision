/**
 * The platform owner's Drone Patrol licence for one organisation: on or off,
 * until when, and how many drones, missions and sites.
 *
 * Shown beside the AI modules and the product catalogue in Manage Licenses.
 * It is saved as a whole, with a button — a limit is not something to change
 * by brushing past a switch — and what the server makes of it (licensed, or
 * why not) is shown as the server says it.
 */
import { useState } from 'react'
import { Alert, Box, Button, Chip, CircularProgress, FormControlLabel, Switch, TextField, Typography } from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { apiError, getDroneLicense, putDroneLicense } from '@/api/platformDroneLicense'
import type { DroneLicense } from '@/api/platformDroneLicense'
import { asLimit, bodyOf, draftOf, licenceDay, limitsValid } from '@/components/platform/droneLicenseFormat'
import type { Draft } from '@/components/platform/droneLicenseFormat'

function Form({ tenantId, licence }: { tenantId: string; licence: DroneLicense }) {
  const qc = useQueryClient()
  const [d, setD] = useState<Draft>(() => draftOf(licence))
  const changed = JSON.stringify(d) !== JSON.stringify(draftOf(licence))
  const save = useMutation({
    mutationFn: () => putDroneLicense(tenantId, bodyOf(d)),
    onSuccess: (saved) => { qc.setQueryData(['drone-license', tenantId], saved); setD(draftOf(saved)) },
  })
  const set = (over: Partial<Draft>) => { setD({ ...d, ...over }); save.reset() }
  const limit = (label: string, key: 'drones' | 'missions' | 'sites') => {
    const wrong = Number.isNaN(asLimit(d[key]))
    return (
      <TextField size="small" label={label} value={d[key]} onChange={(e) => set({ [key]: e.target.value })}
                 error={wrong} helperText={wrong ? 'A whole number, or empty' : 'Empty: no limit'}
                 slotProps={{ htmlInput: { inputMode: 'numeric', maxLength: 7 } }} sx={{ flex: 1, minWidth: 110 }} />)
  }
  return (
    <Box data-testid="drone-license">
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1.5 }}>
        <Chip size="small" color={licence.licensed ? 'success' : 'default'} label={licence.licensed ? 'Licensed' : 'Not licensed'} />
        {!licence.licensed && licence.reason && (
          <Typography variant="caption" color="text.secondary" data-testid="drone-license-reason">{licence.reason}</Typography>)}
        {licence.licensed && licence.licensed_at && (
          <Typography variant="caption" color="text.secondary">Switched on {licenceDay(licence.licensed_at)}</Typography>)}
      </Stack>
      <FormControlLabel sx={{ mb: 1 }}
        label={d.on ? 'Drone Patrol is on for this organisation' : 'Drone Patrol is off for this organisation'}
        control={<Switch checked={d.on} onChange={(_, v) => set({ on: v })} />} />
      <TextField fullWidth size="small" type="date" label="Last day of the licence" value={d.until} sx={{ mb: 2 }}
                 onChange={(e) => set({ until: e.target.value })}
                 helperText="Empty: it does not expire. It runs to the end of that day, UTC"
                 slotProps={{ inputLabel: { shrink: true } }} />
      <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', mb: 2 }}>
        {limit('Most drones', 'drones')}{limit('Most missions', 'missions')}{limit('Most sites', 'sites')}
      </Stack>
      <TextField fullWidth size="small" multiline minRows={2} label="Notes" value={d.notes} sx={{ mb: 2 }}
                 onChange={(e) => set({ notes: e.target.value })} slotProps={{ htmlInput: { maxLength: 2000 } }} />
      {!!save.error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(save.error)}</Alert>}
      {save.isSuccess && !changed && <Alert severity="success" sx={{ mb: 2 }}>Saved.</Alert>}
      <Button variant="contained" disabled={!changed || !limitsValid(d) || save.isPending} onClick={() => save.mutate()}>
        Save the licence
      </Button>
      <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1.5 }}>
        Switching it off stops new drones, missions and patrols for this organisation. What it has recorded stays
        readable. The change is written in the organisation&apos;s own audit log.
        {licence.updated_at ? ` Last changed ${licenceDay(licence.updated_at)}.` : ''}
      </Typography>
    </Box>
  )
}

export function DroneLicensePanel({ tenantId }: { tenantId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['drone-license', tenantId], queryFn: () => getDroneLicense(tenantId), retry: false })
  if (isLoading) return <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}><CircularProgress size={32} /></Box>
  // The platform's own organisation runs no drone patrols: the server says so, and that is shown as it is.
  if (error) return <Alert severity="info" data-testid="drone-license-refused">{apiError(error)}</Alert>
  if (!data) return null
  return <Form tenantId={tenantId} licence={data} />
}

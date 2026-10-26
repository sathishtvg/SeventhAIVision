/**
 * The platform owner's licence for the AI module, for one organisation: on or off, until when, which features,
 * which chat model, and how many assistant replies a month.
 *
 * Shown as its own tab in Manage Licenses, beside the camera AI modules and Drone Patrol. Saved as a whole, with a
 * button, and what the server makes of it (licensed, or why not) is shown in the server's own words.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, FormControlLabel, MenuItem, Switch, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { getAiLicense, putAiLicense } from '@/api/platformAiLicense'
import type { AiLicense } from '@/api/platformAiLicense'
import { apiError } from '@/api/platformDroneLicense'
import { licenceDay } from '@/components/platform/droneLicenseFormat'
import { aiBodyOf, aiDraftOf, aiDraftProblem, toggled } from '@/components/platform/aiLicenseFormat'
import type { AiDraft } from '@/components/platform/aiLicenseFormat'
import { TableBlockSkeleton } from '@/components/states'

const GROUPS: Array<{ group: 'learning' | 'assistant'; title: string }> = [
  { group: 'learning', title: 'Learning from work activity (free, built in)' },
  { group: 'assistant', title: 'Assistant' },
]

function Form({ tenantId, licence }: { tenantId: string; licence: AiLicense }) {
  const qc = useQueryClient()
  const [d, setD] = useState<AiDraft>(() => aiDraftOf(licence))
  const changed = JSON.stringify(d) !== JSON.stringify(aiDraftOf(licence))
  const problem = aiDraftProblem(d)
  const save = useMutation({
    mutationFn: () => putAiLicense(tenantId, aiBodyOf(d)),
    onSuccess: (saved) => { qc.setQueryData(['ai-license', tenantId], saved); setD(aiDraftOf(saved)) },
  })
  const set = (over: Partial<AiDraft>) => { setD({ ...d, ...over }); save.reset() }
  return (
    <Box data-testid="ai-license">
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 1.5 }}>
        <Chip size="small" color={licence.licensed ? 'success' : 'default'} label={licence.licensed ? 'Licensed' : 'Not licensed'} />
        {!licence.licensed && licence.reason && (
          <Typography variant="caption" color="text.secondary" data-testid="ai-license-reason">{licence.reason}</Typography>)}
        {licence.licensed && licence.licensed_at && (
          <Typography variant="caption" color="text.secondary">Switched on {licenceDay(licence.licensed_at)}</Typography>)}
      </Stack>
      <FormControlLabel sx={{ mb: 1 }}
        label={d.on ? 'The AI module is on for this organisation' : 'The AI module is off for this organisation'}
        control={<Switch checked={d.on} onChange={(_, v) => set({ on: v })} />} />
      <TextField fullWidth size="small" type="date" label="Last day of the licence" value={d.until} sx={{ mb: 2 }}
                 onChange={(e) => set({ until: e.target.value })}
                 helperText="Empty: it does not expire. It runs to the end of that day, UTC"
                 slotProps={{ inputLabel: { shrink: true } }} />

      {GROUPS.map(({ group, title }) => (
        <Box key={group} sx={{ mb: 1.5 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>{title}</Typography>
          {licence.catalogue.filter((f) => f.group === group).map((f) => (
            <FormControlLabel key={f.key} sx={{ display: 'flex', alignItems: 'flex-start', mr: 0, mt: 0.5 }}
              control={<Checkbox size="small" checked={d.features.includes(f.key)}
                                 onChange={() => set({ features: toggled(d.features, f.key) })}
                                 slotProps={{ input: { 'aria-label': f.title } }} />}
              label={
                <Box sx={{ pt: 0.75 }}>
                  <Typography variant="body2">{f.title}{!f.ready && (
                    <Chip size="small" variant="outlined" label="Coming" sx={{ ml: 1, height: 18 }} />)}</Typography>
                  <Typography variant="caption" color="text.secondary">{f.description}</Typography>
                </Box>} />
          ))}
        </Box>
      ))}

      <Typography variant="subtitle2" sx={{ fontWeight: 600, mt: 1 }}>The assistant&apos;s chat model</Typography>
      <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', mt: 1, mb: 2 }}>
        <TextField select size="small" label="Model" value={d.provider} sx={{ flex: 1, minWidth: 180 }}
                   onChange={(e) => set({ provider: e.target.value as AiDraft['provider'] })}
                   helperText={d.provider === 'HOSTED' ? 'Paid per reply' : 'Runs on our own server, no per-reply cost'}>
          <MenuItem value="LOCAL">Free built-in</MenuItem>
          <MenuItem value="HOSTED">Hosted (Claude)</MenuItem>
        </TextField>
        <TextField size="small" label="Replies a month" value={d.limit} sx={{ flex: 1, minWidth: 150 }}
                   onChange={(e) => set({ limit: e.target.value })}
                   error={Boolean(problem)} helperText={problem ?? 'Empty: no limit. Raise it at any time.'}
                   slotProps={{ htmlInput: { inputMode: 'numeric', maxLength: 8 } }} />
      </Stack>

      <TextField fullWidth size="small" multiline minRows={2} label="Notes" value={d.notes} sx={{ mb: 2 }}
                 onChange={(e) => set({ notes: e.target.value })} slotProps={{ htmlInput: { maxLength: 2000 } }} />
      {!!save.error && <Alert severity="error" sx={{ mb: 2 }}>{apiError(save.error)}</Alert>}
      {save.isSuccess && !changed && <Alert severity="success" sx={{ mb: 2 }}>Saved.</Alert>}
      <Button variant="contained" disabled={!changed || Boolean(problem) || save.isPending} onClick={() => save.mutate()}>
        Save the licence
      </Button>
      <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1.5 }}>
        The organisation&apos;s admin then switches on each licensed feature and chooses who uses it. Switching the
        licence off stops new suggestions and replies; what was recorded stays readable. The change is written in the
        organisation&apos;s own audit log.
        {licence.updated_at ? ` Last changed ${licenceDay(licence.updated_at)}.` : ''}
      </Typography>
    </Box>
  )
}

export function AiLicensePanel({ tenantId }: { tenantId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['ai-license', tenantId], queryFn: () => getAiLicense(tenantId), retry: false })
  if (isLoading) return <TableBlockSkeleton />
  // The platform's own organisation does not use the module: the server says so, and that is shown as it is.
  if (error) return <Alert severity="info" data-testid="ai-license-refused">{apiError(error)}</Alert>
  if (!data) return null
  return <Form tenantId={tenantId} licence={data} />
}

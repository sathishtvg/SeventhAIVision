/**
 * AI Settings: the company admin's controls for the AI module (ai:configure, Admin and Manager).
 *
 * The platform owner's licence decides what the organisation has; this page decides, for each licensed feature,
 * whether it is on, which roles use it, at which sites, how sure it must be before it warns, and whether it keeps
 * learning. Switching a feature off is always possible. Each feature is saved on its own, with a button.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, FormControlLabel, Grid, MenuItem, Skeleton, Switch, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { getSites } from '@/api/sites'
import { getAiSettings, putAiSetting } from '@/api/ai'
import type { AiFeatureSetting, AiFeatureSettingBody, AiSettings as Settings } from '@/api/ai'
import { apiError } from '@/api/platformDroneLicense'
import { ErrorState } from '@/components/states'
import { ROLE_NAMES, draftOfSetting, featureLock, roleToggled } from './aiSettingsFormat'

export default function AiSettings() {
  const settings = useQuery({ queryKey: ['ai-settings'], queryFn: getAiSettings })
  const sites = useQuery({ queryKey: ['sites', 'ai-settings'], queryFn: () => getSites() })
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="AI Settings"
                  subtitle="Which AI features are on, who uses them and where. The AI suggests; a person decides." />
      {settings.isLoadingError ? <ErrorState error={settings.error} onRetry={settings.refetch} />
        : settings.isLoading || !settings.data ? <Skeleton variant="rounded" height={320} />
          : <Body data={settings.data} sites={sites.data ?? []} />}
    </Box>
  )
}

function Body({ data, sites }: { data: Settings; sites: Array<{ id: string; name: string }> }) {
  const learning = data.features.filter((f) => f.group === 'learning')
  const assistant = data.features.filter((f) => f.group === 'assistant')
  return (
    <>
      <GlassCard sx={{ p: 2, mb: 2 }} data-testid="ai-licence-summary">
        <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
          <Chip size="small" color={data.licensed ? 'success' : 'default'}
                label={data.licensed ? 'Licensed' : 'Not licensed'} />
          {data.licensed ? (
            <Typography variant="body2" color="text.secondary">
              Chat model: {data.assistant_provider === 'HOSTED' ? 'hosted (Claude)' : 'free built-in'}
              {data.monthly_reply_limit != null ? ` · ${data.monthly_reply_limit.toLocaleString()} replies a month` : ''}
              {data.expires_at ? ` · until ${new Date(data.expires_at).toLocaleDateString()}` : ''}
            </Typography>
          ) : <Typography variant="body2" color="text.secondary">{data.reason}</Typography>}
        </Stack>
      </GlassCard>
      <Typography variant="h6" sx={{ mb: 1 }}>Learning from work activity</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
        Each feature learns only from this organisation&apos;s own records, every night, and from what your people
        accept or dismiss. It shows how sure it is and why. It never changes a roster, a shift or an alert by itself.
      </Typography>
      <Grid container spacing={2} sx={{ mb: 3 }}>
        {learning.map((f) => (
          <Grid key={f.key} size={{ xs: 12, lg: 6 }}><FeatureCard feature={f} sites={sites} /></Grid>))}
      </Grid>
      <Typography variant="h6" sx={{ mb: 1 }}>Assistant</Typography>
      <Grid container spacing={2}>
        {assistant.map((f) => (
          <Grid key={f.key} size={{ xs: 12, lg: 6 }}><FeatureCard feature={f} sites={sites} /></Grid>))}
      </Grid>
    </>
  )
}

function FeatureCard({ feature, sites }: { feature: AiFeatureSetting; sites: Array<{ id: string; name: string }> }) {
  const qc = useQueryClient()
  const [d, setD] = useState<AiFeatureSettingBody>(() => draftOfSetting(feature))
  const changed = JSON.stringify(d) !== JSON.stringify(draftOfSetting(feature))
  const lock = featureLock(feature)
  const save = useMutation({
    mutationFn: () => putAiSetting(feature.key, d),
    onSuccess: (saved) => {
      qc.setQueryData<Settings>(['ai-settings'], (old) => old && ({
        ...old, features: old.features.map((f) => (f.key === saved.key ? saved : f)) }))
      setD(draftOfSetting(saved))
      qc.invalidateQueries({ queryKey: ['ai-status'] })
    },
  })
  const set = (over: Partial<AiFeatureSettingBody>) => { setD({ ...d, ...over }); save.reset() }
  const canTurnOn = lock === null
  const isLearning = feature.group === 'learning'
  return (
    <GlassCard sx={{ p: 2, height: '100%' }} data-testid={`ai-feature-${feature.key}`}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>{feature.title}</Typography>
        {lock && <Chip size="small" variant="outlined" label={lock} />}
        {!lock && <Chip size="small" color={feature.enabled ? 'success' : 'default'} label={feature.enabled ? 'On' : 'Off'} />}
      </Stack>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{feature.description}</Typography>
      <FormControlLabel
        label={d.enabled ? 'On' : 'Off'}
        control={<Switch checked={d.enabled} disabled={!canTurnOn && !d.enabled}
                         onChange={(_, v) => set({ enabled: v })}
                         slotProps={{ input: { 'aria-label': `${feature.title} on or off` } }} />} />
      <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>Who uses it</Typography>
      <Stack direction="row" sx={{ flexWrap: 'wrap' }}>
        {feature.allowed_roles.map((r) => (
          <FormControlLabel key={r} sx={{ mr: 1.5 }} label={ROLE_NAMES[r] ?? `Role ${r}`}
            control={<Checkbox size="small" checked={d.role_ids.includes(r)}
                               onChange={() => set({ role_ids: roleToggled(d.role_ids, r) })} />} />
        ))}
      </Stack>
      <TextField select size="small" fullWidth label="Sites" sx={{ mt: 1 }}
                 value={d.site_ids === null ? ['__all__'] : d.site_ids}
                 slotProps={{ select: { multiple: true,
                   renderValue: (v) => ((v as string[]).includes('__all__') ? 'Every site'
                     : (v as string[]).map((id) => sites.find((s) => s.id === id)?.name ?? 'A site').join(', ')) } }}
                 onChange={(e) => {
                   const v = e.target.value as unknown as string[]
                   const picked = v.filter((x) => x !== '__all__')
                   // Choosing "Every site" after sites were picked goes back to every site.
                   set({ site_ids: v[v.length - 1] === '__all__' || picked.length === 0 ? null : picked })
                 }}>
        <MenuItem value="__all__">Every site</MenuItem>
        {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
      </TextField>
      {isLearning && (
        <Stack direction="row" sx={{ gap: 1.5, alignItems: 'center', flexWrap: 'wrap', mt: 1.5 }}>
          <TextField select size="small" label="Warn when" value={d.sensitivity} sx={{ minWidth: 220 }}
                     onChange={(e) => set({ sensitivity: e.target.value as AiFeatureSettingBody['sensitivity'] })}>
            <MenuItem value="HIGH">Fairly likely (more warnings)</MenuItem>
            <MenuItem value="MEDIUM">Likely</MenuItem>
            <MenuItem value="LOW">Very likely (fewer warnings)</MenuItem>
          </TextField>
          <FormControlLabel label="Keeps learning"
            control={<Switch checked={d.learning_enabled} onChange={(_, v) => set({ learning_enabled: v })} />} />
        </Stack>
      )}
      {!!save.error && <Alert severity="error" sx={{ mt: 1.5 }}>{apiError(save.error)}</Alert>}
      {save.isSuccess && !changed && <Alert severity="success" sx={{ mt: 1.5 }}>Saved.</Alert>}
      <Button sx={{ mt: 1.5 }} variant="contained" size="small" disabled={!changed || save.isPending}
              onClick={() => save.mutate()}>
        Save
      </Button>
    </GlassCard>
  )
}

/**
 * AI Insights: the warnings the AI module learned to give, and what each feature has learned (ai:insights:read).
 *
 * Each warning says how sure it is and why, in words. A supervisor accepts it (and acts) or dismisses it; the
 * decision is recorded, written to the audit log, and is what the feature learns from. Accepting changes nothing by
 * itself: the shift, the roster and the standby list stay as they are until a person changes them.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Grid, LinearProgress, Skeleton, Tab, Tabs, TextField, ToggleButton, ToggleButtonGroup,
  Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { decideAiInsight, getAiInsights, getAiLearning, runAiLearning } from '@/api/ai'
import type { AiInsight, AiLearning } from '@/api/ai'
import { apiError } from '@/api/platformDroneLicense'
import { EmptyState, ErrorState } from '@/components/states'
import { barWords, howSure, rankWords, statusWords } from './aiInsightsFormat'

type Show = 'OPEN' | 'DECIDED' | 'ALL'

export default function AiInsights() {
  const [show, setShow] = useState<Show>('OPEN')
  const [feature, setFeature] = useState<string>('all')
  const insights = useQuery({
    queryKey: ['ai-insights', show], queryFn: () => getAiInsights({ status: show }), placeholderData: (prev) => prev })
  const learning = useQuery({ queryKey: ['ai-learning'], queryFn: getAiLearning })
  const titles = Object.fromEntries((learning.data?.features ?? []).map((f) => [f.key, f.title]))
  const items = (insights.data?.items ?? []).filter((i) => feature === 'all' || i.feature_key === feature)
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="AI Insights"
                  subtitle="Warnings learned from your own records. Each says how sure it is and why; you decide." />
      {insights.isLoadingError ? <ErrorState error={insights.error} onRetry={insights.refetch} /> : (
        <>
          <Stack direction="row" sx={{ gap: 2, alignItems: 'center', flexWrap: 'wrap', mb: 2 }}>
            <ToggleButtonGroup size="small" exclusive value={show} onChange={(_, v) => v && setShow(v)}>
              <ToggleButton value="OPEN">Open</ToggleButton>
              <ToggleButton value="DECIDED">Decided</ToggleButton>
              <ToggleButton value="ALL">All</ToggleButton>
            </ToggleButtonGroup>
            <Tabs value={feature} onChange={(_, v) => setFeature(v)} variant="scrollable" scrollButtons="auto">
              <Tab value="all" label="All" />
              {(insights.data?.features ?? []).map((k) => <Tab key={k} value={k} label={titles[k] ?? k} />)}
            </Tabs>
          </Stack>
          {insights.isLoading || !insights.data ? <Skeleton variant="rounded" height={240} /> : (
            insights.data.features.length === 0 ? (
              <Alert severity="info">
                No AI feature is switched on for your role. Your administrator chooses who sees them in AI Settings.
              </Alert>
            ) : items.length === 0 ? (
              <EmptyState title={show === 'OPEN' ? 'No warnings right now' : 'Nothing here yet'}
                          hint="When a feature has something to warn about, it appears here with its reasons." />
            ) : (
              <Grid container spacing={2} sx={{ mb: 3 }}>
                {items.map((i) => <Grid key={i.id} size={{ xs: 12, lg: 6 }}><Warning item={i} /></Grid>)}
              </Grid>
            )
          )}
        </>
      )}
      <Typography variant="h6" sx={{ mt: 3, mb: 1 }}>How it learns</Typography>
      {learning.isLoadingError ? <ErrorState error={learning.error} onRetry={learning.refetch} />
        : learning.isLoading || !learning.data ? <Skeleton variant="rounded" height={160} /> : (
          <Grid container spacing={2}>
            {learning.data.features.map((f) => <Grid key={f.key} size={{ xs: 12, lg: 6 }}><Learned feature={f} /></Grid>)}
          </Grid>
        )}
    </Box>
  )
}

function Warning({ item }: { item: AiInsight }) {
  const qc = useQueryClient()
  const canDecide = usePermission('ai:insights:decide')
  const [note, setNote] = useState('')
  const decide = useMutation({
    mutationFn: (decision: 'ACCEPT' | 'DISMISS') => decideAiInsight(item.id, { decision, note: note.trim() || null }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ai-insights'] })
      qc.invalidateQueries({ queryKey: ['ai-learning'] })
    },
  })
  const bar = item.subject_type === 'guard_week' ? 100 : Math.min(100, Math.round(item.score * 100))
  return (
    <GlassCard sx={{ p: 2, height: '100%' }} data-testid={`ai-warning-${item.id}`}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap', mb: 0.5 }}>
        <Chip size="small" label={item.feature_title} />
        <Chip size="small" variant="outlined" label={statusWords(item)}
              color={item.status === 'OPEN' ? 'warning' : 'default'} />
        {item.site_name && <Typography variant="caption" color="text.secondary">{item.site_name}</Typography>}
      </Stack>
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>{item.title}</Typography>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', my: 1 }}>
        <LinearProgress variant="determinate" value={bar} sx={{ flex: 1, height: 8, borderRadius: 4 }}
                        color={item.subject_type === 'guard_week' ? 'info' : bar >= 70 ? 'error' : 'warning'} />
        <Typography variant="body2" sx={{ minWidth: 150, textAlign: 'right' }}>{howSure(item)}</Typography>
      </Stack>
      <Typography variant="caption" color="text.secondary">Why</Typography>
      <Box component="ul" sx={{ mt: 0.25, mb: 1, pl: 2.5 }}>
        {item.reasons.map((r) => <li key={r.factor}><Typography variant="body2">{r.words}</Typography></li>)}
      </Box>
      {item.detail && <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>{item.detail}</Typography>}
      {item.status === 'OPEN' ? (canDecide ? (
        <>
          <TextField size="small" fullWidth label="Note (optional)" value={note} sx={{ mb: 1 }}
                     onChange={(e) => setNote(e.target.value)} slotProps={{ htmlInput: { maxLength: 1000 } }} />
          {!!decide.error && <Alert severity="error" sx={{ mb: 1 }}>{apiError(decide.error)}</Alert>}
          <Stack direction="row" sx={{ gap: 1 }}>
            <Button size="small" variant="contained" disabled={decide.isPending} onClick={() => decide.mutate('ACCEPT')}>
              Accept
            </Button>
            <Button size="small" variant="outlined" disabled={decide.isPending} onClick={() => decide.mutate('DISMISS')}>
              Dismiss
            </Button>
          </Stack>
        </>
      ) : <Typography variant="caption" color="text.secondary">A supervisor or administrator decides this.</Typography>
      ) : (
        <Typography variant="caption" color="text.secondary" component="div">
          {item.decided_by_name ? `${statusWords(item)} by ${item.decided_by_name}` : statusWords(item)}
          {item.decided_at ? ` on ${new Date(item.decided_at).toLocaleString()}` : ''}
          {item.decision_note ? ` — ${item.decision_note}` : ''}
        </Typography>
      )}
    </GlassCard>
  )
}

function Learned({ feature }: { feature: AiLearning }) {
  const qc = useQueryClient()
  const canRun = usePermission('ai:configure')
  const run = useMutation({
    mutationFn: () => runAiLearning(feature.key),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['ai-learning'] })
      qc.invalidateQueries({ queryKey: ['ai-insights'] })
    },
  })
  const m = feature.model
  const ranked = rankWords(m?.metrics?.auc_on_newest as number | null | undefined)
  const recent = feature.last_30_days
  return (
    <GlassCard sx={{ p: 2, height: '100%' }} data-testid={`ai-learning-${feature.key}`}>
      <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>{feature.title}</Typography>
        <Chip size="small" color={m?.status === 'READY' ? 'success' : 'default'}
              label={m ? (m.status === 'READY' ? 'Learned' : 'Still learning') : 'Not trained yet'} />
        {!feature.learning_enabled && <Chip size="small" variant="outlined" label="Learning paused" />}
      </Stack>
      {m?.note && <Typography variant="body2" sx={{ mt: 1 }}>{m.note}</Typography>}
      {ranked && <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>{ranked}</Typography>}
      <Typography variant="body2" sx={{ mt: 1 }}><b>{barWords(feature)}.</b></Typography>
      <Typography variant="caption" color="text.secondary">{feature.threshold_reason}</Typography>
      {feature.learned.length > 0 && (
        <>
          <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>What it has learned matters most</Typography>
          <Box component="ul" sx={{ mt: 0.25, mb: 0, pl: 2.5 }}>
            {feature.learned.slice(0, 4).map((f) => (
              <li key={f.factor}><Typography variant="body2">{f.title} ({f.raises ? 'raises' : 'lowers'} the chance)</Typography></li>
            ))}
          </Box>
        </>
      )}
      <Typography variant="caption" color="text.secondary" component="div" sx={{ mt: 1 }}>
        Last 30 days: {recent.accepted} accepted, {recent.dismissed} dismissed, {recent.came_true} came true,
        {' '}{recent.did_not} did not happen.
      </Typography>
      {canRun && (
        <>
          {!!run.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(run.error)}</Alert>}
          <Button size="small" sx={{ mt: 1 }} disabled={run.isPending} onClick={() => run.mutate()}>
            {run.isPending ? 'Learning…' : 'Learn now'}
          </Button>
        </>
      )}
    </GlassCard>
  )
}

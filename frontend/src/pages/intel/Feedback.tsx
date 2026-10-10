/**
 * Security Intelligence — how the suggestions fared.
 *
 * What the layer suggested, what people decided, and what the matter turned
 * out to be, counted over the situations closed in a period. It is a
 * description for people. Nothing in the platform is trained on it or adjusts
 * itself because of it: a rule or a weight is changed only by a person — and
 * this page says so, in the server's own words.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Grid, MenuItem, Skeleton, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import { useMutation, useQuery } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { apiError, exportFeedbackCsv, getFeedbackAnalytics } from '@/api/securityIntelligence'
import { DECISION_LABEL, pretty, rate } from '@/components/intel/intelFormat'
import { IntelNav, IntelStatusBanner } from './IntelNav'
import { ErrorState } from '@/components/states'

const PERIODS = [30, 90, 180, 365]
const BAR = '#7f93b0'

function Tile({ label, value, hint }: { label: string; value: string | number; hint?: string }) {
  return (
    <GlassCard sx={{ p: 1.5, height: '100%' }} data-testid="feedback-tile">
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>
        {label}</Typography>
      <Typography sx={{ fontSize: '1.5rem', fontWeight: 700 }}>{value}</Typography>
      {hint && <Typography variant="caption" color="text.secondary">{hint}</Typography>}
    </GlassCard>
  )
}

function Tally({ title, rows, empty }: { title: string; rows: Record<string, number>; empty: string }) {
  const list = Object.entries(rows)
  const most = Math.max(1, ...list.map(([, n]) => n))
  return (
    <GlassCard sx={{ p: 2, height: '100%' }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>{title}</Typography>
      {!list.length ? <Typography variant="body2" color="text.secondary">{empty}</Typography> : list.map(([code, n]) => (
        <Box key={code} title={`${pretty(code)}: ${n}`}
             sx={{ display: 'grid', gridTemplateColumns: 'minmax(140px, 40%) 1fr 36px', gap: 1, alignItems: 'center', py: 0.4 }}>
          <Typography variant="body2" noWrap>{pretty(code)}</Typography>
          <Box sx={{ height: 8, borderRadius: '0 4px 4px 0', bgcolor: BAR, width: `${Math.max(2, (n / most) * 100)}%` }} />
          <Typography variant="body2" align="right" sx={{ fontWeight: 700 }}>{n}</Typography>
        </Box>
      ))}
    </GlassCard>
  )
}

export default function Feedback() {
  const [days, setDays] = useState(30)
  const canExport = usePermission('intel:feedback:export')
  const { data, error, refetch: refetchData } = useQuery({ queryKey: ['intel-feedback-analytics', days], queryFn: () => getFeedbackAnalytics(days) })
  const download = useMutation({
    mutationFn: () => exportFeedbackCsv(days),
    onSuccess: (blob) => {
      const url = URL.createObjectURL(blob)
      const link = document.createElement('a')
      link.href = url
      link.download = `security-feedback-last-${days}-days.csv`
      link.click()
      URL.revokeObjectURL(url)
    },
  })
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="How the Suggestions Fared"
                  subtitle="What was suggested, what people decided, and what it turned out to be — over the situations closed in the period" />
      <IntelNav />
      <IntelStatusBanner />
      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField select size="small" label="Period" value={days} sx={{ minWidth: 170 }}
                     onChange={(e) => setDays(Number(e.target.value))}>
            {PERIODS.map((d) => <MenuItem key={d} value={d}>Last {d} days</MenuItem>)}
          </TextField>
          {canExport && (
            <Button size="small" variant="outlined" startIcon={<DownloadIcon />} disabled={download.isPending}
                    onClick={() => download.mutate()}>
              {download.isPending ? 'Preparing…' : 'Export the dataset (CSV)'}</Button>)}
          {canExport && (
            <Typography variant="caption" color="text.secondary">
              One row a closed situation. No names and none of what anyone wrote. Each export is in the audit log.
            </Typography>)}
        </Stack>
        {download.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(download.error)}</Alert>}
      </GlassCard>
      {error ? <ErrorState compact error={error} onRetry={refetchData} /> : !data ? <Skeleton height={300} /> : (
        <>
          <Alert severity="info" sx={{ mb: 2 }} data-testid="feedback-use">{data.use}</Alert>
          <Grid container spacing={1.5} sx={{ mb: 2 }}>
            {[
              { label: 'Situations closed', value: data.situations, hint: `${data.with_a_suggestion} had a suggestion` },
              { label: 'First decision followed it', value: data.followed, hint: `of ${data.decided} decided` },
              { label: 'First decision went against it', value: data.overridden, hint: 'each with its reason' },
              { label: 'Followed, of those two', value: rate(data.acceptance_rate) },
              { label: 'Closed as false', value: data.closed_false, hint: rate(data.false_positive_rate) },
              { label: 'Reviewed afterwards', value: data.reviewed, hint: `of ${data.situations}` },
            ].map((t) => <Grid key={t.label} size={{ xs: 6, md: 4, lg: 2 }}><Tile {...t} /></Grid>)}
          </Grid>
          <GlassCard sx={{ p: 2, mb: 2 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>By the step that was suggested first</Typography>
            {!Object.keys(data.by_suggested_action).length
              ? <Typography variant="body2" color="text.secondary">No situation with a suggestion was closed in the period.</Typography>
              : (
                <Table size="small">
                  <TableHead><TableRow><TableCell>Suggested</TableCell><TableCell align="right">Times</TableCell>
                    <TableCell align="right">Followed</TableCell><TableCell align="right">Went against</TableCell>
                    <TableCell align="right">Closed as false</TableCell></TableRow></TableHead>
                  <TableBody>
                    {Object.entries(data.by_suggested_action).map(([action, n]) => (
                      <TableRow key={action} data-testid="suggested-row">
                        <TableCell>{DECISION_LABEL[action as keyof typeof DECISION_LABEL] ?? pretty(action)}</TableCell>
                        <TableCell align="right">{n.suggested}</TableCell><TableCell align="right">{n.followed}</TableCell>
                        <TableCell align="right">{n.overridden}</TableCell><TableCell align="right">{n.closed_false}</TableCell>
                      </TableRow>))}
                  </TableBody>
                </Table>
              )}
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
              Going against a suggestion is a person using their judgement. The reasons they gave are counted below.
            </Typography>
          </GlassCard>
          <Grid container spacing={2}>
            <Grid size={{ xs: 12, md: 6 }}>
              <Tally title="Why people went against it" rows={data.override_reasons}
                     empty="No first decision went against a suggestion in the period." /></Grid>
            <Grid size={{ xs: 12, md: 6 }}>
              <Tally title="What reviewers said it turned out to be" rows={data.review_outcomes}
                     empty="No closed situation has been reviewed in the period." /></Grid>
            <Grid size={{ xs: 12, md: 6 }}>
              <Tally title="What reviewers said of the assessment" rows={data.review_of_assessment}
                     empty="No reviewer has said." /></Grid>
            <Grid size={{ xs: 12, md: 6 }}>
              <Tally title="What reviewers said of the suggestion" rows={data.review_of_recommendation}
                     empty="No reviewer has said." /></Grid>
          </Grid>
        </>
      )}
    </Box>
  )
}

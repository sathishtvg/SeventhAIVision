/**
 * What a closed situation turned out to be — said afterwards, by a person.
 *
 * A review is a person's statement: what it was, whether the layer's
 * assessment was about right, whether what it suggested was useful. It changes
 * nothing about the situation, and nothing in the platform learns from it by
 * itself. It is shown on a closed situation only, and offered to someone who
 * may approve decisions and has not reviewed this one yet.
 */
import { useState } from 'react'
import { Alert, Box, Button, MenuItem, TextField, Typography } from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { apiError, getSituationFeedback, reviewSituation } from '@/api/securityIntelligence'
import { HUMAN_COLOR, ROLE_LABEL, fmt } from './intelFormat'
import { HumanMark } from './intelUi'

export function SituationReview({ situationId }: { situationId: string }) {
  const qc = useQueryClient()
  const { data } = useQuery({ queryKey: ['intel-feedback', situationId], queryFn: () => getSituationFeedback(situationId) })
  const [outcome, setOutcome] = useState('')
  const [assessment, setAssessment] = useState('')
  const [recommendation, setRecommendation] = useState('')
  const [note, setNote] = useState('')
  const save = useMutation({
    mutationFn: () => reviewSituation(situationId, {
      outcome, assessment_verdict: assessment || undefined, recommendation_verdict: recommendation || undefined,
      note: note.trim() || undefined }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['intel-feedback', situationId] }),
  })
  if (!data || !data.closed) return null                    // what it turned out to be is said once it is closed
  if (!data.reviews.length && !data.may_review) return null
  const label = (list: { code: string; label: string }[], code: string | null) =>
    list.find((x) => x.code === code)?.label ?? code
  const ready = !!outcome && (outcome !== 'UNDETERMINED' || !!note.trim())
  return (
    <GlassCard sx={{ p: 2, mt: 2, borderColor: `${HUMAN_COLOR}55` }} data-testid="review-card">
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>What it turned out to be</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        A review by a person, after the matter was closed. It changes nothing here, and nothing in the platform
        learns from it by itself.
      </Typography>
      {data.reviews.map((r) => (
        <Box key={r.id} data-testid="review" sx={{ py: 0.75, borderBottom: '1px solid rgba(255,255,255,0.08)' }}>
          <HumanMark>{r.name ?? 'A former user'} · {ROLE_LABEL[r.role_id] ?? `Role ${r.role_id}`}</HumanMark>
          <Typography variant="body2" sx={{ fontWeight: 600 }}>{label(data.outcomes, r.outcome)}</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
            {[r.assessment_verdict && `The assessment: ${label(data.assessment_verdicts, r.assessment_verdict)}`,
              r.recommendation_verdict && `The suggestion: ${label(data.recommendation_verdicts, r.recommendation_verdict)}`,
              fmt(r.reviewed_at)].filter(Boolean).join(' · ')}
          </Typography>
          {r.note && <Typography variant="body2" color="text.secondary">{r.note}</Typography>}
        </Box>
      ))}
      {data.may_review && (
        <Box sx={{ mt: 1.5 }}>
          <TextField select fullWidth size="small" label="What it turned out to be" value={outcome} sx={{ mb: 1.5 }}
                     onChange={(e) => setOutcome(e.target.value)}>
            {data.outcomes.map((o) => <MenuItem key={o.code} value={o.code}>{o.label}</MenuItem>)}
          </TextField>
          <TextField select fullWidth size="small" label="The layer's assessment was (optional)" value={assessment}
                     sx={{ mb: 1.5 }} onChange={(e) => setAssessment(e.target.value)}>
            <MenuItem value="">Not said</MenuItem>
            {data.assessment_verdicts.map((o) => <MenuItem key={o.code} value={o.code}>{o.label}</MenuItem>)}
          </TextField>
          <TextField select fullWidth size="small" label="What it suggested was (optional)" value={recommendation}
                     sx={{ mb: 1.5 }} onChange={(e) => setRecommendation(e.target.value)}>
            <MenuItem value="">Not said</MenuItem>
            {data.recommendation_verdicts.map((o) => <MenuItem key={o.code} value={o.code}>{o.label}</MenuItem>)}
          </TextField>
          <TextField fullWidth multiline minRows={2} size="small" value={note} onChange={(e) => setNote(e.target.value)}
                     label={outcome === 'UNDETERMINED' ? 'What is still not known (required)' : 'Note (optional)'}
                     slotProps={{ htmlInput: { maxLength: 2000 } }} />
          {save.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(save.error)}</Alert>}
          <Button variant="contained" size="small" sx={{ mt: 1.5 }} disabled={!ready || save.isPending}
                  onClick={() => save.mutate()}>
            {save.isPending ? 'Recording…' : 'Record my review'}</Button>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
            A review is not edited afterwards. A second view is a second person's.
          </Typography>
        </Box>
      )}
    </GlassCard>
  )
}

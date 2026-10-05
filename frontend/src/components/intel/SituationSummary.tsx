/**
 * The AI-assisted summary of a situation: a paragraph made only of what is
 * recorded, by fixed templates. It is drawn as everything the layer says is
 * drawn — dashed, violet, marked — and it says that it is not the record:
 * the timeline and the records behind each sentence are.
 */
import { Box, Skeleton, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { apiError, getSummary } from '@/api/securityIntelligence'
import { AI_COLOR } from './intelFormat'
import { AiMark } from './intelUi'

export function SituationSummary({ situationId }: { situationId: string }) {
  const { data, error } = useQuery({
    queryKey: ['intel-summary', situationId], queryFn: () => getSummary(situationId), refetchInterval: 20_000 })
  return (
    <GlassCard data-testid="ai-summary" sx={{ p: 2, mb: 2, border: `1px dashed ${AI_COLOR}88` }}>
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1, flexWrap: 'wrap' }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Summary</Typography>
        <AiMark>{data?.label ?? 'AI-assisted summary'}</AiMark>
      </Box>
      {error ? <Typography variant="body2" color="error">{apiError(error)}</Typography>
        : !data ? <Skeleton height={90} />
          : (
            <>
              <Typography variant="body2" sx={{ lineHeight: 1.7 }}>
                {data.sentences.map((s, i) => (
                  <Box component="span" key={i} data-testid="summary-sentence" data-refs={s.refs.length}>{s.text} </Box>))}
              </Typography>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
                {data.made_of} Written by fixed templates, not by a language model. Times are in {data.timezone}.
                It is not the record: the timeline below is.
              </Typography>
              {!data.suggestions_shown && (
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                  What the layer suggested is not shown to your role, and is not summarised.</Typography>)}
            </>
          )}
    </GlassCard>
  )
}

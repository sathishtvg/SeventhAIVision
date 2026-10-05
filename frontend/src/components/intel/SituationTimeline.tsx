/**
 * One situation, in the order it happened.
 *
 * Every line is read from a record that already exists and says whose it is:
 * a SOURCE reported, the layer (AI) assessed or suggested, a PERSON looked,
 * decided, approved or reported, the PLATFORM carried a step out. The four are
 * drawn differently, with the same violet for the layer and green for people
 * as everywhere else on these screens — so a suggestion on the timeline can no
 * more be read as a decision than it can on its own card.
 */
import { Box, Skeleton, Typography } from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { apiError, getTimeline } from '@/api/securityIntelligence'
import type { TimelineActor, TimelineEntry } from '@/api/securityIntelligence'
import { AI_COLOR, HUMAN_COLOR, ROLE_LABEL, SOURCE_LABEL, fmtTime } from './intelFormat'
import { AiMark, HumanMark } from './intelUi'

const SOURCE_COLOR = '#8ea0b8'
const PLATFORM_COLOR = '#c9d3e0'

const ACTOR_WORDS: Record<TimelineActor, string> = {
  SOURCE: 'A source reported', AI: 'The layer assessed or suggested', PERSON: 'A person looked, decided or reported',
  PLATFORM: 'The platform carried out, or recorded',
}

/** The point on the rail: hollow and dashed for the layer, solid for a person, square for the platform. */
function Dot({ actor }: { actor: TimelineActor }) {
  const base = { width: 12, height: 12, flexShrink: 0, mt: '5px' }
  if (actor === 'AI') return <Box sx={{ ...base, borderRadius: '50%', border: `2px dashed ${AI_COLOR}` }} />
  if (actor === 'PERSON') return <Box sx={{ ...base, borderRadius: '50%', bgcolor: HUMAN_COLOR }} />
  if (actor === 'PLATFORM') return <Box sx={{ ...base, borderRadius: '2px', bgcolor: PLATFORM_COLOR }} />
  return <Box sx={{ ...base, borderRadius: '50%', border: `2px solid ${SOURCE_COLOR}` }} />
}

function Whose({ e }: { e: TimelineEntry }) {
  if (e.actor === 'AI') return <AiMark>{e.kind === 'RECOMMENDATION' ? 'AI suggestion — not a decision' : 'AI-assisted'}</AiMark>
  if (e.actor === 'PERSON') {
    const who = e.who?.name ?? 'A former user'
    const role = e.who ? ROLE_LABEL[e.who.role_id] ?? `Role ${e.who.role_id}` : null
    return <HumanMark>{role ? `${who} · ${role}` : who}{e.via === 'mobile' ? ' · from the phone' : ''}</HumanMark>
  }
  const words = e.actor === 'PLATFORM'
    ? (e.kind === 'ACTION' ? 'What the platform then did' : 'The incident’s own record')
    : `${SOURCE_LABEL[e.source_type as keyof typeof SOURCE_LABEL] ?? 'Source'}${e.where ? ` · ${e.where}` : ''}`
  return (
    <Typography component="span" sx={{ fontSize: '0.68rem', fontWeight: 700, letterSpacing: '0.06em',
                                       textTransform: 'uppercase', color: 'text.secondary' }}>{words}</Typography>
  )
}

const day = (iso: string) => new Date(iso).toLocaleDateString(undefined, { dateStyle: 'medium' })

export function SituationTimeline({ situationId }: { situationId: string }) {
  const { data, error } = useQuery({
    queryKey: ['intel-timeline', situationId], queryFn: () => getTimeline(situationId), refetchInterval: 20_000 })
  const entries = data?.entries ?? []
  return (
    <GlassCard sx={{ p: 2, mb: 2 }} data-testid="timeline">
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Timeline</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        In the order it happened. Each line is read from the record it describes — nothing is written for the timeline.
      </Typography>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 2, mb: 1.5 }}>
        {(Object.keys(ACTOR_WORDS) as TimelineActor[]).map((actor) => (
          <Box key={actor} sx={{ display: 'flex', alignItems: 'flex-start', gap: 0.75 }}>
            <Dot actor={actor} />
            <Typography variant="caption" color="text.secondary">
              {ACTOR_WORDS[actor]}{data ? ` (${data.counts[actor]})` : ''}</Typography>
          </Box>
        ))}
      </Box>
      {error ? <Typography variant="body2" color="error">{apiError(error)}</Typography>
        : !data ? <Skeleton height={160} />
          : !entries.length ? <Typography variant="body2" color="text.secondary">Nothing is recorded yet.</Typography>
            : entries.map((e, i) => {
              const newDay = i === 0 || day(e.at) !== day(entries[i - 1].at)
              return (
                <Box key={`${e.kind}-${e.ref.id}-${i}`}>
                  {newDay && (
                    <Typography variant="caption" color="text.secondary"
                                sx={{ display: 'block', mt: i ? 1 : 0, mb: 0.5, fontWeight: 700 }}>{day(e.at)}</Typography>)}
                  <Box data-testid="timeline-entry" data-actor={e.actor} data-kind={e.kind}
                       sx={{ display: 'grid', gridTemplateColumns: '78px 14px 1fr', columnGap: 1.25,
                             alignItems: 'flex-start', pb: 1.25 }}>
                    <Typography variant="caption" color="text.secondary" sx={{ pt: '2px', whiteSpace: 'nowrap' }}>
                      {fmtTime(e.at)}</Typography>
                    <Dot actor={e.actor} />
                    <Box sx={{ minWidth: 0 }}>
                      <Whose e={e} />
                      <Typography variant="body2" data-result={e.result}
                                  sx={{ fontWeight: e.kind === 'DECISION' ? 700 : 400,
                                        color: e.result === 'FAILED' ? 'warning.main' : undefined }}>{e.title}</Typography>
                      {e.detail && <Typography variant="caption" color="text.secondary"
                                               sx={{ display: 'block' }}>{e.detail}</Typography>}
                      {e.kind === 'REPEATS' && e.until && (e.count ?? 0) > 1 && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          The last at {fmtTime(e.until)}.</Typography>)}
                      {e.kind === 'ACTION' && e.through && (
                        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                          through {e.through}</Typography>)}
                    </Box>
                  </Box>
                </Box>
              )
            })}
      {data && !data.suggestions_shown && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          What the layer suggested is not shown to your role.</Typography>)}
    </GlassCard>
  )
}

/**
 * A video's timeline: when people, vehicles and possible altercations were found.
 *
 * One row per kind, the whole video's length across. Each event is a mark
 * where it begins and as long as it lasts; choosing one jumps the player there.
 * Beneath, the events in the order they happen, each with its picture.
 *
 * What is counted is what the detector saw in one frame a second: a count is how
 * many were seen at once, never who.
 */
import { Box, ButtonBase, Chip, Tooltip, Typography } from '@mui/material'
import Stack from '@/components/common/Stack'
import { useAuthStore } from '@/store/auth'
import { frameUrl } from '@/api/footage'
import { clock } from '@/api/footage'
import type { Finding, FindingKind } from '@/api/footage'
import { along, eventText } from './footageFormat'

const ROWS: { kind: FindingKind; label: string; color: string }[] = [
  { kind: 'ALTERCATION', label: 'Possible altercation', color: 'error.main' },
  { kind: 'PEOPLE', label: 'People', color: 'primary.main' },
  { kind: 'VEHICLES', label: 'Vehicles', color: 'warning.main' },
]

interface Props {
  videoId: string
  duration: number
  findings: Finding[]
  onSeek: (second: number) => void
}

export function Timeline({ videoId, duration, findings, onSeek }: Props) {
  const token = useAuthStore((s) => s.accessToken)
  const rows = ROWS.filter((r) => r.kind !== 'ALTERCATION' || findings.some((f) => f.kind === 'ALTERCATION'))
  // Seconds are counted from 0, so a video of 30 seconds ends at second 29: one second's mark is a whole second wide.
  const width = (f: Finding) => Math.max(0.6, along(f.end_second - f.start_second + 1, duration))
  return (
    <Box>
      <Box sx={{ position: 'relative' }} data-testid="timeline">
        {rows.map((row) => (
          <Stack key={row.kind} direction="row" sx={{ alignItems: 'center', gap: 1, mb: 0.75 }}>
            <Typography variant="caption" sx={{ width: 132, flexShrink: 0 }} color="text.secondary">{row.label}</Typography>
            <Box sx={{ position: 'relative', flex: 1, height: 14, borderRadius: 1, bgcolor: 'action.hover' }}>
              {findings.filter((f) => f.kind === row.kind).map((f) => (
                <Tooltip key={f.id} title={eventText(f)}>
                  <ButtonBase aria-label={eventText(f)} onClick={() => onSeek(f.start_second)}
                              sx={{ position: 'absolute', top: 0, bottom: 0, left: `${along(f.start_second, duration)}%`,
                                    width: `${width(f)}%`, bgcolor: row.color, borderRadius: 0.5, opacity: f.verdict === 'REJECTED' ? 0.35 : 0.9 }} />
                </Tooltip>
              ))}
            </Box>
          </Stack>
        ))}
        <Stack direction="row" sx={{ justifyContent: 'space-between', pl: '140px' }}>
          <Typography variant="caption" color="text.secondary">0:00</Typography>
          <Typography variant="caption" color="text.secondary">{clock(duration)}</Typography>
        </Stack>
      </Box>
      {!findings.length ? (
        <Typography variant="body2" color="text.secondary" sx={{ mt: 1.5 }}>
          No people or vehicles were seen in the frames looked at (one a second).</Typography>
      ) : (
        <Box sx={{ mt: 1.5, display: 'grid', gap: 1, gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
          {findings.map((f) => (
            <ButtonBase key={f.id} onClick={() => onSeek(f.start_second)} data-testid="event"
                        sx={{ display: 'flex', gap: 1, p: 0.75, borderRadius: 1, border: 1, borderColor: 'divider',
                              justifyContent: 'flex-start', textAlign: 'left' }}>
              <Box component="img" src={frameUrl(videoId, f.best_second, token)} alt="" loading="lazy"
                   sx={{ width: 88, height: 66, objectFit: 'cover', borderRadius: 0.5, bgcolor: 'action.hover', flexShrink: 0 }} />
              <Box sx={{ minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>{f.kind_words}</Typography>
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                  {eventText(f).slice(f.kind_words.length + 1)}</Typography>
                {f.verdict_words && <Chip size="small" sx={{ mt: 0.5 }} label={f.verdict_words}
                                          color={f.verdict === 'CONFIRMED' ? 'error' : 'default'} />}
              </Box>
            </ButtonBase>
          ))}
        </Box>
      )}
    </Box>
  )
}

/**
 * Searching footage by description: the box, and the moments found.
 *
 * Used on the search page (every ready video the person may see) and on a
 * video's own page (that video only). The words become a vector in our own
 * footage worker; how likely each moment is to show them is the model's own
 * probability, said in words. A search with no strong or possible match says
 * so, and what weak matches there are are shown as weak, for a person to judge.
 */
import { useState } from 'react'
import type { FormEvent } from 'react'
import {
  Alert, Box, Button, Card, CardActionArea, CardContent, Chip, LinearProgress, TextField, Typography,
} from '@mui/material'
import SearchIcon from '@mui/icons-material/Search'
import { useMutation } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { ErrorState } from '@/components/states'
import { useAuthStore } from '@/store/auth'
import { clock, frameUrl, searchFootage } from '@/api/footage'
import type { SearchAnswer, SearchMoment, Strength } from '@/api/footage'
import { instantOf } from './footageFormat'

const STRENGTH_COLOR: Record<Strength, 'success' | 'warning' | 'default'> = { HIGH: 'success', MEDIUM: 'warning', LOW: 'default' }

interface Props {
  /** Search this video only. */
  videoId?: string
  /** Offer the period the footage was filmed in (the search page). */
  withPeriod?: boolean
  /** A moment was chosen: open the video there. */
  onOpen: (moment: SearchMoment) => void
}

export function SearchPanel({ videoId, withPeriod = false, onOpen }: Props) {
  const token = useAuthStore((s) => s.accessToken)
  const [words, setWords] = useState('')
  const [from, setFrom] = useState('')
  const [to, setTo] = useState('')
  const search = useMutation({
    mutationFn: () => searchFootage({ text: words.trim(), video_id: videoId, filmed_from: instantOf(from), filmed_to: instantOf(to) }),
  })
  const submit = (e: FormEvent) => {
    e.preventDefault()
    if (words.trim()) search.mutate()
  }
  const answer: SearchAnswer | undefined = search.data
  return (
    <Box>
      <Box component="form" onSubmit={submit}>
        <Stack direction="row" sx={{ gap: 1.5, flexWrap: 'wrap', alignItems: 'flex-start' }}>
          <TextField size="small" label="Describe what you are looking for" value={words} sx={{ flex: 1, minWidth: 280 }}
                     onChange={(e) => setWords(e.target.value)} slotProps={{ htmlInput: { maxLength: 300 } }}
                     helperText='For example "a man in a red jacket carrying a box" or "a white van at the gate"' />
          {withPeriod && (
            <>
              <TextField size="small" type="datetime-local" label="Filmed from" value={from}
                         onChange={(e) => setFrom(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
              <TextField size="small" type="datetime-local" label="Filmed to" value={to}
                         onChange={(e) => setTo(e.target.value)} slotProps={{ inputLabel: { shrink: true } }} />
            </>
          )}
          <Button type="submit" variant="contained" startIcon={<SearchIcon />} disabled={!words.trim() || search.isPending}>
            Search</Button>
        </Stack>
      </Box>
      {search.isPending && (
        <Box sx={{ mt: 2 }} role="status" aria-live="polite">
          <Typography variant="body2" sx={{ mb: 0.5 }}>Comparing the description with every frame…</Typography>
          <LinearProgress />
        </Box>
      )}
      {search.isError && <Box sx={{ mt: 2 }}><ErrorState compact error={search.error} onRetry={() => search.mutate()} /></Box>}
      {answer && !search.isPending && (
        <Box sx={{ mt: 2 }} data-testid="search-answer">
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
            {answer.videos_searched === 1 ? '1 video' : `${answer.videos_searched} videos`} searched,{' '}
            {answer.frames_searched} frames.
            {answer.not_searched > 0 && ` ${answer.not_searched} analysed with an earlier model ${answer.not_searched === 1 ? 'was' : 'were'} not searched: analyse ${answer.not_searched === 1 ? 'it' : 'them'} again.`}
          </Typography>
          {!answer.moments.length ? (
            <Alert severity="info" sx={{ mb: 2 }}>
              Nothing in the footage looks like “{answer.text}”. Try other words, or describe one thing at a time.
            </Alert>
          ) : !answer.strong ? (
            <Alert severity="info" sx={{ mb: 2 }}>
              No strong match for “{answer.text}”. These are weak matches only: look at them yourself before relying on any.
            </Alert>
          ) : null}
          <Box sx={{ display: 'grid', gap: 1.5, gridTemplateColumns: 'repeat(auto-fill, minmax(220px, 1fr))' }}>
            {answer.moments.map((m) => (
              <Card key={`${m.video_id}-${m.start}`} variant="outlined" data-testid="moment">
                <CardActionArea onClick={() => onOpen(m)}>
                  <Box component="img" src={frameUrl(m.video_id, m.best, token)} alt={`${m.title} at ${clock(m.best)}`}
                       loading="lazy" sx={{ width: '100%', aspectRatio: '4 / 3', objectFit: 'cover', display: 'block', bgcolor: 'action.hover' }} />
                  <CardContent sx={{ p: 1.25, '&:last-child': { pb: 1.25 } }}>
                    <Stack direction="row" sx={{ alignItems: 'center', justifyContent: 'space-between', gap: 1 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>{m.title}</Typography>
                      <Chip size="small" color={STRENGTH_COLOR[m.label]} label={m.label_words} />
                    </Stack>
                    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                      {m.start === m.end ? clock(m.start) : `${clock(m.start)} – ${clock(m.end)}`}
                      {m.at ? ` · ${new Date(m.at).toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'medium' })}` : ''}
                    </Typography>
                    {(m.site_name || m.place) && (
                      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }} noWrap>
                        {[m.site_name, m.place].filter(Boolean).join(' · ')}</Typography>)}
                  </CardContent>
                </CardActionArea>
              </Card>
            ))}
          </Box>
        </Box>
      )}
    </Box>
  )
}

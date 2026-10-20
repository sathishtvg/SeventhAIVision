/**
 * Footage Review — one video: play it, and search inside it.
 *
 * The player plays the original when a browser can, else the copy the worker
 * made; a video neither will play can still be searched by its frames. Opened
 * from a search result, the player starts a few seconds before the moment.
 */
import { useMemo, useRef, useState } from 'react'
import { useNavigate, useParams, useSearchParams } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, LinearProgress, Skeleton, Typography,
} from '@mui/material'
import ReplayIcon from '@mui/icons-material/Replay'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { ErrorState } from '@/components/states'
import { FootageNav } from '@/components/footage/FootageNav'
import { SearchPanel } from '@/components/footage/SearchPanel'
import { Timeline } from '@/components/footage/Timeline'
import { keptText } from '@/components/footage/footageFormat'
import { fmt } from '@/components/evidence/evidenceFormat'
import { useAuthStore } from '@/store/auth'
import { clock, getFindings, getFootage, reanalyse, sizeText, videoUrl } from '@/api/footage'

export default function FootageVideo() {
  const { id = '' } = useParams()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const token = useAuthStore((s) => s.accessToken)
  const player = useRef<HTMLVideoElement>(null)
  const [playFailed, setPlayFailed] = useState(false)
  const [attempt, setAttempt] = useState(0)
  const start = Number(params.get('t') ?? 0) || 0
  const { data: video, isLoading, isLoadingError: videoFailed, error: videoError, refetch } = useQuery({
    queryKey: ['footage-video', id],
    queryFn: () => getFootage(id),
    refetchInterval: (q) => (q.state.data && ['QUEUED', 'PROCESSING'].includes(q.state.data.status) ? 5000 : false),
  })
  const ready = video?.status === 'READY'
  const { data: found, isLoading: findingsLoading, isLoadingError: findingsFailed, error: findingsError, refetch: refetchFindings } = useQuery({
    queryKey: ['footage-findings', id],
    queryFn: () => getFindings(id),
    enabled: ready,
  })
  // The address carries the token as it was when the video was opened, or reopened after a failure.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  const source = useMemo(() => videoUrl(id, token), [id, attempt])

  const seekTo = (second: number) => {
    const el = player.current
    if (!el) return
    el.currentTime = second
    // play() answers a promise in a browser and nothing in some others; a refusal to start is not an error here.
    el.play()?.catch(() => undefined)
  }

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title={video?.title ?? 'Footage Review'}
                  subtitle={video ? [video.site_name, video.place, video.filmed_at ? `filmed ${fmt(video.filmed_at)}` : null].filter(Boolean).join(' · ') || undefined : undefined}
                  action={video?.may.review && (video.status === 'READY' || video.status === 'FAILED') ? (
                    <Button startIcon={<ReplayIcon />} onClick={() => { void reanalyse(id).then(() => qc.invalidateQueries({ queryKey: ['footage-video', id] })) }}>
                      Analyse again</Button>) : undefined} />
      <FootageNav />
      {isLoading ? <Skeleton height={360} /> : videoFailed ? <ErrorState error={videoError} onRetry={refetch} /> : video && (
        <Stack sx={{ gap: 2 }}>
          <GlassCard sx={{ p: 2 }}>
            <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', alignItems: 'center', mb: 1.5 }}>
              <Chip size="small" label={video.status_label} />
              <Chip size="small" variant="outlined" label={video.held ? 'Held' : keptText(video)} />
              <Typography variant="caption" color="text.secondary">
                {video.original_filename} · {sizeText(video.size_bytes)}
                {video.duration_seconds ? ` · ${clock(video.duration_seconds)}` : ''}
                {video.frames_indexed ? ` · ${video.frames_indexed} frames searchable` : ''}
              </Typography>
            </Stack>
            {video.status === 'READY' && (video.playable === 'ORIGINAL' || video.playable === 'PROXY') && !playFailed && (
              <Box component="video" ref={player} controls preload="metadata" src={source} data-testid="player"
                   onLoadedMetadata={() => { if (start && player.current) player.current.currentTime = start }}
                   onError={() => setPlayFailed(true)}
                   sx={{ width: '100%', maxHeight: 520, bgcolor: 'black', borderRadius: 1 }} />
            )}
            {playFailed && (
              <Alert severity="warning" action={<Button onClick={() => { setPlayFailed(false); setAttempt((n) => n + 1) }}>Reload</Button>}>
                The video stopped loading. Reload it to carry on.</Alert>)}
            {video.status === 'READY' && video.playable === 'NONE' && (
              <Alert severity="info">This video cannot be played in a browser. Its frames can still be searched below.</Alert>)}
            {(video.status === 'QUEUED' || video.status === 'PROCESSING') && (
              <Box role="status">
                <Typography variant="body2">{video.status === 'QUEUED' ? 'Waiting to be analysed.' : `Being analysed: ${video.progress}%`}</Typography>
                <LinearProgress variant={video.status === 'PROCESSING' ? 'determinate' : 'indeterminate'} value={video.progress} sx={{ mt: 0.5 }} />
              </Box>
            )}
            {video.status === 'FAILED' && <Alert severity="error">{video.error ?? 'This video could not be analysed.'}</Alert>}
            {video.status === 'UPLOADING' && <Alert severity="info">This video is still being uploaded.</Alert>}
          </GlassCard>
          {ready && (
            <GlassCard sx={{ p: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>What was found</Typography>
              {findingsLoading ? <Skeleton height={96} /> : findingsFailed ? (
                <ErrorState compact error={findingsError} onRetry={refetchFindings} />
              ) : found && (
                <Timeline videoId={id} duration={Number(video.duration_seconds ?? 0)} findings={found.findings}
                          onSeek={(second) => (player.current ? seekTo(second) : navigate(`/footage/${id}?t=${second}`))} />
              )}
            </GlassCard>
          )}
          {video.status === 'READY' && (
            <GlassCard sx={{ p: 2 }}>
              <Typography variant="subtitle2" sx={{ mb: 1 }}>Search in this video</Typography>
              <SearchPanel videoId={id} onOpen={(m) => {
                if (player.current) seekTo(Math.max(0, m.best - 3))
                else navigate(`/footage/${id}?t=${Math.max(0, m.best - 3)}`)
              }} />
            </GlassCard>
          )}
        </Stack>
      )}
    </Box>
  )
}

/**
 * Playback (Gap 85) — pick a camera and a day, see the recorded segments on a
 * 24-hour timeline with alert markers, click a segment to watch it.
 */
import { useMemo, useState } from 'react'
import {
  Box,
  Chip,
  MenuItem,
  Select,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import DownloadIcon from '@mui/icons-material/Download'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import {
  downloadRecordingUrl, getRecordingTimeline, playRecordingUrl,
  type TimelineSegment,
} from '@/api/recordings'
import { useAuthStore } from '@/store/auth'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'

const DAY_MS = 24 * 60 * 60 * 1000

const SEVERITY_COLORS: Record<string, string> = {
  critical: '#FF4560', high: '#FF7F50', medium: '#FFA500',
  low: '#00E396', info: '#6C63FF',
}

function todayStr() {
  return new Date().toISOString().slice(0, 10)
}

/** Percentage offsets of a segment within the selected UTC day. Exported logic
 *  kept pure via useMemo below. */
function segmentSpan(seg: TimelineSegment, dayStartMs: number) {
  const start = new Date(seg.started_at).getTime()
  const end = seg.ended_at ? new Date(seg.ended_at).getTime() : Date.now()
  const left = Math.max(0, ((start - dayStartMs) / DAY_MS) * 100)
  const right = Math.min(100, ((end - dayStartMs) / DAY_MS) * 100)
  return { left, width: Math.max(right - left, 0.15) }
}

export function PlaybackPage() {
  const token = useAuthStore((s) => s.accessToken)
  const [cameraId, setCameraId] = useState('')
  const [date, setDate] = useState(todayStr())
  const [playing, setPlaying] = useState<TimelineSegment | null>(null)

  const { data: cameras = [] } = useQuery({
    queryKey: ['cameras'],
    queryFn: () => apiClient.get('/api/v1/cameras').then((r) => r.data),
  })

  const { data: timeline } = useQuery({
    queryKey: ['recording-timeline', cameraId, date],
    queryFn: () => getRecordingTimeline(cameraId, date),
    enabled: Boolean(cameraId && date),
    refetchInterval: 60_000,
  })

  const dayStartMs = useMemo(() => new Date(`${date}T00:00:00Z`).getTime(), [date])

  const fmtTime = (iso: string) =>
    new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false })

  return (
    <Box>
      <PageHeader title="Playback" subtitle="Recorded footage timeline with alert markers" />

      <Stack direction="row" spacing={2} sx={{ mb: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        <Select
          size="small"
          displayEmpty
          value={cameraId}
          onChange={(e) => { setCameraId(e.target.value); setPlaying(null) }}
          sx={{ minWidth: 220 }}
          renderValue={(v) => {
            const cam = cameras.find((c: any) => c.id === v)
            return cam ? cam.name : 'Select camera…'
          }}
        >
          {cameras.map((c: any) => (
            <MenuItem key={c.id} value={c.id}>
              {c.name}{c.site_name ? ` — ${c.site_name}` : ''}
            </MenuItem>
          ))}
        </Select>
        <TextField
          type="date"
          size="small"
          value={date}
          onChange={(e) => { setDate(e.target.value); setPlaying(null) }}
          inputProps={{ max: todayStr() }}
        />
        {timeline && (
          <Typography variant="caption" color="text.secondary">
            {timeline.segments.length} segment{timeline.segments.length === 1 ? '' : 's'} ·{' '}
            {timeline.alerts.length} alert{timeline.alerts.length === 1 ? '' : 's'}
          </Typography>
        )}
      </Stack>

      {!cameraId ? (
        <GlassCard>
          <Typography color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>
            Select a camera to browse its recorded footage.
          </Typography>
        </GlassCard>
      ) : (
        <>
          {/* 24h timeline bar */}
          <GlassCard sx={{ mb: 2 }}>
            <Box sx={{ px: 2, pt: 2, pb: 1 }}>
              <Box sx={{ position: 'relative', height: 44, borderRadius: 1,
                         background: 'rgba(255,255,255,0.04)',
                         border: '1px solid rgba(255,255,255,0.08)' }}>
                {/* Hour gridlines */}
                {Array.from({ length: 23 }).map((_, i) => (
                  <Box key={i} sx={{
                    position: 'absolute', left: `${((i + 1) / 24) * 100}%`,
                    top: 0, bottom: 0, width: '1px',
                    background: 'rgba(255,255,255,0.05)',
                  }} />
                ))}
                {/* Recording segments */}
                {timeline?.segments.map((seg) => {
                  const { left, width } = segmentSpan(seg, dayStartMs)
                  return (
                    <Tooltip
                      key={seg.id}
                      title={`${fmtTime(seg.started_at)} – ${seg.ended_at ? fmtTime(seg.ended_at) : 'now'}${seg.is_active ? ' (recording)' : ''}`}
                    >
                      <Box
                        onClick={() => !seg.is_active && seg.status === 'completed' && setPlaying(seg)}
                        sx={{
                          position: 'absolute', left: `${left}%`, width: `${width}%`,
                          top: 8, bottom: 8, borderRadius: 0.5,
                          background: seg.is_active ? 'rgba(255,69,96,0.75)'
                            : playing?.id === seg.id ? '#6C63FF' : 'rgba(0,227,150,0.65)',
                          cursor: seg.status === 'completed' ? 'pointer' : 'default',
                          '&:hover': { filter: 'brightness(1.25)' },
                        }}
                      />
                    </Tooltip>
                  )
                })}
                {/* Alert markers */}
                {timeline?.alerts.map((a) => {
                  const left = Math.min(99.7, Math.max(0,
                    ((new Date(a.created_at).getTime() - dayStartMs) / DAY_MS) * 100))
                  return (
                    <Tooltip key={a.id} title={`${fmtTime(a.created_at)} [${a.severity}] ${a.title}`}>
                      <Box sx={{
                        position: 'absolute', left: `${left}%`, top: 0, bottom: 0, width: '2.5px',
                        background: SEVERITY_COLORS[a.severity] ?? '#FF4560',
                        boxShadow: `0 0 5px ${SEVERITY_COLORS[a.severity] ?? '#FF4560'}`,
                        cursor: 'default',
                      }} />
                    </Tooltip>
                  )
                })}
              </Box>
              {/* Hour labels */}
              <Box sx={{ display: 'flex', justifyContent: 'space-between', mt: 0.5 }}>
                {[0, 4, 8, 12, 16, 20, 24].map((h) => (
                  <Typography key={h} variant="caption" color="text.disabled" sx={{ fontSize: '0.6rem' }}>
                    {String(h).padStart(2, '0')}:00
                  </Typography>
                ))}
              </Box>
            </Box>
          </GlassCard>

          {/* Player */}
          {playing && (
            <GlassCard sx={{ mb: 2 }}>
              <Box sx={{ p: 1.5 }}>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1 }}>
                  <PlayArrowIcon fontSize="small" />
                  <Typography variant="body2" fontWeight={700} sx={{ flex: 1 }}>
                    {timeline?.camera_name} — {fmtTime(playing.started_at)}
                    {playing.ended_at ? ` to ${fmtTime(playing.ended_at)}` : ''}
                  </Typography>
                  <Chip
                    icon={<DownloadIcon sx={{ fontSize: 14 }} />}
                    label="Download"
                    size="small"
                    component="a"
                    href={downloadRecordingUrl(playing.id)}
                    clickable
                  />
                </Stack>
                {token && (
                  <Box
                    component="video"
                    key={playing.id}
                    controls
                    autoPlay
                    src={playRecordingUrl(playing.id, token)}
                    sx={{ width: '100%', maxHeight: '60vh', borderRadius: 1, background: '#000' }}
                  />
                )}
              </Box>
            </GlassCard>
          )}

          {/* Segment list */}
          <GlassCard>
            <Box sx={{ p: 1.5 }}>
              {timeline?.segments.length === 0 ? (
                <Typography color="text.secondary" variant="body2" sx={{ p: 1 }}>
                  No recordings on {date}. Enable continuous recording on this camera's
                  stream (Cameras page) for 24/7 footage.
                </Typography>
              ) : (
                <Stack spacing={0.5}>
                  {timeline?.segments.map((seg) => (
                    <Stack key={seg.id} direction="row" spacing={1.5} alignItems="center"
                           sx={{ px: 1, py: 0.5, borderRadius: 1,
                                 background: playing?.id === seg.id ? 'rgba(108,99,255,0.12)' : 'transparent',
                                 '&:hover': { background: 'rgba(255,255,255,0.04)' } }}>
                      <Typography variant="body2" sx={{ minWidth: 120, fontFamily: 'monospace' }}>
                        {fmtTime(seg.started_at)} – {seg.ended_at ? fmtTime(seg.ended_at) : '…'}
                      </Typography>
                      <Chip label={seg.is_active ? 'recording' : seg.status} size="small"
                            color={seg.is_active ? 'error' : seg.status === 'completed' ? 'success' : 'default'}
                            variant="outlined" sx={{ height: 18, fontSize: '0.62rem' }} />
                      <Typography variant="caption" color="text.secondary" sx={{ flex: 1 }}>
                        {seg.file_size_bytes ? `${(seg.file_size_bytes / 1024 / 1024).toFixed(1)} MB` : ''}
                      </Typography>
                      {seg.status === 'completed' && (
                        <Chip icon={<PlayArrowIcon sx={{ fontSize: 14 }} />} label="Play"
                              size="small" clickable onClick={() => setPlaying(seg)} />
                      )}
                    </Stack>
                  ))}
                </Stack>
              )}
            </Box>
          </GlassCard>
        </>
      )}
    </Box>
  )
}

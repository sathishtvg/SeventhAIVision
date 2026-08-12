import { useState } from 'react'
import {
  Box, Chip, Dialog, DialogContent, DialogTitle, IconButton, Table, TableBody,
  TableCell, TableHead, TableRow, Tooltip, Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import DownloadIcon from '@mui/icons-material/Download'
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord'
import PlayArrowIcon from '@mui/icons-material/PlayArrow'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { downloadRecordingUrl, playRecordingUrl } from '@/api/recordings'
import { getSites } from '@/api/sites'
import { useAuthStore } from '@/store/auth'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { VideoPlayer } from '@/components/common/VideoPlayer'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import type { Recording } from '@/types/api'

function formatBytes(bytes: number | null) {
  if (!bytes) return '—'
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatDuration(s: number | null) {
  if (s == null) return '—'
  const m = Math.floor(s / 60)
  const sec = s % 60
  return `${m}:${String(sec).padStart(2, '0')}`
}

export function RecordingsPage() {
  const [siteFilter, setSiteFilter] = useState('')
  const [playing, setPlaying] = useState<any | null>(null)
  const token = useAuthStore((s) => s.accessToken)
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  // Fetch cameras to get recordings across all streams
  const { data: cameras = [] } = useQuery({
    queryKey: ['cameras'],
    queryFn: () => apiClient.get('/api/v1/cameras').then((r) => r.data),
  })

  // For each camera, fetch their streams and recordings (simplified: fetch all in one query)
  const { data: allRecordings = [], isLoading } = useQuery({
    queryKey: ['all-recordings', cameras],
    queryFn: async () => {
      const recs: (Recording & { camera_name: string; site_id: string | null })[] = []
      for (const cam of cameras) {
        const streams = await apiClient.get(`/api/v1/cameras/${cam.id}/streams`).then((r) => r.data)
        for (const stream of streams) {
          const streamRecs = await apiClient
            .get(`/api/v1/cameras/${cam.id}/streams/${stream.id}/recordings`)
            .then((r) => r.data)
          for (const rec of streamRecs) {
            recs.push({ ...rec, camera_name: cam.name, site_id: cam.site_id })
          }
        }
      }
      return recs.sort((a: any, b: any) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime())
    },
    enabled: cameras.length > 0,
  })

  const filtered = siteFilter
    ? allRecordings.filter((r: any) => r.site_id === siteFilter)
    : allRecordings

  const filterGroups: FilterGroup[] = [{
    key: 'site',
    label: 'Site',
    value: siteFilter,
    onChange: setSiteFilter,
    options: [
      { value: '', label: 'All Sites' },
      ...(sites as any[]).map((s) => ({ value: s.id, label: s.name })),
    ],
  }]

  return (
    <Box sx={{ p: 3, display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      <PageHeader title="Recordings" subtitle="Video recordings from all camera streams" />

      <Box sx={{ display: 'flex', gap: 1, mb: 2, alignItems: 'center' }}>
        {allRecordings.filter((r: any) => r.status === 'recording').length > 0 && (
          <Chip
            icon={<FiberManualRecordIcon sx={{ color: 'red !important', fontSize: '0.75rem !important' }} />}
            label={`${allRecordings.filter((r: any) => r.status === 'recording').length} recording`}
            color="error"
            size="small"
          />
        )}
      </Box>

      <GlassCard>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Camera</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Started</TableCell>
              <TableCell>Duration</TableCell>
              <TableCell>Size</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading ? (
              <TableRow><TableCell colSpan={6} align="center">Loading…</TableCell></TableRow>
            ) : filtered.length === 0 ? (
              <TableRow><TableCell colSpan={6} align="center" sx={{ color: 'text.secondary' }}>No recordings</TableCell></TableRow>
            ) : filtered.map((rec: any) => (
              <TableRow key={rec.id} hover>
                <TableCell>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{rec.camera_name}</Typography>
                </TableCell>
                <TableCell>
                  {rec.status === 'recording' ? (
                    <Chip
                      icon={<FiberManualRecordIcon sx={{ color: 'red !important', fontSize: '0.7rem !important' }} />}
                      label="Recording"
                      size="small"
                      color="error"
                    />
                  ) : (
                    <Chip label={rec.status} size="small" color={rec.status === 'completed' ? 'success' : 'default'} />
                  )}
                </TableCell>
                <TableCell>
                  <Typography variant="caption">{new Date(rec.started_at).toLocaleString()}</Typography>
                </TableCell>
                <TableCell>{formatDuration(rec.duration_seconds)}</TableCell>
                <TableCell>{formatBytes(rec.file_size_bytes)}</TableCell>
                <TableCell align="right">
                  {rec.status === 'completed' && (
                    <>
                      <Tooltip title="Play">
                        <IconButton size="small" onClick={() => setPlaying(rec)}>
                          <PlayArrowIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Download">
                        <IconButton
                          size="small"
                          component="a"
                          href={downloadRecordingUrl(rec.id)}
                          download
                        >
                          <DownloadIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </>
                  )}
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </GlassCard>
      </Box>

      <FilterRail groups={filterGroups} storageKey="recordings" />

      {/* Built-in playback. Download stays available alongside it — an
          investigator often wants the file itself, not just a preview. */}
      <Dialog
        open={Boolean(playing)}
        onClose={() => setPlaying(null)}
        maxWidth="md"
        fullWidth
      >
        <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pr: 1 }}>
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }} noWrap>
              {playing?.camera_name}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              {playing && new Date(playing.started_at).toLocaleString()}
              {playing?.duration_seconds != null && ` · ${formatDuration(playing.duration_seconds)}`}
            </Typography>
          </Box>
          <Tooltip title="Download">
            <IconButton
              size="small"
              component="a"
              href={playing ? downloadRecordingUrl(playing.id) : undefined}
              download
            >
              <DownloadIcon fontSize="small" />
            </IconButton>
          </Tooltip>
          <Tooltip title="Close">
            <IconButton size="small" onClick={() => setPlaying(null)} aria-label="Close player">
              <CloseIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </DialogTitle>
        <DialogContent sx={{ pt: 0 }}>
          {playing && token && (
            <VideoPlayer
              src={playRecordingUrl(playing.id, token)}
              playerKey={playing.id}
              autoPlay
              maxHeight="70vh"
              label={`Recording from ${playing.camera_name}`}
            />
          )}
        </DialogContent>
      </Dialog>
    </Box>
  )
}

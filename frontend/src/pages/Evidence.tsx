import { useState } from 'react'
import {
  Box, Grid, Typography, Skeleton, Dialog, DialogContent, IconButton, Chip,
  Tooltip, Divider, Button,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import BrokenImageIcon from '@mui/icons-material/BrokenImage'
import PlayCircleIcon from '@mui/icons-material/PlayCircle'
import VideocamIcon from '@mui/icons-material/Videocam'
import PlaceIcon from '@mui/icons-material/Place'
import AccessTimeIcon from '@mui/icons-material/AccessTime'
import DownloadIcon from '@mui/icons-material/Download'
import ReportProblemIcon from '@mui/icons-material/ReportProblem'
import { useQuery } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { VideoPlayer } from '@/components/common/VideoPlayer'
import { getEvidence, evidenceImageUrl } from '@/api/evidence'
import { useAuthStore } from '@/store/auth'
import type { Evidence as EvidenceItem } from '@/types/api'

/** Analytic labels — same wording the rest of the app uses for module_type. */
const MODULE_LABEL: Record<string, string> = {
  lpr: 'LPR', face: 'Face', intrusion: 'Intrusion', ppe: 'PPE',
  crowd: 'Crowd', fire_smoke: 'Fire/Smoke', weapon: 'Weapon',
  behavior: 'Behavior', tampering: 'Tamper', abandoned: 'Abandoned', fall: 'Fall',
}

const MODULE_COLOR: Record<string, string> = {
  lpr: '#6C63FF', face: '#00D9C0', intrusion: '#FF4560', ppe: '#FF9800',
  crowd: '#2196F3', fire_smoke: '#FF4560', weapon: '#FF4560',
  behavior: '#9C27B0', tampering: '#FF9800', abandoned: '#FFC107', fall: '#FF4560',
}

/** A capture is either the whole frame or a crop of the thing that fired
 *  (plate, face). Worth stating outright — a tight crop out of context is
 *  otherwise easy to mistake for a badly aimed camera. */
const KIND_LABEL: Record<string, string> = {
  frame: 'Full frame', plate: 'Plate crop', face: 'Face crop',
}

const moduleLabel = (m?: string | null) =>
  m ? MODULE_LABEL[m] ?? m.replace(/_/g, ' ') : null

function ModuleChip({ module }: { module: string }) {
  const color = MODULE_COLOR[module] ?? '#6C63FF'
  return (
    <Chip
      label={moduleLabel(module)}
      size="small"
      sx={{
        height: 20, fontSize: '0.65rem', fontWeight: 700,
        bgcolor: `${color}22`, color, border: `1px solid ${color}55`,
      }}
    />
  )
}

interface EvidenceCardProps {
  item: EvidenceItem
  onClick: () => void
}

function EvidenceCard({ item, onClick }: EvidenceCardProps) {
  const [imgError, setImgError] = useState(false)
  const token = useAuthStore((s) => s.accessToken)
  const src = evidenceImageUrl(item.id, token, 640)  // card-sized, not full frame
  // Video evidence has no still to downscale, so the thumbnail endpoint would
  // hand a whole MP4 to an <img> — always a broken image. Show it as a clip
  // instead, and don't pull the file down just to render a grid tile.
  const isVideo = item.media_type === 'video'

  return (
    <GlassCard
      sx={{ cursor: 'pointer', overflow: 'hidden', '&:hover': { transform: 'scale(1.02)', transition: 'transform 0.2s' } }}
      onClick={onClick}
    >
      <Box sx={{ position: 'relative', aspectRatio: '16/9', background: 'rgba(0,0,0,0.3)' }}>
        {isVideo ? (
          <Box
            sx={{
              display: 'flex', flexDirection: 'column', gap: 0.5,
              alignItems: 'center', justifyContent: 'center', height: '100%',
              color: 'text.secondary',
            }}
          >
            <PlayCircleIcon sx={{ fontSize: 48, color: 'primary.main' }} />
            <Typography variant="caption">Video clip — click to play</Typography>
          </Box>
        ) : imgError || !src ? (
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <BrokenImageIcon sx={{ fontSize: 48, color: 'text.disabled' }} />
          </Box>
        ) : (
          <img
            src={src}
            alt={`${moduleLabel(item.module_type) ?? 'Evidence'} capture from ${item.camera_name ?? 'unknown camera'}`}
            onError={() => setImgError(true)}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        )}
        {/* Analytic badge on the image itself, so a wall of thumbnails is
            scannable by what fired without opening each one. */}
        {item.module_type && (
          <Box sx={{ position: 'absolute', top: 6, left: 6 }}>
            <ModuleChip module={item.module_type} />
          </Box>
        )}
      </Box>
      <Box sx={{ p: 1.5 }}>
        <Typography variant="body2" noWrap title={item.camera_name ?? undefined} sx={{ fontWeight: 600 }}>
          {item.camera_name ?? 'Unknown camera'}
        </Typography>
        <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
          {item.site_name ?? 'Unassigned site'}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.25 }}>
          {new Date(item.captured_at).toLocaleString()}
        </Typography>
      </Box>
    </GlassCard>
  )
}

/** One label/value line in the viewer's detail panel. */
function Detail({ label, value, mono }: { label: string; value: React.ReactNode; mono?: boolean }) {
  return (
    <Box sx={{ display: 'flex', gap: 1, alignItems: 'baseline', py: 0.4 }}>
      <Typography
        variant="caption"
        sx={{
          minWidth: 92, flexShrink: 0, color: 'text.secondary',
          textTransform: 'uppercase', letterSpacing: '0.06em', fontSize: '0.62rem', fontWeight: 700,
        }}
      >
        {label}
      </Typography>
      <Typography
        variant="body2"
        sx={{ wordBreak: 'break-all', ...(mono && { fontFamily: 'monospace', fontSize: '0.75rem' }) }}
      >
        {value}
      </Typography>
    </Box>
  )
}

export default function Evidence() {
  const [selected, setSelected] = useState<EvidenceItem | null>(null)
  const token = useAuthStore((s) => s.accessToken)
  const { data: items, isLoading } = useQuery({
    queryKey: ['evidence'],
    queryFn: () => getEvidence(100),
  })

  const fullSrc = selected ? evidenceImageUrl(selected.id, token) : null

  return (
    <Box>
      <Grid container spacing={2}>
        {isLoading
          ? Array.from({ length: 12 }).map((_, i) => (
              <Grid size={{ xs: 12, sm: 6, md: 4, lg: 3 }} key={i}>
                <GlassCard>
                  <Skeleton variant="rectangular" height={135} />
                  <Box sx={{ p: 1.5 }}><Skeleton width="60%" /></Box>
                </GlassCard>
              </Grid>
            ))
          : items?.length === 0
          ? (
              <Grid size={{ xs: 12 }}>
                <Typography color="text.secondary" align="center" sx={{ mt: 8 }}>
                  No evidence files stored
                </Typography>
              </Grid>
            )
          : items?.map((item) => (
              <Grid size={{ xs: 12, sm: 6, md: 4, lg: 3 }} key={item.id}>
                <EvidenceCard item={item} onClick={() => setSelected(item)} />
              </Grid>
            ))}
      </Grid>

      {/* Viewer. Was the image alone, which left an operator looking at a
          photo with no way to tell where or when it came from or what fired.
          Image on the left, provenance on the right. */}
      <Dialog open={!!selected} onClose={() => setSelected(null)} maxWidth="lg" fullWidth>
        <DialogContent sx={{ p: 0, position: 'relative', background: '#000' }}>
          <IconButton
            aria-label="Close"
            sx={{ position: 'absolute', top: 8, right: 8, zIndex: 2, background: 'rgba(0,0,0,0.6)', color: '#fff' }}
            onClick={() => setSelected(null)}
          >
            <CloseIcon />
          </IconButton>

          {selected && (
            <Box sx={{ display: 'flex', flexDirection: { xs: 'column', md: 'row' }, alignItems: 'stretch' }}>
              <Box sx={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                {selected.media_type === 'video' ? (
                  // Video evidence plays in place, with the same provenance
                  // panel beside it — previously this rendered an <img>, so a
                  // clip showed as a broken image with no way to view it.
                  <VideoPlayer
                    src={fullSrc ?? ''}
                    playerKey={selected.id}
                    autoPlay
                    maxHeight="80vh"
                    sx={{ width: '100%' }}
                    label={`Evidence clip from ${selected.camera_name ?? 'unknown camera'}`}
                  />
                ) : (
                  <img
                    src={fullSrc ?? undefined}
                    alt={`${moduleLabel(selected.module_type) ?? 'Evidence'} capture from ${selected.camera_name ?? 'unknown camera'}`}
                    style={{ width: '100%', maxHeight: '80vh', objectFit: 'contain', display: 'block' }}
                  />
                )}
              </Box>

              <Box
                sx={{
                  width: { xs: '100%', md: 320 }, flexShrink: 0,
                  p: 2, bgcolor: 'rgba(13,17,28,0.98)',
                  borderLeft: { md: '1px solid rgba(255,255,255,0.10)' },
                  borderTop: { xs: '1px solid rgba(255,255,255,0.10)', md: 'none' },
                  overflowY: 'auto', maxHeight: { md: '80vh' },
                }}
              >
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap', mb: 1.5, pr: 4 }}>
                  {selected.module_type
                    ? <ModuleChip module={selected.module_type} />
                    : <Chip label="No analytic" size="small" variant="outlined" sx={{ height: 20, fontSize: '0.65rem' }} />}
                  {selected.capture_kind && (
                    <Chip
                      label={KIND_LABEL[selected.capture_kind] ?? selected.capture_kind}
                      size="small" variant="outlined"
                      sx={{ height: 20, fontSize: '0.65rem' }}
                    />
                  )}
                </Box>

                <Detail
                  label="Site"
                  value={
                    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                      <PlaceIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                      {selected.site_name ?? '—'}
                    </Box>
                  }
                />
                <Detail
                  label="Camera"
                  value={
                    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                      <VideocamIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                      {selected.camera_name ?? '—'}
                    </Box>
                  }
                />
                {selected.camera_location && <Detail label="Location" value={selected.camera_location} />}
                <Detail
                  label="Captured"
                  value={
                    <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                      <AccessTimeIcon sx={{ fontSize: 14, color: 'text.secondary' }} />
                      {new Date(selected.captured_at).toLocaleString()}
                    </Box>
                  }
                />
                {selected.confidence != null && (
                  <Detail label="Confidence" value={`${(Number(selected.confidence) * 100).toFixed(1)}%`} />
                )}
                {selected.incident_title && (
                  <Detail
                    label="Incident"
                    value={
                      <Box component="span" sx={{ display: 'inline-flex', alignItems: 'center', gap: 0.5 }}>
                        <ReportProblemIcon sx={{ fontSize: 14, color: '#FF9800' }} />
                        {selected.incident_title}
                      </Box>
                    }
                  />
                )}

                <Divider sx={{ my: 1.5, borderColor: 'rgba(255,255,255,0.08)' }} />

                {/* Chain-of-custody fields — what makes this admissible as
                    evidence rather than just a screenshot. */}
                <Detail label="Evidence ID" value={selected.id} mono />
                {selected.detection_id && <Detail label="Detection" value={selected.detection_id} mono />}
                {selected.checksum_sha256 && (
                  <Tooltip title="SHA-256 of the stored file — proves it has not been altered since capture">
                    <Box><Detail label="SHA-256" value={selected.checksum_sha256} mono /></Box>
                  </Tooltip>
                )}

                {fullSrc && (
                  <Button
                    fullWidth
                    size="small"
                    variant="outlined"
                    startIcon={<DownloadIcon />}
                    href={fullSrc}
                    download={`evidence-${selected.id}.jpg`}
                    sx={{ mt: 2 }}
                  >
                    Download
                  </Button>
                )}
              </Box>
            </Box>
          )}
        </DialogContent>
      </Dialog>
    </Box>
  )
}

import { useState } from 'react'
import {
  Box, Grid, Typography, Skeleton, Dialog, DialogContent, IconButton, Chip,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import BrokenImageIcon from '@mui/icons-material/BrokenImage'
import { useQuery } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { getEvidence, evidenceFileUrl } from '@/api/evidence'
import type { Evidence as EvidenceItem } from '@/types/api'

interface EvidenceCardProps {
  item: EvidenceItem
  onClick: () => void
}

function EvidenceCard({ item, onClick }: EvidenceCardProps) {
  const [imgError, setImgError] = useState(false)

  return (
    <GlassCard
      sx={{ cursor: 'pointer', overflow: 'hidden', '&:hover': { transform: 'scale(1.02)', transition: 'transform 0.2s' } }}
      onClick={onClick}
    >
      <Box sx={{ position: 'relative', aspectRatio: '16/9', background: 'rgba(0,0,0,0.3)' }}>
        {imgError ? (
          <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%' }}>
            <BrokenImageIcon sx={{ fontSize: 48, color: 'text.disabled' }} />
          </Box>
        ) : (
          <img
            src={evidenceFileUrl(item.id)}
            alt={`Evidence ${item.id}`}
            onError={() => setImgError(true)}
            style={{ width: '100%', height: '100%', objectFit: 'cover' }}
          />
        )}
      </Box>
      <Box sx={{ p: 1.5 }}>
        <Typography variant="caption" sx={{ fontFamily: 'monospace', display: 'block' }}>
          {item.id.slice(0, 12)}…
        </Typography>
        <Box sx={{ display: 'flex', gap: 0.5, mt: 0.5, flexWrap: 'wrap', alignItems: 'center' }}>
          <Chip label={item.media_type} size="small" variant="outlined" />
          <Typography variant="caption" color="text.secondary">
            {new Date(item.captured_at).toLocaleString()}
          </Typography>
        </Box>
      </Box>
    </GlassCard>
  )
}

export default function Evidence() {
  const [selected, setSelected] = useState<EvidenceItem | null>(null)
  const { data: items, isLoading } = useQuery({
    queryKey: ['evidence'],
    queryFn: () => getEvidence(100),
  })

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

      <Dialog open={!!selected} onClose={() => setSelected(null)} maxWidth="md" fullWidth>
        <DialogContent sx={{ p: 0, position: 'relative', background: '#000' }}>
          <IconButton
            sx={{ position: 'absolute', top: 8, right: 8, zIndex: 1, background: 'rgba(0,0,0,0.6)' }}
            onClick={() => setSelected(null)}
          >
            <CloseIcon />
          </IconButton>
          {selected && (
            <img
              src={evidenceFileUrl(selected.id)}
              alt={`Evidence ${selected.id}`}
              style={{ width: '100%', maxHeight: '80vh', objectFit: 'contain' }}
            />
          )}
        </DialogContent>
      </Dialog>
    </Box>
  )
}

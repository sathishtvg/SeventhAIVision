/**
 * Footage Review — search every ready video by description.
 *
 * The description becomes a vector in the platform's own footage worker (no
 * footage leaves the platform); the moments that look most like it are shown
 * by their best frame, with how strongly they stand out said in words. Each
 * search, with its wording, is kept in the audit log.
 */
import { Box, Typography } from '@mui/material'
import { useNavigate } from 'react-router-dom'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { FootageNav } from '@/components/footage/FootageNav'
import { SearchPanel } from '@/components/footage/SearchPanel'

export default function FootageSearch() {
  const navigate = useNavigate()
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Footage Review"
                  subtitle="Search the uploaded videos by describing what you are looking for" />
      <FootageNav />
      <GlassCard sx={{ p: 2 }}>
        <SearchPanel withPeriod onOpen={(m) => navigate(`/footage/${m.video_id}?t=${Math.max(0, m.best - 5)}`)} />
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 2 }}>
          The search compares your words with one frame a second of every ready video you may see. It ranks what it
          finds; it does not decide anything, and finding a likeness is not finding a person or a fact. Every search
          is recorded with its wording.
        </Typography>
      </GlassCard>
    </Box>
  )
}

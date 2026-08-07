import { useState } from 'react'
import { Box, Dialog, DialogContent, IconButton, Tooltip, Typography } from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import ImageNotSupportedIcon from '@mui/icons-material/ImageNotSupported'
import { evidenceImageUrl } from '@/api/evidence'
import { useAuthStore } from '@/store/auth'

interface EvidenceThumbProps {
  /** Full-frame capture — "which vehicle, which lane, when". */
  frameEvidenceId?: string | null
  /** Cropped plate/face capture — "is this what the camera actually read". */
  plateEvidenceId?: string | null
}

const THUMB = 40

/**
 * Screenshot proof for one detection, as a row-sized thumbnail with a lightbox.
 *
 * Every AI module already writes an evidence snapshot at detection time; until
 * now no list endpoint returned a pointer to it, so the operator reviewing a
 * detection had a row of text and no picture. This is that picture.
 *
 * When a plate crop exists it leads, because it is the one that answers the
 * question actually being asked at review time — the full frame renders the
 * plate as a smudge. Both remain openable.
 */
export function EvidenceThumb({ frameEvidenceId, plateEvidenceId }: EvidenceThumbProps) {
  const token = useAuthStore((s) => s.accessToken)
  const [open, setOpen] = useState<string | null>(null)
  const [failed, setFailed] = useState<Record<string, boolean>>({})

  // Crop first — it is the more informative image at thumbnail size.
  const ids = [
    plateEvidenceId ? { id: plateEvidenceId, label: 'Plate crop' } : null,
    frameEvidenceId ? { id: frameEvidenceId, label: 'Full frame' } : null,
  ].filter((x): x is { id: string; label: string } => x !== null)

  if (ids.length === 0 || !token) {
    return (
      <Tooltip title="No screenshot captured for this detection">
        <ImageNotSupportedIcon sx={{ fontSize: 18, color: 'text.disabled' }} />
      </Tooltip>
    )
  }

  return (
    <>
      <Box sx={{ display: 'flex', gap: 0.5, alignItems: 'center' }}>
        {ids.map(({ id, label }) =>
          failed[id] ? null : (
            <Tooltip key={id} title={`${label} — click to enlarge`}>
              <Box
                component="img"
                src={evidenceImageUrl(id, token, THUMB * 2) ?? undefined}
                alt={label}
                loading="lazy"
                onClick={() => setOpen(id)}
                onError={() => setFailed((f) => ({ ...f, [id]: true }))}
                sx={{
                  width: THUMB,
                  height: THUMB,
                  objectFit: 'cover',
                  borderRadius: 1,
                  cursor: 'pointer',
                  border: '1px solid rgba(255,255,255,0.15)',
                  transition: 'border-color 0.15s',
                  '&:hover': { borderColor: 'primary.main' },
                }}
              />
            </Tooltip>
          ),
        )}
        {/* Every thumbnail 404'd/errored — say so rather than render an empty cell. */}
        {ids.every(({ id }) => failed[id]) && (
          <Tooltip title="Screenshot file missing from storage">
            <ImageNotSupportedIcon sx={{ fontSize: 18, color: 'text.disabled' }} />
          </Tooltip>
        )}
      </Box>

      <Dialog open={!!open} onClose={() => setOpen(null)} maxWidth="md" fullWidth>
        <DialogContent sx={{ p: 0, position: 'relative', background: '#000' }}>
          <IconButton
            sx={{ position: 'absolute', top: 8, right: 8, zIndex: 1, background: 'rgba(0,0,0,0.6)' }}
            onClick={() => setOpen(null)}
          >
            <CloseIcon />
          </IconButton>
          {open && (
            <>
              <Box
                component="img"
                src={evidenceImageUrl(open, token) ?? undefined}
                alt="Detection evidence"
                sx={{ width: '100%', maxHeight: '80vh', objectFit: 'contain', display: 'block' }}
              />
              <Typography
                variant="caption"
                sx={{ position: 'absolute', bottom: 8, left: 12, color: 'rgba(255,255,255,0.7)' }}
              >
                {ids.find((i) => i.id === open)?.label}
              </Typography>
            </>
          )}
        </DialogContent>
      </Dialog>
    </>
  )
}

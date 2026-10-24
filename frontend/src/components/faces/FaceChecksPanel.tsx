/**
 * Check-in face checks, for a supervisor to review.
 *
 * The owner chose "allow but flag": a check-in whose selfie does not match the face on file still goes
 * through, and is listed here. Each row opens the check-in selfie beside the photo on file, so the
 * supervisor judges for themselves. Design: docs/plans/2026-10-23-staff-face-registration-design.md.
 */
import { useState } from 'react'
import {
  Avatar, Box, Chip, Dialog, DialogActions, DialogContent, DialogTitle, Button, Skeleton, ToggleButton,
  ToggleButtonGroup, Typography,
} from '@mui/material'
import { useQuery } from '@tanstack/react-query'
import { facePhotoUrl, getFaceChecks, OUTCOME_COLOR, OUTCOME_LABEL, type FaceCheck } from '@/api/faces'
import { checkinPhotoUrl } from '@/api/attendance'
import { GlassCard } from '@/components/common/GlassCard'
import Stack from '@/components/common/Stack'
import { ErrorState } from '@/components/states'
import { useTenantTimeZone } from '@/lib/tenantTime'

/** In the organisation's own zone, passed in so the panel draws again when it arrives: the app-wide default is set
 *  only once the organisation's settings have loaded, and a list drawn before that kept the browser's zone. */
const when = (iso: string, timeZone?: string) =>
  new Date(iso).toLocaleString([], { dateStyle: 'medium', timeStyle: 'short', timeZone })

function CompareDialog({ check, token, timeZone, onClose }: {
  check: FaceCheck; token: string | null; timeZone?: string; onClose: () => void
}) {
  const selfie = checkinPhotoUrl(check.shift_id, check.which, token)
  const onFile = check.face_on_file && check.guard_user_id ? facePhotoUrl(check.guard_user_id, token) : null
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{check.guard_name ?? check.guard_email ?? 'Guard'}: {check.which === 'check_in' ? 'check-in' : 'check-out'}</DialogTitle>
      <DialogContent dividers>
        <Stack direction="row" spacing={2} sx={{ justifyContent: 'center' }}>
          {[{ label: 'Selfie at this check', src: selfie }, { label: 'Face on file', src: onFile }].map((p) => (
            <Box key={p.label} sx={{ textAlign: 'center' }}>
              <Avatar variant="rounded" src={p.src ?? undefined} sx={{ width: 200, height: 200 }}>-</Avatar>
              <Typography variant="caption" color="text.secondary">{p.src ? p.label : `${p.label}: none`}</Typography>
            </Box>
          ))}
        </Stack>
        <Typography variant="body2" sx={{ mt: 2 }}>
          <Chip size="small" color={OUTCOME_COLOR[check.outcome]} label={OUTCOME_LABEL[check.outcome]} sx={{ mr: 1 }} />
          {check.similarity != null && `Similarity ${check.similarity.toFixed(2)} (needed ${check.min_similarity?.toFixed(2) ?? '-'}). `}
          {check.reason}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
          {check.site_name ?? 'No site'} · {when(check.created_at, timeZone)}
        </Typography>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

export function FaceChecksPanel({ token }: { token: string | null }) {
  const [show, setShow] = useState<'review' | 'all'>('review')
  const [open, setOpen] = useState<FaceCheck | null>(null)
  const timeZone = useTenantTimeZone()
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['face-checks', show],
    queryFn: () => getFaceChecks({ outcome: show === 'review' ? 'review' : undefined, days: 7 }),
  })

  return (
    <GlassCard sx={{ p: 2, mt: 2 }}>
      <Stack direction="row" alignItems="center" sx={{ mb: 1 }}>
        <Typography variant="subtitle2" sx={{ fontWeight: 800, flex: 1 }}>
          Check-in face checks, last 7 days
        </Typography>
        <ToggleButtonGroup size="small" exclusive value={show} onChange={(_, v) => v && setShow(v)}>
          <ToggleButton value="review">Needs review</ToggleButton>
          <ToggleButton value="all">All</ToggleButton>
        </ToggleButtonGroup>
      </Stack>
      {isLoading ? (
        <Skeleton height={64} />
      ) : isError ? (
        <ErrorState compact error={error} onRetry={refetch} />
      ) : !data?.length ? (
        <Typography variant="body2" color="text.secondary">
          {show === 'review' ? 'Nothing to review: every check-in face matched.' : 'No check-in with a selfie in the last 7 days.'}
        </Typography>
      ) : (
        <Stack spacing={0.75}>
          {data.map((c) => (
            <Stack key={`${c.shift_id}-${c.which}`} direction="row" spacing={1.5} alignItems="center"
                   onClick={() => setOpen(c)} role="button"
                   sx={{ px: 1, py: 0.75, borderRadius: 1, cursor: 'pointer', background: 'rgba(255,255,255,0.03)' }}>
              <Chip size="small" color={OUTCOME_COLOR[c.outcome]} label={OUTCOME_LABEL[c.outcome]} />
              <Box sx={{ flex: 1, minWidth: 0 }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }} noWrap>
                  {c.guard_name ?? c.guard_email ?? 'Guard'} · {c.which === 'check_in' ? 'check-in' : 'check-out'}
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {c.site_name ?? 'No site'} · {when(c.created_at, timeZone)}
                  {c.similarity != null ? ` · similarity ${c.similarity.toFixed(2)}` : ''}
                </Typography>
              </Box>
            </Stack>
          ))}
        </Stack>
      )}
      {open && <CompareDialog check={open} token={token} timeZone={timeZone} onClose={() => setOpen(null)} />}
    </GlassCard>
  )
}

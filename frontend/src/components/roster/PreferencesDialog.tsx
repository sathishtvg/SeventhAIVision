import { useEffect, useState } from 'react'
import {
  Alert, Avatar, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  ToggleButton, ToggleButtonGroup, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import LightModeIcon from '@mui/icons-material/LightMode'
import DarkModeIcon from '@mui/icons-material/DarkMode'
import { useMutation, useQueryClient } from '@tanstack/react-query'

import { setPreferences, type GridEmployee } from '@/api/roster'
import { profilePhotoUrl } from '@/api/attendance'
import { useAuthStore } from '@/store/auth'

/** '' is the wire value for "no preference" — the API stores null, and the
 *  scheduler reads that as "favour neither", which is what Both means. */
type Choice = 'day' | 'night' | ''

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((w) => w[0]).join('').toUpperCase() || '?'
}

/**
 * Set everyone's preferred shift in one place.
 *
 * Seeded from the grid, which already carries each guard's preference, so
 * opening this costs nothing — the alternative was one request per guard.
 *
 * On save it writes only the rows that actually changed. Editing one person's
 * preference should not rewrite forty records and stamp updated_at across a
 * team that did not change.
 */
export function PreferencesDialog({ open, employees, onClose }: {
  open: boolean
  employees: GridEmployee[]
  onClose: () => void
}) {
  const qc = useQueryClient()
  const token = useAuthStore((s) => s.accessToken)
  const [choices, setChoices] = useState<Record<string, Choice>>({})
  const [original, setOriginal] = useState<Record<string, Choice>>({})

  useEffect(() => {
    if (!open) return
    const seed: Record<string, Choice> = {}
    for (const e of employees) seed[e.guard_user_id] = (e.preferred_shift_type ?? '') as Choice
    setChoices(seed)
    setOriginal(seed)
  }, [open, employees])

  const changed = Object.keys(choices).filter((id) => choices[id] !== original[id])

  const { mutate: save, isPending, error } = useMutation({
    mutationFn: async () => {
      for (const id of changed) {
        await setPreferences(id, { preferred_shift_type: choices[id] || null })
      }
    },
    onSuccess: () => {
      // The grid paints a preference dot per row and the scheduler scores
      // against these, so both have to be refetched.
      qc.invalidateQueries({ queryKey: ['roster-grid'] })
      qc.invalidateQueries({ queryKey: ['guard-preferences'] })
      onClose()
    },
  })

  const message = (error as { response?: { data?: { detail?: string } } } | null)
    ?.response?.data?.detail

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        Shift preferences
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          Set each employee's preferred shift type
        </Typography>
      </DialogTitle>

      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1.5 }}>
        {message && <Alert severity="warning">{message}</Alert>}

        {/* Said plainly, because the difference matters: a planner who reads
            this as a restriction will not understand why somebody got a night. */}
        <Alert severity="info" sx={{ py: 0.5 }}>
          Preferences are <strong>soft</strong>. The scheduler favours them, but will roster
          against one rather than leave a post unmanned or break a rest rule.
        </Alert>

        {employees.length === 0 ? (
          <Typography variant="body2" color="text.secondary">No schedulable staff.</Typography>
        ) : (
          <Stack spacing={0.75}>
            {employees.map((e) => (
              <Stack
                key={e.guard_user_id} direction="row" alignItems="center" spacing={1.25}
                sx={{
                  px: 1.25, py: 0.9, borderRadius: '8px',
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}
              >
                <Avatar
                  src={e.profile_photo_path ? (profilePhotoUrl(e.guard_user_id, token) ?? undefined) : undefined}
                  sx={{ width: 30, height: 30, fontSize: '0.68rem', fontWeight: 700 }}
                >
                  {initials(e.full_name)}
                </Avatar>
                <Box sx={{ flex: 1, minWidth: 0 }}>
                  <Typography variant="body2" noWrap sx={{ fontWeight: 600, fontSize: '0.8rem' }}>
                    {e.full_name}
                  </Typography>
                  <Typography variant="caption" noWrap sx={{ display: 'block', color: 'text.disabled', fontSize: '0.63rem' }}>
                    {e.designation || 'Security Officer'}
                  </Typography>
                </Box>
                <ToggleButtonGroup
                  size="small" exclusive
                  value={choices[e.guard_user_id] ?? ''}
                  onChange={(_, v: Choice | null) =>
                    // A null here is the group deselecting the active button.
                    // Treated as Both rather than ignored, so clicking the
                    // current choice clears it instead of doing nothing.
                    setChoices((c) => ({ ...c, [e.guard_user_id]: v ?? '' }))
                  }
                >
                  <ToggleButton value="day" sx={{ px: 1, py: 0.4, fontSize: '0.66rem' }}>
                    <LightModeIcon sx={{ fontSize: 13, mr: 0.4 }} /> Day
                  </ToggleButton>
                  <ToggleButton value="night" sx={{ px: 1, py: 0.4, fontSize: '0.66rem' }}>
                    <DarkModeIcon sx={{ fontSize: 13, mr: 0.4 }} /> Night
                  </ToggleButton>
                  <ToggleButton value="" sx={{ px: 1, py: 0.4, fontSize: '0.66rem' }}>
                    Both
                  </ToggleButton>
                </ToggleButtonGroup>
              </Stack>
            ))}
          </Stack>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button
          variant="contained" onClick={() => save()}
          disabled={isPending || changed.length === 0}
        >
          {isPending
            ? 'Saving…'
            : changed.length
              ? `Save ${changed.length} change${changed.length === 1 ? '' : 's'}`
              : 'Save preferences'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default PreferencesDialog

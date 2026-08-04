import { useState } from 'react'
import {
  Box, Button, Checkbox, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControlLabel, IconButton, Tab, Tabs, TextField, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import RemoveIcon from '@mui/icons-material/Remove'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { WallScreenEditor, type GridSize, type WallScreenCell } from './WallScreenEditor'
import { createWallProfile, type WallProfile, type WallProfileScreenInput } from '@/api/wallProfiles'

const MIN_SCREENS = 1
/** Shared with LiveWall's "add another screen" control so the cap is stated
 * once. Mirrors _MAX_SCREENS in backend/app/routers/wall_profiles.py. */
export const MAX_PROFILE_SCREENS = 8
const MAX_SCREENS = MAX_PROFILE_SCREENS

interface WallProfileSetupDialogProps {
  open: boolean
  onClose: () => void
  onCreated: (profile: WallProfile) => void
}

function defaultScreen(): WallProfileScreenInput {
  return { grid_size: 4, cells: [], analytics_modules: [] }
}

/** "How many screens do you need?" wizard — asks the screen count, then
 * walks through configuring cameras/grid/analytics for each one (via
 * WallScreenEditor), and saves the whole set as one named, launchable
 * wall_profiles row. */
export function WallProfileSetupDialog({ open, onClose, onCreated }: WallProfileSetupDialogProps) {
  const [step, setStep] = useState<'count' | 'configure'>('count')
  const [profileName, setProfileName] = useState('')
  const [isShared, setIsShared] = useState(false)
  const [screenCount, setScreenCount] = useState(2)
  const [activeScreen, setActiveScreen] = useState(0)
  const [screens, setScreens] = useState<WallProfileScreenInput[]>([])
  const qc = useQueryClient()

  const reset = () => {
    setStep('count')
    setProfileName('')
    setIsShared(false)
    setScreenCount(2)
    setActiveScreen(0)
    setScreens([])
  }

  const goToConfigure = () => {
    setScreens((prev) => Array.from({ length: screenCount }, (_, i) => prev[i] ?? defaultScreen()))
    setActiveScreen(0)
    setStep('configure')
  }

  const updateScreen = (idx: number, patch: Partial<WallProfileScreenInput>) => {
    setScreens((prev) => prev.map((s, i) => (i === idx ? { ...s, ...patch } : s)))
  }

  const { mutate: save, isPending: saving } = useMutation({
    mutationFn: () => createWallProfile({ name: profileName.trim(), is_shared: isShared, screens }),
    onSuccess: (profile) => {
      qc.invalidateQueries({ queryKey: ['wall-profiles'] })
      onCreated(profile)
      reset()
    },
  })

  const handleClose = () => {
    reset()
    onClose()
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="md" fullWidth>
      <DialogTitle>Multi-Screen Setup</DialogTitle>
      <DialogContent>
        {step === 'count' ? (
          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2, py: 1 }}>
            <Typography variant="body2" color="text.secondary">
              How many screens does this control room need? Configure each screen's cameras and
              analytics once, save it as a set, and reopen every screen with the same setup —
              on this machine or any other.
            </Typography>
            <TextField
              label="Profile name"
              size="small"
              value={profileName}
              onChange={(e) => setProfileName(e.target.value)}
              autoFocus
              fullWidth
            />
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
              <Typography variant="body2">Number of screens:</Typography>
              <IconButton
                size="small"
                onClick={() => setScreenCount((c) => Math.max(MIN_SCREENS, c - 1))}
                disabled={screenCount <= MIN_SCREENS}
              >
                <RemoveIcon fontSize="small" />
              </IconButton>
              <Typography variant="h6" sx={{ minWidth: 28, textAlign: 'center' }}>{screenCount}</Typography>
              <IconButton
                size="small"
                onClick={() => setScreenCount((c) => Math.min(MAX_SCREENS, c + 1))}
                disabled={screenCount >= MAX_SCREENS}
              >
                <AddIcon fontSize="small" />
              </IconButton>
              <Typography variant="caption" color="text.secondary">(max {MAX_SCREENS} — add more later if needed)</Typography>
            </Box>
            <FormControlLabel
              control={<Checkbox size="small" checked={isShared} onChange={(e) => setIsShared(e.target.checked)} />}
              label={<Typography variant="body2">Share with everyone in this organisation</Typography>}
            />
          </Box>
        ) : (
          <Box>
            <Tabs
              value={activeScreen}
              onChange={(_, v) => setActiveScreen(v)}
              variant="scrollable"
              scrollButtons="auto"
              sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
            >
              {screens.map((_, idx) => (
                <Tab key={idx} label={`Screen ${idx + 1}`} />
              ))}
            </Tabs>
            {screens[activeScreen] && (
              <WallScreenEditor
                gridSize={screens[activeScreen].grid_size as GridSize}
                cells={screens[activeScreen].cells as WallScreenCell[]}
                analyticsModules={screens[activeScreen].analytics_modules}
                onChangeGridSize={(g) => updateScreen(activeScreen, { grid_size: g })}
                onChangeCells={(cells) => updateScreen(activeScreen, { cells })}
                onChangeAnalytics={(m) => updateScreen(activeScreen, { analytics_modules: m })}
              />
            )}
          </Box>
        )}
      </DialogContent>
      <DialogActions>
        {step === 'count' ? (
          <>
            <Button onClick={handleClose}>Cancel</Button>
            <Button variant="contained" disabled={!profileName.trim()} onClick={goToConfigure}>
              Next
            </Button>
          </>
        ) : (
          <>
            <Button onClick={() => setStep('count')} disabled={saving}>Back</Button>
            <Button variant="contained" disabled={saving} onClick={() => save()}>
              {saving ? 'Saving…' : 'Save Profile'}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  )
}

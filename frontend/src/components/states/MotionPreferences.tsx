import { FormControl, FormControlLabel, FormLabel, Radio, RadioGroup, Switch, Typography } from '@mui/material'
import { useColorMode } from '@/context/ColorMode'
import { usePageScenes, useReducedMotion } from '@/motion'
import type { MotionPreference } from '@/motion'

const CHOICES: { value: MotionPreference; label: string; means: string }[] = [
  { value: 'system', label: 'Follow this device', means: 'Uses the reduced-motion setting of the computer or phone.' },
  { value: 'reduced', label: 'Reduced', means: 'Nothing slides or counts up. Changes appear at once.' },
  { value: 'full', label: 'Full', means: 'The app’s ordinary motion, whatever the device says.' },
]

/**
 * How much the app moves, for the person signed in. Kept with their theme
 * choice: on this device, for their account, and for nobody else's.
 *
 * Whichever is chosen, nothing is told by movement alone - an alert is still
 * coloured and named, a new row is still marked - so "Reduced" loses no
 * information.
 */
export function MotionPreferences() {
  const { motion, setMotion } = useColorMode()
  const [scenes, setScenes] = usePageScenes()
  const reduced = useReducedMotion()
  return (
    <FormControl sx={{ mb: 3, display: 'block' }}>
      <FormLabel id="motion-preference" sx={{ fontSize: '0.75rem', mb: 0.5 }}>Motion</FormLabel>
      <RadioGroup
        aria-labelledby="motion-preference"
        value={motion}
        onChange={(_, value) => setMotion(value as MotionPreference)}
      >
        {CHOICES.map((choice) => (
          <FormControlLabel
            key={choice.value}
            value={choice.value}
            control={<Radio size="small" />}
            sx={{ alignItems: 'flex-start', '& .MuiRadio-root': { pt: 0.5 } }}
            label={(
              <>
                <Typography variant="body2">{choice.label}</Typography>
                <Typography variant="caption" color="text.secondary">{choice.means}</Typography>
              </>
            )}
          />
        ))}
      </RadioGroup>
      {/* Each page's own entrance. Apart from the choice above: somebody who moves between pages all shift
          may want them simply to appear, and keep everything else as it is. */}
      <FormControlLabel
        control={<Switch size="small" checked={scenes && !reduced} disabled={reduced} onChange={(_, on) => setScenes(on)} />}
        sx={{ alignItems: 'flex-start', mt: 1, '& .MuiSwitch-root': { mt: 0.25 } }}
        label={(
          <>
            <Typography variant="body2">Page animations</Typography>
            <Typography variant="caption" color="text.secondary">
              {reduced
                ? 'Not played while motion is reduced.'
                : 'Each page draws its own emblem as you open it, and keeps it moving while the page loads. A click or a key skips it.'}
            </Typography>
          </>
        )}
      />
    </FormControl>
  )
}

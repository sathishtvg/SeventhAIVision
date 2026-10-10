import { FormControl, FormControlLabel, FormLabel, Radio, RadioGroup, Typography } from '@mui/material'
import { useColorMode } from '@/context/ColorMode'
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
    </FormControl>
  )
}

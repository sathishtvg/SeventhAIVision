/**
 * Whether a visitor's badge used outside its authorisation is handed to
 * Security Intelligence as an event to look at.
 *
 * Off unless the organisation switches it on. What is handed over names
 * nobody: the door, the time, and that it was outside what the visit was
 * authorised for. The list of door events to look at is the same either way.
 *
 * Read by whoever may read the organisation's settings; switched by whoever
 * may change them.
 */
import { Alert, FormControlLabel, Switch, Typography } from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { usePermission } from '@/hooks/usePermission'
import { getSettings, upsertSetting } from '@/api/settings'

export const MOVEMENTS_SETTING = 'visitor.movements_to_intelligence'

export function MovementsToIntelligence() {
  const qc = useQueryClient()
  const mayRead = usePermission('settings:read')
  const maySwitch = usePermission('settings:write')
  const { data } = useQuery({ queryKey: ['settings'], queryFn: getSettings, enabled: mayRead })
  const flip = useMutation({
    mutationFn: (on: boolean) => upsertSetting(MOVEMENTS_SETTING, on),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })
  if (!mayRead || !data) return null
  // A setting's value is typed as a number, which most are. This one is yes or no.
  const on = data.some((s) => s.setting_key === MOVEMENTS_SETTING && (s.setting_value as unknown) === true)
  return (
    <GlassCard sx={{ p: 2, mb: 2 }} data-testid="movements-to-intelligence">
      <FormControlLabel
        label={on ? 'Door events outside an authorisation are handed to Security Intelligence'
          : 'Door events outside an authorisation are not handed to Security Intelligence'}
        control={<Switch size="small" checked={on} disabled={!maySwitch || flip.isPending}
                         onChange={(_, v) => flip.mutate(v)} />} />
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        On, each one still to be looked at becomes an event the intelligence layer can place beside what a camera
        saw there. The event names nobody — the door, the time, and that it was outside what the visit was
        authorised for. It is something to look at, not a finding, and the list above is the same either way.
      </Typography>
      {!!flip.error && <Alert severity="error" sx={{ mt: 1 }}>Could not be changed. Try again.</Alert>}
    </GlassCard>
  )
}

import { useState } from 'react'
import {
  Alert, Box, Button, Dialog, DialogActions, DialogContent, DialogTitle,
  MenuItem, Select, Switch, Tab, Tabs, TextField, ToggleButton,
  ToggleButtonGroup, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import { useMutation, useQuery } from '@tanstack/react-query'

import {
  autoSchedule,
  type AutoScheduleRules, type RosterBatch, type ShiftPatternMode,
} from '@/api/roster'
import { getSites } from '@/api/sites'

const PATTERN_MODES: { value: ShiftPatternMode; label: string }[] = [
  { value: 'rotation', label: 'Day/Night Rotation' },
  { value: 'day_only', label: 'Day Only' },
  { value: 'night_only', label: 'Night Only' },
]

/**
 * One rule row: a switch, a description, and an optional number.
 *
 * The number is disabled rather than hidden when the rule is off, so the
 * value a planner set is still visible — switching a rule off and on again
 * should not silently lose what it was set to.
 */
function RuleRow({ label, hint, enabled, onToggle, value, onValue, unit, min, max }: {
  label: string
  hint: string
  enabled: boolean
  onToggle: (v: boolean) => void
  value?: number
  onValue?: (v: number) => void
  unit?: string
  min?: number
  max?: number
}) {
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.5}
      sx={{
        px: 1.5, py: 1, borderRadius: '8px',
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
      }}
    >
      <Switch size="small" checked={enabled} onChange={(e) => onToggle(e.target.checked)} />
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.82rem' }}>{label}</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.68rem' }}>
          {hint}
        </Typography>
      </Box>
      {onValue && (
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <TextField
            type="number" size="small" value={value ?? ''} disabled={!enabled}
            onChange={(e) => onValue(Number(e.target.value))}
            sx={{ width: 78 }}
            slotProps={{ htmlInput: { min, max, style: { textAlign: 'center' } } }}
          />
          <Typography variant="caption" color="text.disabled" sx={{ minWidth: 38, fontSize: '0.66rem' }}>
            {unit}
          </Typography>
        </Stack>
      )}
    </Stack>
  )
}

const HOW_IT_WORKS = [
  ['Set a shift preference on the guard', 'Each guard’s profile records whether they prefer day or night. The scheduler treats it as a strong preference, not a restriction.'],
  ['Configure the rules here', 'Hard rules — rest, consecutive days, the night cap — are never broken. Soft rules are balanced against each other.'],
  ['The scheduler fills every post', 'Day by day, site by site, it fills the strength each site is staffed for, drawing from that site’s duty team first and the wider pool only if it must.'],
  ['Review the draft', 'Nothing is live yet. Unfilled posts, coverage shortfalls and off-team fills are all flagged for you to fix by hand.'],
  ['Publish', 'The draft becomes the roster and the guards are notified.'],
]

export function AutoScheduleDialog({ open, onClose, onGenerated }: {
  open: boolean
  onClose: () => void
  onGenerated: (batch: RosterBatch) => void
}) {
  const [tab, setTab] = useState(0)
  const [siteId, setSiteId] = useState('')
  const [periodStart, setPeriodStart] = useState(() => new Date().toISOString().slice(0, 10))
  const [periodEnd, setPeriodEnd] = useState(() => {
    const d = new Date(); d.setDate(d.getDate() + 29); return d.toISOString().slice(0, 10)
  })

  const [mode, setMode] = useState<ShiftPatternMode>('rotation')
  const [fairRotation, setFairRotation] = useState(true)
  const [restOn, setRestOn] = useState(true)
  const [restHours, setRestHours] = useState(11)
  const [consecOn, setConsecOn] = useState(true)
  const [consecDays, setConsecDays] = useState(6)
  const [nightCapOn, setNightCapOn] = useState(false)
  const [nightCap, setNightCap] = useState(15)
  const [headcountOn, setHeadcountOn] = useState(false)
  const [headcount, setHeadcount] = useState(1)
  const [preferences, setPreferences] = useState(true)
  const [respectLeave, setRespectLeave] = useState(true)
  const [offDaysOn, setOffDaysOn] = useState(false)
  const [offDays, setOffDays] = useState(4)
  const [overwrite, setOverwrite] = useState(false)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(), enabled: open })

  // Null, not omitted, when a numeric rule is switched off: the API reads null
  // as "this rule is off" and an absent key as "use the default", which are
  // different instructions.
  const rules = (): AutoScheduleRules => ({
    shift_pattern: mode,
    fair_rotation: fairRotation,
    min_rest_hours: restOn ? restHours : null,
    max_consecutive_days: consecOn ? consecDays : null,
    max_night_shifts_per_period: nightCapOn ? nightCap : null,
    min_headcount: headcountOn ? headcount : null,
    honour_preferences: preferences,
    respect_leave: respectLeave,
    max_off_days_per_period: offDaysOn ? offDays : null,
    overwrite_existing: overwrite,
  })

  const { mutate: run, isPending, error } = useMutation({
    mutationFn: () => autoSchedule({
      site_id: siteId || undefined,
      period_start: periodStart,
      period_end: periodEnd,
      rules: rules(),
    }),
    onSuccess: (batch) => { onGenerated(batch); onClose() },
  })

  const message = (error as { response?: { data?: { detail?: unknown } } } | null)
    ?.response?.data?.detail

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ pb: 0.5 }}>
        <Stack direction="row" alignItems="center" spacing={1}>
          <AutoAwesomeIcon sx={{ fontSize: 19, color: 'primary.main' }} />
          <Box>
            <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>AI Auto-Schedule</Typography>
            <Typography variant="caption" color="text.secondary">
              Configure the rules — the scheduler builds the roster
            </Typography>
          </Box>
        </Stack>
      </DialogTitle>

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ px: 3, borderBottom: 1, borderColor: 'divider' }}>
        <Tab label="Rules" />
        <Tab label="Period & Site" />
        <Tab label="How it works" />
      </Tabs>

      <DialogContent sx={{ pt: 2 }}>
        {message != null && (
          <Alert severity="warning" sx={{ mb: 2 }}>
            {typeof message === 'string' ? message : 'Those rules were rejected — check the values.'}
          </Alert>
        )}

        {tab === 0 && (
          <Stack spacing={1.25}>
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.6 }}>
                SHIFT PATTERN
              </Typography>
              <ToggleButtonGroup
                size="small" exclusive value={mode} fullWidth
                onChange={(_, v) => v && setMode(v)}
              >
                {PATTERN_MODES.map((m) => (
                  <ToggleButton key={m.value} value={m.value} sx={{ fontSize: '0.7rem', py: 0.6 }}>
                    {m.label}
                  </ToggleButton>
                ))}
              </ToggleButtonGroup>
            </Box>

            <RuleRow
              label="Fair rotation" hint="Spread day and night shifts evenly across staff"
              enabled={fairRotation} onToggle={setFairRotation}
            />
            <RuleRow
              label="Minimum rest between shifts" hint="A guard cannot start again inside this gap"
              enabled={restOn} onToggle={setRestOn}
              value={restHours} onValue={setRestHours} unit="hrs" min={0} max={48}
            />
            <RuleRow
              label="Max consecutive working days" hint="Forces a day off after this many in a row"
              enabled={consecOn} onToggle={setConsecOn}
              value={consecDays} onValue={setConsecDays} unit="days" min={1} max={31}
            />
            <RuleRow
              label="Max night shifts per period" hint="Caps the nights any one guard is given"
              enabled={nightCapOn} onToggle={setNightCapOn}
              value={nightCap} onValue={setNightCap} unit="shifts" min={0} max={62}
            />
            <RuleRow
              label="Minimum headcount per shift"
              hint="Overrides each site's own day/night strength with one number"
              enabled={headcountOn} onToggle={setHeadcountOn}
              value={headcount} onValue={setHeadcount} unit="staff" min={0} max={20}
            />
            <RuleRow
              label="Honour shift preferences" hint="Favour the day or night each guard prefers"
              enabled={preferences} onToggle={setPreferences}
            />
            <RuleRow
              label="Respect approved leave" hint="Never roster anyone across approved leave"
              enabled={respectLeave} onToggle={setRespectLeave}
            />
            <RuleRow
              label="Max off days per period" hint="Prefers whoever has been idle longest"
              enabled={offDaysOn} onToggle={setOffDaysOn}
              value={offDays} onValue={setOffDays} unit="days" min={0} max={62}
            />
            <RuleRow
              label="Overwrite existing assignments"
              hint="On publish, replaces scheduled shifts in this period. Started shifts are kept."
              enabled={overwrite} onToggle={setOverwrite}
            />

            {!respectLeave && (
              <Alert severity="warning" sx={{ py: 0.5 }}>
                With leave ignored, guards on approved leave can be rostered.
              </Alert>
            )}
          </Stack>
        )}

        {tab === 1 && (
          <Stack spacing={2}>
            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.6 }}>
                SCHEDULE PERIOD
              </Typography>
              <Stack direction="row" spacing={1.5}>
                <TextField
                  type="date" size="small" value={periodStart} sx={{ flex: 1 }}
                  onChange={(e) => setPeriodStart(e.target.value)}
                  slotProps={{ inputLabel: { shrink: true } }}
                />
                <TextField
                  type="date" size="small" value={periodEnd} sx={{ flex: 1 }}
                  onChange={(e) => setPeriodEnd(e.target.value)}
                  slotProps={{ inputLabel: { shrink: true } }}
                />
              </Stack>
            </Box>

            <Box>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.6 }}>
                APPLY TO SITE
              </Typography>
              <Select
                size="small" fullWidth displayEmpty value={siteId}
                onChange={(e) => setSiteId(e.target.value)}
                renderValue={(v) => sites.find((s: { id: string }) => s.id === v)?.name ?? 'Every site'}
              >
                <MenuItem value="">Every site</MenuItem>
                {sites.map((s: { id: string; name: string }) => (
                  <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
                ))}
              </Select>
            </Box>

            <Alert severity="info" sx={{ py: 0.75 }}>
              The scheduler fills the strength each site is staffed for, from that site's duty
              team first. Hard rules — rest, consecutive days, the night cap, approved leave —
              are never broken; a post it cannot fill without breaking one is left open and
              flagged rather than filled badly.
            </Alert>
          </Stack>
        )}

        {tab === 2 && (
          <Stack spacing={1.25}>
            {HOW_IT_WORKS.map(([title, body], i) => (
              <Stack
                key={title} direction="row" spacing={1.5}
                sx={{
                  px: 1.5, py: 1.25, borderRadius: '8px',
                  background: 'rgba(255,255,255,0.03)',
                  border: '1px solid rgba(255,255,255,0.06)',
                }}
              >
                <Box sx={{
                  width: 22, height: 22, borderRadius: '50%', flexShrink: 0,
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: 'rgba(108,99,255,0.18)', color: 'primary.main',
                  fontSize: '0.7rem', fontWeight: 800,
                }}>
                  {i + 1}
                </Box>
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600, fontSize: '0.82rem' }}>{title}</Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.7rem' }}>
                    {body}
                  </Typography>
                </Box>
              </Stack>
            ))}
          </Stack>
        )}
      </DialogContent>

      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button
          variant="contained" startIcon={<AutoAwesomeIcon sx={{ fontSize: 16 }} />}
          onClick={() => run()} disabled={isPending || !periodStart || !periodEnd}
        >
          {isPending ? 'Generating…' : 'Generate Schedule'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default AutoScheduleDialog

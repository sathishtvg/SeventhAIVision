/**
 * The four kinds of record an organisation may give a retention period.
 *
 * Each is kept until a period is set. Setting one removes nothing by itself:
 * the scheduler removes what is over and older than the period, once a day,
 * for good. So before a period is set the server says how many are already
 * older than it, and the dialog shows that and asks again.
 *
 * The buttons are there only for somebody who may change the organisation's
 * settings — the server says who (`may_set`).
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, TextField, Typography,
} from '@mui/material'
import { useMutation } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { alreadyOlder, apiError, setRetentionPeriod } from '@/api/dataGovernance'
import type { OptionalKind, Statement } from '@/api/dataGovernance'
import { day, optionalLine } from '@/components/governance/governanceFormat'

function PeriodForm({ kind, least, onClose, onSet }: {
  kind: OptionalKind; least: number; onClose: () => void; onSet: () => Promise<unknown>
}) {
  const [days, setDays] = useState(kind.days == null ? '' : String(kind.days))
  // What the server said is already older than the period being set, once it has said so.
  const [told, setTold] = useState<{ message: string; already_older: number; days: number } | null>(null)
  const wanted = Number(days)
  const valid = /^\d+$/.test(days.trim()) && wanted >= least
  const save = useMutation({
    mutationFn: (confirmed: number | undefined) => setRetentionPeriod(kind.key, wanted, confirmed).then(onSet),
    onSuccess: onClose,
    onError: (err) => setTold(alreadyOlder(err)),
  })
  const refused = save.error && !alreadyOlder(save.error) ? apiError(save.error) : null
  return (
    <>
      <DialogTitle>A period for: {kind.label.toLowerCase()}</DialogTitle>
      <DialogContent>
        <Typography variant="body2" sx={{ mb: 0.5 }}>Counted from {kind.counted_from}.</Typography>
        <Typography variant="body2" sx={{ mb: 0.5 }}>What is removed: {kind.removes}.</Typography>
        {kind.kept_whatever && (
          <Typography variant="body2" sx={{ mb: 0.5 }}>Kept whatever its age: {kind.kept_whatever}.</Typography>)}
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          {kind.removed_by}. What is removed is removed for good.
        </Typography>
        <TextField autoFocus fullWidth size="small" label="Days" value={days} disabled={save.isPending}
                   onChange={(e) => { setDays(e.target.value); setTold(null); save.reset() }}
                   helperText={`A whole number of days, ${least} or more`} slotProps={{ htmlInput: { inputMode: 'numeric' } }} />
        {told && <Alert severity="warning" sx={{ mt: 2 }} data-testid="already-older">{told.message}</Alert>}
        {refused && <Alert severity="error" sx={{ mt: 2 }}>{refused}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={save.isPending}>Cancel</Button>
        {told && told.days === wanted
          ? <Button color="warning" variant="contained" disabled={save.isPending} onClick={() => save.mutate(told.already_older)}>
              Set it, and remove them
            </Button>
          : <Button variant="contained" disabled={!valid || save.isPending} onClick={() => save.mutate(undefined)}>Set the period</Button>}
      </DialogActions>
    </>
  )
}

function TakeAway({ kind, onClose, onSet }: { kind: OptionalKind; onClose: () => void; onSet: () => Promise<unknown> }) {
  const off = useMutation({ mutationFn: () => setRetentionPeriod(kind.key, null).then(onSet), onSuccess: onClose })
  return (
    <>
      <DialogTitle>Take the period away?</DialogTitle>
      <DialogContent>
        <Typography variant="body2">
          {kind.label} will be kept again, however old. What the period has already removed does not come back.
        </Typography>
        {!!off.error && <Alert severity="error" sx={{ mt: 2 }}>{apiError(off.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={off.isPending}>Cancel</Button>
        <Button variant="contained" disabled={off.isPending} onClick={() => off.mutate()}>Take it away</Button>
      </DialogActions>
    </>
  )
}

export function OptionalPeriods({ data, onChanged }: { data: Statement; onChanged: () => Promise<unknown> }) {
  const [setting, setSetting] = useState<OptionalKind | null>(null)
  const [removing, setRemoving] = useState<OptionalKind | null>(null)
  return (
    <GlassCard sx={{ p: 2, mb: 2 }} data-testid="optional">
      <Box sx={{ mb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Periods the organisation may set</Typography>
        <Typography variant="body2" color="text.secondary">{data.optional_note}</Typography>
      </Box>
      {data.optional.map((k) => (
        <Box key={k.key} data-testid="optional-kind" sx={{ py: 1.25, borderTop: 1, borderColor: 'divider' }}>
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Typography variant="body2" sx={{ fontWeight: 600, flex: 1, minWidth: 220 }}>{k.label}</Typography>
            <Chip size="small" variant={k.days == null ? 'outlined' : 'filled'} color={k.days == null ? 'default' : 'warning'}
                  label={optionalLine(k)} />
          </Stack>
          {k.days != null && k.set_at && (
            <Typography variant="caption" color="text.secondary" component="div">Set {day(k.set_at)}. {k.removed_by}.</Typography>)}
          <Typography variant="caption" color="text.secondary" component="div">What is removed: {k.removes}.</Typography>
          {k.kept_whatever && (
            <Typography variant="caption" color="text.secondary" component="div">Kept whatever its age: {k.kept_whatever}.</Typography>)}
          {Object.values(k.taken_away).map((words) => (
            <Typography key={words} variant="caption" color="text.secondary" component="div">{words}.</Typography>))}
          {data.may_set && (
            <Stack direction="row" sx={{ gap: 1, mt: 0.75 }}>
              <Button size="small" variant="outlined" onClick={() => setSetting(k)}>
                {k.days == null ? 'Set a period' : 'Change the period'}
              </Button>
              {k.days != null && <Button size="small" onClick={() => setRemoving(k)}>Take the period away</Button>}
            </Stack>)}
        </Box>))}
      <Dialog open={!!setting} onClose={() => setSetting(null)} maxWidth="sm" fullWidth>
        {setting ? <PeriodForm kind={setting} least={data.least_days} onClose={() => setSetting(null)} onSet={onChanged} /> : null}
      </Dialog>
      <Dialog open={!!removing} onClose={() => setRemoving(null)} maxWidth="xs" fullWidth>
        {removing ? <TakeAway kind={removing} onClose={() => setRemoving(null)} onSet={onChanged} /> : null}
      </Dialog>
    </GlassCard>
  )
}

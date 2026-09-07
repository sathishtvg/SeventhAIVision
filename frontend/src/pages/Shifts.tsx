import { useEffect, useMemo, useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, FormControlLabel, IconButton, InputLabel, MenuItem, Select,
  Switch, TextField, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/Edit'
import DeleteIcon from '@mui/icons-material/Delete'
import LightModeIcon from '@mui/icons-material/LightMode'
import DarkModeIcon from '@mui/icons-material/DarkMode'
import ScheduleIcon from '@mui/icons-material/Schedule'
import FreeBreakfastIcon from '@mui/icons-material/FreeBreakfast'
import PaidIcon from '@mui/icons-material/Paid'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  createShiftDefinition, deleteShiftDefinition, listShiftDefinitions,
  updateShiftDefinition,
  type ShiftDefinition, type ShiftKind,
} from '@/api/roster'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { fadeUpSx } from '@/lib/motion'

/** Icon, colour and wording per shift type, in one place so the cards, the
 *  chips and the create dialog cannot describe the same shift differently. */
const KINDS: Record<ShiftKind, { label: string; colour: string; icon: React.ReactNode }> = {
  day:     { label: 'Day',     colour: '#F5A524', icon: <LightModeIcon sx={{ fontSize: 15 }} /> },
  night:   { label: 'Night',   colour: '#6C63FF', icon: <DarkModeIcon sx={{ fontSize: 15 }} /> },
  general: { label: 'General', colour: '#00D9C0', icon: <ScheduleIcon sx={{ fontSize: 15 }} /> },
  split:   { label: 'Split',   colour: '#FF9800', icon: <ScheduleIcon sx={{ fontSize: 15 }} /> },
}

function hexToRgb(hex: string) {
  const h = hex.replace('#', '')
  return `${parseInt(h.slice(0, 2), 16)},${parseInt(h.slice(2, 4), 16)},${parseInt(h.slice(4, 6), 16)}`
}

/** Minutes between two HH:MM times, wrapping past midnight. Mirrors the
 *  server's rule — equal times mean 24 hours, never zero — so the dialog can
 *  show the duration before anything is saved. */
function durationMinutes(start: string, end: string): number | null {
  const m = (v: string) => {
    const [h, mm] = v.split(':').map(Number)
    return Number.isFinite(h) && Number.isFinite(mm) ? h * 60 + mm : null
  }
  const s = m(start), e = m(end)
  if (s == null || e == null) return null
  return ((e - s + 1440) % 1440) || 1440
}

// ── Create / edit ────────────────────────────────────────────────────────────

function ShiftDialog({ open, shift, onClose }: {
  open: boolean; shift: ShiftDefinition | null; onClose: () => void
}) {
  const qc = useQueryClient()
  const isEdit = Boolean(shift)

  const [name, setName] = useState('')
  const [kind, setKind] = useState<ShiftKind>('day')
  const [start, setStart] = useState('07:00')
  const [end, setEnd] = useState('19:00')
  const [grace, setGrace] = useState('')
  const [breakMins, setBreakMins] = useState('30')
  const [ot, setOt] = useState(false)

  // Resync when the dialog opens or switches which shift it is editing. The
  // dialog is rendered unconditionally, so useState initialisers only run once.
  useEffect(() => {
    if (!open) return
    setName(shift?.name ?? '')
    setKind(shift?.shift_type ?? 'day')
    setStart(shift?.start_time ?? '07:00')
    setEnd(shift?.end_time ?? '19:00')
    setGrace(shift?.grace_minutes != null ? String(shift.grace_minutes) : '')
    setBreakMins(String(shift?.break_minutes ?? 30))
    setOt(shift?.ot_eligible ?? false)
  }, [open, shift])

  const mins = durationMinutes(start, end)
  const crosses = mins != null && (() => {
    const [h, m] = start.split(':').map(Number)
    return h * 60 + m + mins >= 1440
  })()

  const payload = () => ({
    name: name.trim(),
    shift_type: kind,
    start_time: start,
    end_time: end,
    grace_minutes: grace === '' ? null : Number(grace),
    break_minutes: breakMins === '' ? 0 : Number(breakMins),
    ot_eligible: ot,
    colour: KINDS[kind].colour,
  })

  const { mutate: save, isPending, error } = useMutation({
    mutationFn: () => (isEdit
      ? updateShiftDefinition(shift!.id, payload())
      : createShiftDefinition(payload())),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['shift-definitions'] })
      onClose()
    },
  })

  // The API rejects a duplicate name and an over-long break by name; showing
  // its reason beats a generic failure the operator has to guess at.
  const message = (error as { response?: { data?: { detail?: string } } } | null)
    ?.response?.data?.detail

  const breakTooLong = mins != null && Number(breakMins || 0) >= mins

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>{isEdit ? 'Edit shift' : 'New shift'}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 1 }}>
        {message && <Alert severity="warning">{message}</Alert>}

        <TextField
          label="Shift name" size="small" value={name} autoFocus
          onChange={(e) => setName(e.target.value)}
          placeholder="Day Shift"
          helperText="What the roster and the guard's app will call it"
        />

        <FormControl size="small" fullWidth>
          <InputLabel>Type</InputLabel>
          <Select value={kind} label="Type" onChange={(e) => setKind(e.target.value as ShiftKind)}>
            {(Object.keys(KINDS) as ShiftKind[]).map((k) => (
              <MenuItem key={k} value={k}>{KINDS[k].label}</MenuItem>
            ))}
          </Select>
        </FormControl>

        <Stack direction="row" spacing={1.5} alignItems="flex-start">
          <TextField
            label="Start" type="time" size="small" value={start}
            onChange={(e) => setStart(e.target.value)} sx={{ flex: 1 }}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <TextField
            label="End" type="time" size="small" value={end}
            onChange={(e) => setEnd(e.target.value)} sx={{ flex: 1 }}
            slotProps={{ inputLabel: { shrink: true } }}
          />
          <Box sx={{ minWidth: 66, pt: 0.5, textAlign: 'right' }}>
            <Typography sx={{ fontWeight: 800, color: 'primary.main', lineHeight: 1.2 }}>
              {mins != null ? `${(mins / 60).toFixed(mins % 60 ? 1 : 0)}h` : '—'}
            </Typography>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.6rem' }}>
              {crosses ? 'ends next day' : 'duration'}
            </Typography>
          </Box>
        </Stack>

        <Stack direction="row" spacing={1.5}>
          <TextField
            label="Grace (min)" type="number" size="small" value={grace}
            onChange={(e) => setGrace(e.target.value)} sx={{ flex: 1 }}
            slotProps={{ htmlInput: { min: 0, max: 240 } }}
            helperText="Blank = use the site's"
          />
          <TextField
            label="Break (min)" type="number" size="small" value={breakMins}
            onChange={(e) => setBreakMins(e.target.value)} sx={{ flex: 1 }}
            slotProps={{ htmlInput: { min: 0, max: 480 } }}
            error={breakTooLong}
            helperText={breakTooLong ? 'Longer than the shift' : ' '}
          />
        </Stack>

        <FormControlLabel
          control={<Switch checked={ot} onChange={(e) => setOt(e.target.checked)} />}
          label={
            <Box>
              <Typography variant="body2">Overtime eligible</Typography>
              <Typography variant="caption" color="text.secondary">
                Hours beyond this shift earn OT
              </Typography>
            </Box>
          }
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button
          variant="contained" onClick={() => save()}
          disabled={isPending || !name.trim() || mins == null || breakTooLong}
        >
          {isEdit ? 'Save' : 'Create shift'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── One shift ────────────────────────────────────────────────────────────────

function ShiftCard({ shift, index, onEdit, onDelete }: {
  shift: ShiftDefinition; index: number
  onEdit: () => void; onDelete: () => void
}) {
  const kind = KINDS[shift.shift_type] ?? KINDS.general
  const colour = shift.colour || kind.colour

  return (
    <GlassCard sx={{ p: 2, ...fadeUpSx(index), opacity: shift.is_active ? 1 : 0.55 }}>
      <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 1.5 }}>
        <Box sx={{
          width: 36, height: 36, borderRadius: '10px', flexShrink: 0,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: `rgba(${hexToRgb(colour)},0.16)`, color: colour,
        }}>
          {kind.icon}
        </Box>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="subtitle2" noWrap sx={{ fontWeight: 800 }}>{shift.name}</Typography>
          <Stack direction="row" spacing={0.5} sx={{ mt: 0.25 }}>
            <Chip
              size="small" label={kind.label}
              sx={{
                height: 18, fontSize: '0.6rem', fontWeight: 700,
                color: colour, background: `rgba(${hexToRgb(colour)},0.14)`,
              }}
            />
            {!shift.is_active && (
              <Chip size="small" variant="outlined" label="Retired"
                    sx={{ height: 18, fontSize: '0.6rem' }} />
            )}
          </Stack>
        </Box>
      </Stack>

      {/* The times are the point of the card, so they get the weight. */}
      <Stack
        direction="row" alignItems="center" spacing={1}
        sx={{ px: 1.5, py: 1.25, borderRadius: '10px', background: 'rgba(255,255,255,0.04)', mb: 1.25 }}
      >
        <Box>
          <Typography sx={{ fontFamily: 'monospace', fontWeight: 800, fontSize: '1.05rem', lineHeight: 1.2 }}>
            {shift.start_time}
          </Typography>
          <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.55rem', letterSpacing: '0.08em' }}>
            START
          </Typography>
        </Box>
        <Typography sx={{ color: 'text.disabled', px: 0.5 }}>&rarr;</Typography>
        {shift.crosses_midnight && (
          <Tooltip title="Ends on the following day">
            <Chip size="small" label="+1" sx={{ height: 17, fontSize: '0.58rem', fontWeight: 700 }} />
          </Tooltip>
        )}
        <Box>
          <Typography sx={{ fontFamily: 'monospace', fontWeight: 800, fontSize: '1.05rem', lineHeight: 1.2 }}>
            {shift.end_time}
          </Typography>
          <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.55rem', letterSpacing: '0.08em' }}>
            END
          </Typography>
        </Box>
        <Box sx={{ flex: 1 }} />
        <Box sx={{ textAlign: 'right' }}>
          <Typography sx={{ fontWeight: 800, fontSize: '1.05rem', lineHeight: 1.2, color: 'primary.main' }}>
            {shift.duration_hours % 1 === 0 ? shift.duration_hours : shift.duration_hours.toFixed(1)}h
          </Typography>
          <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.55rem', letterSpacing: '0.08em' }}>
            DURATION
          </Typography>
        </Box>
      </Stack>

      <Stack direction="row" spacing={2} sx={{ mb: 0.5, flexWrap: 'wrap', gap: 0.75 }}>
        <Stack direction="row" spacing={0.5} alignItems="center">
          <ScheduleIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
          <Typography variant="caption" color="text.secondary">
            Grace: {shift.grace_minutes != null ? `${shift.grace_minutes}min` : "site's"}
          </Typography>
        </Stack>
        <Stack direction="row" spacing={0.5} alignItems="center">
          <FreeBreakfastIcon sx={{ fontSize: 14, color: 'text.disabled' }} />
          <Typography variant="caption" color="text.secondary">Break: {shift.break_minutes}min</Typography>
        </Stack>
        <Stack direction="row" spacing={0.5} alignItems="center">
          <PaidIcon sx={{ fontSize: 14, color: shift.ot_eligible ? '#00E396' : 'text.disabled' }} />
          <Typography variant="caption" color="text.secondary">
            {shift.ot_eligible ? 'OT eligible' : 'OT not eligible'}
          </Typography>
        </Stack>
      </Stack>

      <PermissionGuard permission="shift:manage">
        <Stack direction="row" spacing={1} sx={{ mt: 1.5 }}>
          <Button size="small" variant="outlined" startIcon={<EditIcon sx={{ fontSize: 15 }} />}
                  onClick={onEdit} sx={{ flex: 1 }}>
            Edit
          </Button>
          <Tooltip title={shift.is_active ? 'Retire or delete this shift' : 'Delete permanently'}>
            <IconButton size="small" color="error" onClick={onDelete}
                        aria-label={`Remove ${shift.name}`}>
              <DeleteIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        </Stack>
      </PermissionGuard>
    </GlassCard>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function ShiftsPage() {
  const qc = useQueryClient()
  const [dialogOpen, setDialogOpen] = useState(false)
  const [editing, setEditing] = useState<ShiftDefinition | null>(null)
  const [showRetired, setShowRetired] = useState(false)
  const [note, setNote] = useState<string | null>(null)

  const { data: shifts = [], isLoading } = useQuery({
    queryKey: ['shift-definitions', showRetired],
    queryFn: () => listShiftDefinitions(showRetired),
  })

  const { mutate: remove } = useMutation({
    mutationFn: (id: string) => deleteShiftDefinition(id),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ['shift-definitions'] })
      // A shift already on a roster is retired rather than deleted, so the
      // history keeps its name. Say which happened instead of leaving the
      // operator to notice the row is still there.
      setNote(res.retired
        ? `Retired — ${res.in_use_by} roster entr${res.in_use_by === 1 ? 'y uses' : 'ies use'} this shift, so it was kept for the record.`
        : 'Shift deleted.')
    },
  })

  const active = useMemo(() => shifts.filter((s) => s.is_active).length, [shifts])

  return (
    <Box>
      <PageHeader
        pageKey="shifts"
        action={
          <PermissionGuard permission="shift:manage">
            <Button
              variant="contained" size="small" startIcon={<AddIcon />}
              onClick={() => { setEditing(null); setDialogOpen(true) }}
            >
              Add Shift
            </Button>
          </PermissionGuard>
        }
      />

      <Stack direction="row" alignItems="center" spacing={1.5} sx={{ mb: 2 }}>
        <Typography variant="body2" color="text.secondary">
          {active} shift{active === 1 ? '' : 's'} configured
        </Typography>
        <Box sx={{ flex: 1 }} />
        <FormControlLabel
          control={<Switch size="small" checked={showRetired}
                           onChange={(e) => setShowRetired(e.target.checked)} />}
          label={<Typography variant="caption">Show retired</Typography>}
        />
      </Stack>

      {note && <Alert severity="info" sx={{ mb: 2 }} onClose={() => setNote(null)}>{note}</Alert>}

      {isLoading ? (
        <GlassCard sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary">Loading shifts…</Typography>
        </GlassCard>
      ) : shifts.length === 0 ? (
        <GlassCard sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary" sx={{ mb: 1 }}>
            No shifts configured yet.
          </Typography>
          <Typography variant="caption" color="text.disabled">
            A shift is the pattern you roster people onto — "Day Shift, 07:00 to 19:00" —
            defined once here and then assigned.
          </Typography>
        </GlassCard>
      ) : (
        <Box sx={{
          display: 'grid', gap: 2, alignItems: 'start',
          gridTemplateColumns: 'repeat(auto-fill, minmax(320px, 1fr))',
        }}>
          {shifts.map((s, i) => (
            <ShiftCard
              key={s.id} shift={s} index={i}
              onEdit={() => { setEditing(s); setDialogOpen(true) }}
              onDelete={() => remove(s.id)}
            />
          ))}
        </Box>
      )}

      <ShiftDialog
        open={dialogOpen} shift={editing}
        onClose={() => { setDialogOpen(false); setEditing(null) }}
      />
    </Box>
  )
}

export default ShiftsPage

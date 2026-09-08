/**
 * Man Down — a guard has stopped moving and somebody has to go and look.
 *
 * The only page in this application where an empty list is the good outcome,
 * so it is built around that: live events are enormous and impossible to miss,
 * everything closed collapses into history below.
 *
 * A supervisor working a live event needs three things in the first two
 * seconds — who, where, and how long ago. Everything else is below the fold.
 * The one non-obvious field that earns its place is the phone battery: a
 * handset at 2% that stopped reporting is a different story from one at 80%,
 * and it changes whether you send somebody now.
 *
 * The page polls hard. Every other list here refetches on a lazy interval;
 * this one is the exception, because thirty seconds of staleness is thirty
 * seconds nobody is moving.
 */
import { useMemo, useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  MenuItem, Skeleton, Switch, TextField, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import PersonOffIcon from '@mui/icons-material/PersonOff'
import BatteryAlertIcon from '@mui/icons-material/BatteryAlert'
import PlaceIcon from '@mui/icons-material/Place'
import PhonelinkEraseIcon from '@mui/icons-material/PhonelinkErase'
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutlineOutlined'
import SettingsIcon from '@mui/icons-material/Settings'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  acknowledgeManDown, getManDownSettings, listManDown, resolveManDown,
  updateManDownSettings,
  MAN_DOWN_OUTCOMES, OUTCOME_LABELS, TRIGGER_LABELS,
  type ManDownEvent,
} from '@/api/mandown'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { FilterRail } from '@/components/common/FilterRail'
import { useAuthStore } from '@/store/auth'

const CAN_MANAGE = new Set([1, 2, 3, 4, 8])

const STATUS_META: Record<string, { label: string; colour: string }> = {
  pending: { label: 'Counting down', colour: '#F5A524' },
  escalated: { label: 'HELP NEEDED', colour: '#FF4560' },
  acknowledged: { label: 'Responding', colour: '#6C63FF' },
  cancelled: { label: 'Cancelled by guard', colour: '#8892A6' },
  resolved: { label: 'Resolved', colour: '#00D97E' },
}

const LIVE = new Set(['pending', 'escalated', 'acknowledged'])

function fmt(ts: string | null) {
  if (!ts) return '—'
  return new Date(ts).toLocaleString(undefined, {
    day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', second: '2-digit',
  })
}

function ago(ts: string) {
  const seconds = Math.max(0, Math.floor((Date.now() - new Date(ts).getTime()) / 1000))
  if (seconds < 60) return `${seconds}s ago`
  if (seconds < 3600) return `${Math.floor(seconds / 60)} min ago`
  return `${Math.floor(seconds / 3600)} h ago`
}

function apiError(e: unknown, fallback: string) {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

// ── Live event ───────────────────────────────────────────────────────────────

function LiveEvent({ row, canManage, onResolve }: {
  row: ManDownEvent; canManage: boolean; onResolve: () => void
}) {
  const qc = useQueryClient()
  const meta = STATUS_META[row.status] ?? STATUS_META.pending
  const [error, setError] = useState('')

  const acknowledge = useMutation({
    mutationFn: () => acknowledgeManDown(row.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['man-down'] }),
    onError: (e) => setError(apiError(e, 'Could not acknowledge')),
  })

  const lowBattery = row.battery_level !== null && row.battery_level <= 15

  return (
    <GlassCard sx={{
      p: 2,
      border: `2px solid ${meta.colour}`,
      background: row.status === 'escalated' ? 'rgba(255,69,96,0.08)' : undefined,
    }}>
      {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}

      <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1 }}>
        <PersonOffIcon sx={{ fontSize: 26, color: meta.colour }} />
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="h6" sx={{ fontWeight: 800, fontSize: '1.15rem', lineHeight: 1.2 }}>
            {row.guard_name || 'Unknown guard'}
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>
            {row.site_name || 'No site'} · {TRIGGER_LABELS[row.trigger] ?? row.trigger}
            {' · '}{ago(row.detected_at)}
          </Typography>
        </Box>
        <Chip
          label={meta.label}
          sx={{
            fontWeight: 800, bgcolor: `${meta.colour}22`, color: meta.colour,
            fontSize: '0.72rem',
          }}
        />
      </Stack>

      <Stack direction="row" spacing={1} sx={{ mb: 1.5, flexWrap: 'wrap', gap: 1 }}>
        {row.status === 'pending' && (
          <Chip
            size="small" label={`${row.seconds_remaining}s to escalate`}
            sx={{ bgcolor: 'rgba(245,165,36,0.18)', color: '#F5A524', fontWeight: 700 }}
          />
        )}
        {row.latitude !== null && row.longitude !== null ? (
          <Tooltip title={`Accuracy ±${Math.round(row.accuracy_m ?? 0)}m`}>
            <Chip
              size="small" icon={<PlaceIcon sx={{ fontSize: 15 }} />}
              label={`${row.latitude.toFixed(5)}, ${row.longitude.toFixed(5)}`}
              component="a" clickable
              href={`https://www.google.com/maps?q=${row.latitude},${row.longitude}`}
              target="_blank" rel="noopener noreferrer"
            />
          </Tooltip>
        ) : (
          <Chip size="small" label="No position — likely indoors" />
        )}
        {row.battery_level !== null && (
          <Chip
            size="small"
            icon={lowBattery ? <BatteryAlertIcon sx={{ fontSize: 15 }} /> : undefined}
            label={`Battery ${row.battery_level}%`}
            sx={lowBattery
              ? { bgcolor: 'rgba(255,69,96,0.18)', color: '#FF4560', fontWeight: 700 }
              : undefined}
          />
        )}
        {row.escalated_by_server && (
          <Tooltip title="The phone never called back — it may not have survived">
            <Chip
              size="small" icon={<PhonelinkEraseIcon sx={{ fontSize: 15 }} />}
              label="No response from the handset"
              sx={{ bgcolor: 'rgba(255,69,96,0.18)', color: '#FF4560', fontWeight: 700 }}
            />
          </Tooltip>
        )}
      </Stack>

      {row.status === 'acknowledged' && (
        <Typography variant="caption" sx={{ display: 'block', mb: 1, color: 'text.secondary' }}>
          {row.acknowledged_by_name} is responding since {fmt(row.acknowledged_at)}
        </Typography>
      )}

      {canManage && (
        <Stack direction="row" spacing={1}>
          {row.status === 'escalated' && (
            <Button
              variant="contained" color="error" disabled={acknowledge.isPending}
              onClick={() => { setError(''); acknowledge.mutate() }}
            >
              I am responding
            </Button>
          )}
          {row.status !== 'pending' && (
            <Button
              variant="outlined"
              startIcon={<CheckCircleOutlineIcon sx={{ fontSize: 17 }} />}
              onClick={onResolve}
            >
              Close it out
            </Button>
          )}
        </Stack>
      )}
    </GlassCard>
  )
}

// ── History row ──────────────────────────────────────────────────────────────

function HistoryRow({ row }: { row: ManDownEvent }) {
  const meta = STATUS_META[row.status] ?? STATUS_META.cancelled
  return (
    <Stack
      direction="row" alignItems="center" spacing={1.25}
      sx={{
        px: 1.25, py: 0.9, borderRadius: '8px',
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.07)',
      }}
    >
      <Box sx={{ minWidth: 150 }}>
        <Typography variant="body2" noWrap sx={{ fontWeight: 700, fontSize: '0.8rem' }}>
          {row.guard_name || 'Unknown guard'}
        </Typography>
        <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.63rem' }}>
          {fmt(row.detected_at)}
        </Typography>
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap sx={{ fontSize: '0.8rem' }}>
          {TRIGGER_LABELS[row.trigger] ?? row.trigger}
          {row.site_name ? ` · ${row.site_name}` : ''}
        </Typography>
        {row.notes && (
          <Typography variant="caption" noWrap sx={{ display: 'block', color: 'text.secondary', fontSize: '0.63rem' }}>
            {row.notes}
          </Typography>
        )}
      </Box>
      {row.outcome && (
        <Chip size="small" variant="outlined" label={OUTCOME_LABELS[row.outcome] ?? row.outcome}
              sx={{ height: 18, fontSize: '0.6rem' }} />
      )}
      <Chip
        size="small" label={meta.label}
        sx={{ height: 18, fontSize: '0.6rem', bgcolor: `${meta.colour}22`, color: meta.colour }}
      />
    </Stack>
  )
}

// ── Dialogs ──────────────────────────────────────────────────────────────────

function ResolveDialog({ row, onClose }: { row: ManDownEvent; onClose: () => void }) {
  const qc = useQueryClient()
  const [outcome, setOutcome] = useState('guard_ok')
  const [notes, setNotes] = useState('')
  const [error, setError] = useState('')

  const resolve = useMutation({
    mutationFn: () => resolveManDown(row.id, { outcome, notes: notes.trim() || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['man-down'] }); onClose() },
    onError: (e) => setError(apiError(e, 'Could not close this out')),
  })

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle sx={{ pb: 1 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 800 }}>Close this out</Typography>
        <Typography variant="caption" color="text.secondary">
          {row.guard_name} · {fmt(row.detected_at)}
        </Typography>
      </DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        <Stack spacing={1.5} sx={{ mt: 0.5 }}>
          <TextField select size="small" label="What actually happened" value={outcome}
                     onChange={(e) => setOutcome(e.target.value)}>
            {MAN_DOWN_OUTCOMES.map((o) => (
              <MenuItem key={o} value={o}>{OUTCOME_LABELS[o]}</MenuItem>
            ))}
          </TextField>
          <TextField size="small" multiline rows={2} label="Notes" value={notes} autoFocus
                     onChange={(e) => setNotes(e.target.value)} />
          <Typography variant="caption" color="text.secondary">
            The false-alarm rate is what tells you whether the thresholds are right for
            how your officers work.
          </Typography>
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={resolve.isPending}
                onClick={() => { setError(''); resolve.mutate() }}>
          Close out
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function SettingsDialog({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({
    queryKey: ['man-down-settings'],
    queryFn: getManDownSettings,
  })
  const [draft, setDraft] = useState<Record<string, number | boolean> | null>(null)
  const [error, setError] = useState('')

  const current = draft ?? data ?? null

  const save = useMutation({
    mutationFn: () => updateManDownSettings(current as never),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['man-down-settings'] })
      onClose()
    },
    onError: (e) => setError(apiError(e, 'Could not save these thresholds')),
  })

  const set = (key: string, value: number | boolean) =>
    setDraft({ ...(current ?? {}), [key]: value } as Record<string, number | boolean>)

  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Man-down thresholds</DialogTitle>
      <DialogContent dividers>
        {error && <Alert severity="error" sx={{ mb: 1.5 }}>{error}</Alert>}
        {isLoading || !current ? (
          <Stack spacing={1}>
            {[0, 1, 2].map((i) => <Skeleton key={i} variant="rounded" height={44} />)}
          </Stack>
        ) : (
          <Stack spacing={2} sx={{ mt: 0.5 }}>
            <Stack direction="row" alignItems="center" spacing={1}>
              <Switch
                checked={Boolean(current.enabled)}
                onChange={(e) => set('enabled', e.target.checked)}
              />
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 700 }}>
                  Monitor guards&apos; phones
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  Off by default. Detection runs while the app is open — iOS suspends
                  sensors in the background, so this does not cover a pocketed phone
                  with the app closed.
                </Typography>
              </Box>
            </Stack>

            <TextField
              size="small" type="number" label="Seconds without movement"
              value={current.no_motion_seconds}
              onChange={(e) => set('no_motion_seconds', Number(e.target.value))}
              helperText="A guard writing in the occurrence book is also still. 120s is a
                          reasonable floor; a static gatehouse post wants more."
            />
            <TextField
              size="small" type="number" label="Countdown before escalating"
              value={current.countdown_seconds}
              onChange={(e) => set('countdown_seconds', Number(e.target.value))}
              helperText="How long the guard has to say they are fine."
            />
            <TextField
              size="small" type="number" label="Impact threshold (g)"
              value={current.impact_threshold_g}
              onChange={(e) => set('impact_threshold_g', Number(e.target.value))}
              helperText="An impact shortens the no-motion window rather than triggering
                          on its own — a fall is a spike, then stillness."
            />
          </Stack>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!current || save.isPending}
                onClick={() => { setError(''); save.mutate() }}>
          Save
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Page ─────────────────────────────────────────────────────────────────────

export function ManDownPage() {
  const roleId = useAuthStore((s) => s.user?.roleId ?? 0)
  const canManage = CAN_MANAGE.has(roleId)

  const [siteFilter, setSiteFilter] = useState('')
  const [resolving, setResolving] = useState<ManDownEvent | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const { data: events = [], isLoading } = useQuery({
    queryKey: ['man-down', siteFilter],
    queryFn: () => listManDown({ site_id: siteFilter || undefined }),
    // Deliberately aggressive. Every other list here refetches lazily; thirty
    // seconds of staleness on this one is thirty seconds nobody is moving.
    refetchInterval: 10_000,
  })

  const { data: settings } = useQuery({
    queryKey: ['man-down-settings'],
    queryFn: getManDownSettings,
  })

  const live = events.filter((e) => LIVE.has(e.status))
  const history = events.filter((e) => !LIVE.has(e.status))

  const siteOptions = useMemo(
    () => [{ value: '', label: 'All sites' },
           ...sites.map((s) => ({ value: s.id, label: s.name }))],
    [sites],
  )

  return (
    <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'flex-start' }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <PageHeader
          pageKey="man-down"
          action={canManage ? (
            <Button size="small" variant="outlined" startIcon={<SettingsIcon />}
                    onClick={() => setSettingsOpen(true)}>
              Thresholds
            </Button>
          ) : undefined}
        />

        {settings && !settings.enabled && (
          <Alert severity="info" sx={{ mb: 1.5 }}>
            Man-down monitoring is off. Guards&apos; phones are not watching for a fall
            until you turn it on.
          </Alert>
        )}

        {isLoading ? (
          <Stack spacing={1.5}>
            {[0, 1].map((i) => <Skeleton key={i} variant="rounded" height={150} />)}
          </Stack>
        ) : (
          <>
            {live.length > 0 && (
              <Stack spacing={1.5} sx={{ mb: 2.5 }}>
                {live.map((row) => (
                  <LiveEvent
                    key={row.id} row={row} canManage={canManage}
                    onResolve={() => setResolving(row)}
                  />
                ))}
              </Stack>
            )}

            {live.length === 0 && (
              <GlassCard sx={{ p: 3, textAlign: 'center', mb: 2 }}>
                <CheckCircleOutlineIcon sx={{ fontSize: 34, color: '#00D97E', mb: 1 }} />
                <Typography sx={{ fontWeight: 700 }}>Nobody is down.</Typography>
                <Typography variant="caption" color="text.secondary">
                  The only page here where an empty list is the good outcome.
                </Typography>
              </GlassCard>
            )}

            {history.length > 0 && (
              <>
                <Typography variant="caption" sx={{ fontWeight: 800, letterSpacing: '0.04em' }}>
                  HISTORY
                </Typography>
                <Stack spacing={0.75} sx={{ mt: 0.75 }}>
                  {history.map((row) => <HistoryRow key={row.id} row={row} />)}
                </Stack>
              </>
            )}
          </>
        )}
      </Box>

      <FilterRail
        storageKey="man-down"
        groups={[{
          key: 'site', label: 'Site', options: siteOptions,
          value: siteFilter, onChange: setSiteFilter,
        }]}
      />

      {resolving && <ResolveDialog row={resolving} onClose={() => setResolving(null)} />}
      {settingsOpen && <SettingsDialog onClose={() => setSettingsOpen(false)} />}
    </Box>
  )
}

export default ManDownPage

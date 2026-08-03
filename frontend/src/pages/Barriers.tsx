/**
 * Barriers — gate/boom control for the command centre.
 *
 * Two jobs: let an operator open a gate right now, and make every actuation
 * accountable afterwards. The command log shows automatic openings (the ANPR
 * decision engine) alongside manual ones in one list, because "why did that
 * gate open at 03:12" is the question this page exists to answer.
 *
 * The action buttons are driven by the backend's per-vendor capability matrix
 * rather than a fixed list — several Dahua barrier models genuinely cannot
 * latch open, and offering a button that is guaranteed to fail is worse than
 * not offering it.
 */
import { useMemo, useState } from 'react'
import {
  Box, Typography, Chip, Select, MenuItem, FormControl, InputLabel, Skeleton,
  Button, Divider, Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  IconButton, Tooltip, Switch, FormControlLabel, Alert,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import CloseIcon from '@mui/icons-material/Close'
import LockOpenIcon from '@mui/icons-material/LockOpen'
import LockIcon from '@mui/icons-material/Lock'
import PushPinIcon from '@mui/icons-material/PushPin'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import RefreshIcon from '@mui/icons-material/Refresh'
import HistoryIcon from '@mui/icons-material/History'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  listBarriers, createBarrier, updateBarrier, deleteBarrier, issueBarrierCommand,
  getBarrierStatus, listBarrierCommands, getVendorCapabilities,
  VENDOR_LABELS, COMMAND_LABELS,
  type Barrier, type BarrierVendor, type BarrierCommand, type BarrierInput,
} from '@/api/barriers'
import { getSites } from '@/api/sites'
import { getCameras } from '@/api/cameras'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { fadeUpSx, useCountUp } from '@/lib/motion'

const STATUS_META: Record<string, { label: string; color: string }> = {
  open: { label: 'Open', color: '#00E396' },
  closed: { label: 'Closed', color: '#7A8195' },
  held_open: { label: 'Held Open', color: '#FF9800' },
  unknown: { label: 'Unknown', color: '#7A8195' },
  error: { label: 'Fault', color: '#FF4560' },
}

/** Vendors whose barrier is driven by the ANPR camera's own relay output —
 * surfaced in the form so an installer understands they are entering the
 * CAMERA's address, not a separate controller's. */
const CAMERA_IO_VENDORS: BarrierVendor[] = ['hikvision_camera_io', 'dahua_camera_io']

const COMMAND_ICONS: Record<string, JSX.Element> = {
  open: <LockOpenIcon fontSize="small" />,
  close: <LockIcon fontSize="small" />,
  hold_open: <PushPinIcon fontSize="small" />,
  release_hold: <LockIcon fontSize="small" />,
  emergency_override: <WarningAmberIcon fontSize="small" />,
}

function StatusChip({ status }: { status: string | null }) {
  const meta = STATUS_META[status ?? 'unknown'] ?? STATUS_META.unknown
  return (
    <Chip
      size="small"
      label={meta.label}
      sx={{
        bgcolor: `${meta.color}22`,
        color: meta.color,
        fontWeight: 600,
        transition: 'background-color 0.2s, color 0.2s',
      }}
    />
  )
}

export function BarriersPage() {
  const qc = useQueryClient()
  const [siteFilter, setSiteFilter] = useState('')
  const [formOpen, setFormOpen] = useState(false)
  const [editing, setEditing] = useState<Barrier | null>(null)
  const [logFor, setLogFor] = useState<Barrier | null>(null)
  const [overrideFor, setOverrideFor] = useState<Barrier | null>(null)
  const [overrideReason, setOverrideReason] = useState('')
  const [error, setError] = useState<string | null>(null)

  const { data: barriers = [], isLoading } = useQuery({
    queryKey: ['barriers', siteFilter],
    queryFn: () => listBarriers(siteFilter || undefined),
  })
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: capabilities = [] } = useQuery({
    queryKey: ['barrier-vendors'],
    queryFn: getVendorCapabilities,
  })

  const capsFor = (vendor: BarrierVendor): BarrierCommand[] =>
    capabilities.find((c) => c.vendor === vendor)?.commands ?? []

  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['barriers'] })
  }

  const commandMut = useMutation({
    mutationFn: ({ id, command, reason }: { id: string; command: BarrierCommand; reason?: string }) =>
      issueBarrierCommand(id, command, reason),
    onSuccess: () => { setError(null); invalidate() },
    // A 502 means the device refused or was unreachable. The attempt IS
    // recorded either way — surface the device's own message rather than a
    // generic failure, because "authentication rejected" and "timed out" send
    // the operator to very different places.
    onError: (e: any) => setError(e?.response?.data?.detail ?? 'Command failed'),
  })

  const statusMut = useMutation({
    mutationFn: (id: string) => getBarrierStatus(id),
    onSuccess: () => invalidate(),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteBarrier(id),
    onSuccess: invalidate,
  })

  const stats = useMemo(() => ({
    total: barriers.length,
    open: barriers.filter((b) => b.last_status === 'open' || b.last_status === 'held_open').length,
    faults: barriers.filter((b) => b.last_error).length,
    auto: barriers.filter((b) => b.auto_open_enabled).length,
  }), [barriers])

  const totalCount = useCountUp(stats.total)
  const openCount = useCountUp(stats.open)
  const faultCount = useCountUp(stats.faults)
  const autoCount = useCountUp(stats.auto)

  const runCommand = (b: Barrier, command: BarrierCommand) => {
    if (command === 'emergency_override') {
      setOverrideFor(b)
      setOverrideReason('')
      return
    }
    commandMut.mutate({ id: b.id, command })
  }

  return (
    <Box>
      <PageHeader
        title="Barriers"
        subtitle="Gate and boom control, with a full record of every opening"
        action={
          <PermissionGuard permission="barrier:manage">
            <Button
              variant="contained"
              size="medium"
              startIcon={<AddIcon />}
              onClick={() => { setEditing(null); setFormOpen(true) }}
            >
              Add Barrier
            </Button>
          </PermissionGuard>
        }
      />

      {error && (
        <Alert severity="error" onClose={() => setError(null)} sx={{ mb: 2 }}>
          {error}
        </Alert>
      )}

      <Stack direction="row" spacing={2} sx={{ mb: 3, flexWrap: 'wrap' }}>
        {[
          { label: 'Barriers', value: totalCount },
          { label: 'Currently Open', value: openCount },
          { label: 'Reporting Faults', value: faultCount },
          { label: 'Auto-Open Enabled', value: autoCount },
        ].map((k, i) => (
          <GlassCard key={k.label} sx={{ ...fadeUpSx(i), p: 2, minWidth: 170, flex: 1 }}>
            <Typography variant="caption" color="text.secondary">{k.label}</Typography>
            <Typography variant="h4" fontWeight={700}>{k.value}</Typography>
          </GlassCard>
        ))}
      </Stack>

      <FormControl size="small" sx={{ minWidth: 220, mb: 2 }}>
        <InputLabel>Site</InputLabel>
        <Select label="Site" value={siteFilter} onChange={(e) => setSiteFilter(e.target.value)}>
          <MenuItem value="">All Sites</MenuItem>
          {sites.map((s: any) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
        </Select>
      </FormControl>

      {isLoading && <Skeleton variant="rounded" height={180} />}

      {!isLoading && barriers.length === 0 && (
        <GlassCard sx={{ p: 4, textAlign: 'center' }}>
          <Typography color="text.secondary">
            No barriers configured. Add one to control a gate from here — it can be driven by a
            dedicated controller, by the ANPR camera&apos;s own relay output, or by a network relay board.
          </Typography>
        </GlassCard>
      )}

      <Stack spacing={2}>
        {barriers.map((b, i) => {
          const caps = capsFor(b.vendor)
          return (
            <GlassCard key={b.id} sx={{ ...fadeUpSx(i), p: 2 }}>
              <Stack direction="row" spacing={2} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                <Box sx={{ minWidth: 220, flex: 1 }}>
                  <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                    <Typography fontWeight={700}>{b.name}</Typography>
                    <StatusChip status={b.last_status} />
                    {!b.is_active && <Chip size="small" label="Inactive" />}
                  </Stack>
                  <Typography variant="caption" color="text.secondary">
                    {VENDOR_LABELS[b.vendor]} · {b.lane_direction}
                    {b.site_name ? ` · ${b.site_name}` : ''}
                    {b.camera_name ? ` · ${b.camera_name}` : ''}
                  </Typography>
                  {b.last_error && (
                    <Typography variant="caption" sx={{ display: 'block', color: '#FF4560' }}>
                      {b.last_error}
                    </Typography>
                  )}
                </Box>

                <PermissionGuard permission="barrier:operate">
                  <Stack direction="row" spacing={1} sx={{ flexWrap: 'wrap' }}>
                    {(['open', 'close', 'hold_open', 'release_hold', 'emergency_override'] as BarrierCommand[])
                      .filter((cmd) => cmd === 'emergency_override'
                        ? caps.includes('hold_open')
                        : caps.includes(cmd))
                      .map((cmd) => (
                        <Button
                          key={cmd}
                          size="small"
                          variant={cmd === 'open' ? 'contained' : 'outlined'}
                          color={cmd === 'emergency_override' ? 'warning' : 'primary'}
                          startIcon={COMMAND_ICONS[cmd]}
                          disabled={!b.is_active || commandMut.isPending}
                          onClick={() => runCommand(b, cmd)}
                        >
                          {COMMAND_LABELS[cmd]}
                        </Button>
                      ))}
                  </Stack>
                </PermissionGuard>

                <Stack direction="row" spacing={0.5}>
                  {caps.includes('status') && (
                    <Tooltip title="Query the device for its current position">
                      <span>
                        <IconButton size="small" onClick={() => statusMut.mutate(b.id)}
                          disabled={statusMut.isPending}>
                          <RefreshIcon fontSize="small" />
                        </IconButton>
                      </span>
                    </Tooltip>
                  )}
                  <Tooltip title="Command history">
                    <IconButton size="small" onClick={() => setLogFor(b)}>
                      <HistoryIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <PermissionGuard permission="barrier:manage">
                    <Tooltip title="Edit">
                      <IconButton size="small" onClick={() => { setEditing(b); setFormOpen(true) }}>
                        <AddIcon fontSize="small" sx={{ transform: 'rotate(45deg)' }} />
                      </IconButton>
                    </Tooltip>
                  </PermissionGuard>
                </Stack>
              </Stack>
            </GlassCard>
          )
        })}
      </Stack>

      <BarrierFormDialog
        open={formOpen}
        barrier={editing}
        sites={sites}
        onClose={() => { setFormOpen(false); setEditing(null) }}
        onSaved={() => { setFormOpen(false); setEditing(null); invalidate() }}
        onDeactivate={(id) => { deleteMut.mutate(id); setFormOpen(false); setEditing(null) }}
      />

      <CommandLogDialog barrier={logFor} onClose={() => setLogFor(null)} />

      {/* An override bypasses every access rule, so the reason is mandatory —
          it is what makes the action reviewable afterwards. */}
      <Dialog open={!!overrideFor} onClose={() => setOverrideFor(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Emergency Override — {overrideFor?.name}</DialogTitle>
        <DialogContent>
          <Alert severity="warning" sx={{ mb: 2 }}>
            This latches the barrier open, bypassing all access rules. The reason is recorded
            against your name in the command log.
          </Alert>
          <TextField
            autoFocus fullWidth multiline minRows={2} label="Reason (required)"
            value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)}
            placeholder="e.g. fire drill, ambulance access"
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOverrideFor(null)}>Cancel</Button>
          <Button
            variant="contained" color="warning"
            disabled={!overrideReason.trim()}
            onClick={() => {
              if (!overrideFor) return
              commandMut.mutate({
                id: overrideFor.id, command: 'emergency_override', reason: overrideReason.trim(),
              })
              setOverrideFor(null)
            }}
          >
            Confirm Override
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

function BarrierFormDialog({
  open, barrier, sites, onClose, onSaved, onDeactivate,
}: {
  open: boolean
  barrier: Barrier | null
  sites: any[]
  onClose: () => void
  onSaved: () => void
  onDeactivate: (id: string) => void
}) {
  const [form, setForm] = useState<BarrierInput>({ name: '', vendor: 'simulator' })
  const [saveError, setSaveError] = useState<string | null>(null)
  const { data: cameras = [] } = useQuery({
    queryKey: ['cameras'], queryFn: () => getCameras(), enabled: open,
  })

  // Resync on open / when a different barrier is picked. A useState initialiser
  // only runs on first mount, so without this the dialog would show the
  // previous barrier's values — the same bug already fixed in UserFormDialog.
  const key = `${open}:${barrier?.id ?? 'new'}`
  const [syncedKey, setSyncedKey] = useState('')
  if (open && syncedKey !== key) {
    setSyncedKey(key)
    setForm(barrier
      ? {
          name: barrier.name, vendor: barrier.vendor, site_id: barrier.site_id,
          camera_id: barrier.camera_id, lane_direction: barrier.lane_direction,
          host: barrier.host, port: barrier.port, username: barrier.username,
          relay_channel: barrier.relay_channel, pulse_ms: barrier.pulse_ms,
          auto_open_enabled: barrier.auto_open_enabled, is_active: barrier.is_active,
        }
      : { name: '', vendor: 'simulator', lane_direction: 'entry', pulse_ms: 1000, auto_open_enabled: true })
    setSaveError(null)
  }

  const isCameraIO = CAMERA_IO_VENDORS.includes(form.vendor)
  const isSimulator = form.vendor === 'simulator'

  const saveMut = useMutation({
    mutationFn: () => barrier ? updateBarrier(barrier.id, form) : createBarrier(form),
    onSuccess: onSaved,
    onError: (e: any) => setSaveError(e?.response?.data?.detail ?? 'Save failed'),
  })

  const set = <K extends keyof BarrierInput>(k: K, v: BarrierInput[K]) =>
    setForm((f) => ({ ...f, [k]: v }))

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        {barrier ? 'Edit Barrier' : 'Add Barrier'}
        <IconButton onClick={onClose} sx={{ position: 'absolute', right: 8, top: 8 }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        {saveError && <Alert severity="error" sx={{ mb: 2 }}>{saveError}</Alert>}
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField label="Name" fullWidth value={form.name}
            onChange={(e) => set('name', e.target.value)} />

          <FormControl fullWidth>
            <InputLabel>Control method</InputLabel>
            <Select label="Control method" value={form.vendor}
              onChange={(e) => set('vendor', e.target.value as BarrierVendor)}>
              {(Object.keys(VENDOR_LABELS) as BarrierVendor[]).map((v) => (
                <MenuItem key={v} value={v}>{VENDOR_LABELS[v]}</MenuItem>
              ))}
            </Select>
          </FormControl>

          {isCameraIO && (
            <Alert severity="info">
              The barrier is driven by the ANPR camera&apos;s own relay output. Enter the
              <strong> camera&apos;s</strong> address and credentials below, and the output port
              number its boom is wired to.
            </Alert>
          )}
          {isSimulator && (
            <Alert severity="info">
              Simulator: no hardware is contacted. Use this to exercise the decision engine and
              command log before the gate is installed.
            </Alert>
          )}

          <FormControl fullWidth>
            <InputLabel>Site</InputLabel>
            <Select label="Site" value={form.site_id ?? ''}
              onChange={(e) => set('site_id', e.target.value || null)}>
              <MenuItem value="">None</MenuItem>
              {sites.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
            </Select>
          </FormControl>

          <FormControl fullWidth>
            <InputLabel>{isCameraIO ? 'Camera (controls this barrier)' : 'ANPR camera on this lane'}</InputLabel>
            <Select
              label={isCameraIO ? 'Camera (controls this barrier)' : 'ANPR camera on this lane'}
              value={form.camera_id ?? ''}
              onChange={(e) => set('camera_id', e.target.value || null)}
            >
              <MenuItem value="">None</MenuItem>
              {cameras.map((c: any) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
            </Select>
          </FormControl>

          <FormControl fullWidth>
            <InputLabel>Lane</InputLabel>
            <Select label="Lane" value={form.lane_direction ?? 'entry'}
              onChange={(e) => set('lane_direction', e.target.value)}>
              <MenuItem value="entry">Entry</MenuItem>
              <MenuItem value="exit">Exit</MenuItem>
              <MenuItem value="bidirectional">Bidirectional</MenuItem>
            </Select>
          </FormControl>

          {!isSimulator && (
            <>
              <Stack direction="row" spacing={2}>
                <TextField label={isCameraIO ? 'Camera IP / host' : 'Device IP / host'}
                  fullWidth value={form.host ?? ''} onChange={(e) => set('host', e.target.value)} />
                <TextField label="Port" type="number" sx={{ width: 120 }}
                  value={form.port ?? ''}
                  onChange={(e) => set('port', e.target.value ? Number(e.target.value) : null)} />
              </Stack>
              <Stack direction="row" spacing={2}>
                <TextField label="Username" fullWidth value={form.username ?? ''}
                  onChange={(e) => set('username', e.target.value)} />
                <TextField
                  label="Password" type="password" fullWidth
                  value={form.password ?? ''}
                  onChange={(e) => set('password', e.target.value)}
                  placeholder={barrier?.has_credentials ? '•••••• (unchanged)' : ''}
                  helperText={barrier?.has_credentials
                    ? 'A password is stored. Leave blank to keep it.'
                    : 'Stored encrypted; never returned by the API.'}
                />
              </Stack>
              <Stack direction="row" spacing={2}>
                <TextField
                  label={isCameraIO ? 'Camera output port' : 'Relay / door channel'}
                  type="number" fullWidth value={form.relay_channel ?? ''}
                  onChange={(e) => set('relay_channel', e.target.value ? Number(e.target.value) : null)}
                />
                <TextField
                  label="Open pulse (ms)" type="number" fullWidth value={form.pulse_ms ?? 1000}
                  onChange={(e) => set('pulse_ms', Number(e.target.value))}
                  helperText="How long the contact is held closed"
                />
              </Stack>
            </>
          )}

          <FormControlLabel
            control={
              <Switch checked={form.auto_open_enabled ?? true}
                onChange={(e) => set('auto_open_enabled', e.target.checked)} />
            }
            label="Open automatically for permitted vehicles (ANPR decision engine)"
          />
        </Stack>
      </DialogContent>
      <DialogActions>
        {barrier && (
          <Button color="error" onClick={() => onDeactivate(barrier.id)} sx={{ mr: 'auto' }}>
            Deactivate
          </Button>
        )}
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!form.name.trim() || saveMut.isPending}
          onClick={() => saveMut.mutate()}>
          {barrier ? 'Save' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function CommandLogDialog({ barrier, onClose }: { barrier: Barrier | null; onClose: () => void }) {
  const { data: commands = [], isLoading } = useQuery({
    queryKey: ['barrier-commands', barrier?.id],
    queryFn: () => listBarrierCommands(barrier!.id),
    enabled: !!barrier,
  })

  return (
    <Dialog open={!!barrier} onClose={onClose} maxWidth="md" fullWidth>
      <DialogTitle>
        Command History — {barrier?.name}
        <IconButton onClick={onClose} sx={{ position: 'absolute', right: 8, top: 8 }}>
          <CloseIcon />
        </IconButton>
      </DialogTitle>
      <DialogContent>
        {isLoading && <Skeleton variant="rounded" height={120} />}
        {!isLoading && commands.length === 0 && (
          <Typography color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>
            No commands recorded yet.
          </Typography>
        )}
        <Stack divider={<Divider />}>
          {commands.map((c) => (
            <Box key={c.id} sx={{ py: 1.25 }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', flexWrap: 'wrap' }}>
                <Chip size="small" label={COMMAND_LABELS[c.command as BarrierCommand] ?? c.command} />
                <Chip
                  size="small"
                  label={c.source === 'decision_engine' ? 'Automatic' : c.source}
                  sx={{ bgcolor: c.source === 'decision_engine' ? '#6C63FF22' : undefined }}
                />
                <Chip
                  size="small"
                  label={c.succeeded ? 'Succeeded' : 'Failed'}
                  sx={{
                    bgcolor: c.succeeded ? '#00E39622' : '#FF456022',
                    color: c.succeeded ? '#00E396' : '#FF4560',
                  }}
                />
                {c.plate_number && <Chip size="small" variant="outlined" label={c.plate_number} />}
                <Typography variant="caption" color="text.secondary" sx={{ ml: 'auto' }}>
                  {new Date(c.created_at).toLocaleString()}
                  {c.latency_ms != null ? ` · ${c.latency_ms}ms` : ''}
                </Typography>
              </Stack>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                {c.issued_by_name ? `${c.issued_by_name} — ` : ''}
                {c.reason || c.decision || '—'}
                {c.error ? ` · ${c.error}` : ''}
              </Typography>
            </Box>
          ))}
        </Stack>
      </DialogContent>
    </Dialog>
  )
}

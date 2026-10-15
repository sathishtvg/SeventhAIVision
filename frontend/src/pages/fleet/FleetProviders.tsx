/**
 * Where the fleet's positions come from, and when a vehicle counts as stale or
 * offline.
 *
 * The platform's Traccar is set up on the server (docs/FLEET_GPS_PROVIDERS.md);
 * this page says whether it is. An organisation may also connect its own
 * Traccar: its address and sign-in are kept encrypted on the server and never
 * come back to this page, and the secret its Traccar sends with each position
 * is shown once, when it is made or replaced.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle, IconButton, MenuItem, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import KeyIcon from '@mui/icons-material/KeyOutlined'
import RetireIcon from '@mui/icons-material/DoNotDisturbOnOutlined'
import TestIcon from '@mui/icons-material/NetworkCheck'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { SkeletonRows } from '@/components/common/SkeletonRows'
import Stack from '@/components/common/Stack'
import { FleetNav } from '@/components/fleet/FleetNav'
import { EmptyState, ErrorState, SaveStatus, TableEmptyRow, TableErrorRow } from '@/components/states'
import {
  createProvider, getFleetLimits, getFleetOptions, getFleetRetention, listProviders, replaceForwardSecret, retireProvider,
  setFleetLimits, setFleetRetention, testProvider, type FleetLimits, type RetentionKind,
} from '@/api/fleet'

const COLS = 5

export default function FleetProviders() {
  const { data: options } = useQuery({ queryKey: ['fleet-options'], queryFn: getFleetOptions })
  const mayDevices = !!options?.may.manage_devices
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="GPS providers" subtitle="Where the fleet's positions come from, and when a vehicle counts as stale or offline" />
      <FleetNav />
      <Box sx={{ display: 'grid', gap: 2 }}>
        <Limits mayChange={!!options?.may.manage_limits} />
        <Retention />
        {options && !mayDevices && (
          <Alert severity="info">Providers are set up by whoever manages the fleet's devices (Admin or Manager).</Alert>
        )}
        {mayDevices && <Providers />}
      </Box>
    </Box>
  )
}

function Limits({ mayChange }: { mayChange: boolean }) {
  const qc = useQueryClient()
  const { data, isLoading, isLoadingError: limitsFailed, error: limitsError, refetch } = useQuery({
    queryKey: ['fleet-limits'], queryFn: getFleetLimits,
  })
  const [draft, setDraft] = useState<FleetLimits | null>(null)
  const shown = draft ?? data
  const save = useMutation({
    mutationFn: () => setFleetLimits(shown!),
    onSuccess: () => { setDraft(null); qc.invalidateQueries({ queryKey: ['fleet-limits'] }); qc.invalidateQueries({ queryKey: ['fleet-vehicles'] }) },
  })
  const set = (key: keyof FleetLimits) => (e: { target: { value: string } }) =>
    setDraft({ ...(shown as FleetLimits), [key]: Number(e.target.value) })
  return (
    <GlassCard sx={{ p: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>When a vehicle counts as stale or offline</Typography>
      {isLoading ? null : limitsFailed || !shown ? (
        <ErrorState compact title="The limits could not be loaded" error={limitsError} onRetry={refetch} />
      ) : (
        <Stack direction="row" sx={{ gap: 2, flexWrap: 'wrap', alignItems: 'center' }}>
          <TextField size="small" type="number" label="Online for (minutes)" value={shown.online_minutes} onChange={set('online_minutes')}
                     disabled={!mayChange} sx={{ width: 190 }} helperText="After the last fix" />
          <TextField size="small" type="number" label="Offline after (minutes)" value={shown.offline_minutes} onChange={set('offline_minutes')}
                     disabled={!mayChange} sx={{ width: 190 }} helperText="Stale in between" />
          {mayChange && (
            <Button variant="contained" disabled={!draft || save.isPending} onClick={() => save.mutate()}>Save</Button>
          )}
          <SaveStatus state={save.isPending ? 'saving' : save.isError ? 'failed' : save.isSuccess && !draft ? 'saved' : 'idle'} error={save.error} />
        </Stack>
      )}
    </GlassCard>
  )
}

function Providers() {
  const qc = useQueryClient()
  const [making, setMaking] = useState(false)
  const [secret, setSecret] = useState<{ name: string; secret: string; path: string } | null>(null)
  const [tested, setTested] = useState<Record<string, { ok: boolean; message: string }>>({})
  const { data, isLoading, isLoadingError: providersFailed, error: providersError, refetch } = useQuery({
    queryKey: ['fleet-providers'], queryFn: listProviders,
  })
  const again = () => qc.invalidateQueries({ queryKey: ['fleet-providers'] })
  const test = useMutation({
    mutationFn: (id: string) => testProvider(id),
    onSuccess: (r, id) => { setTested((t) => ({ ...t, [id]: r })); again() },
  })
  const retire = useMutation({ mutationFn: (id: string) => retireProvider(id), onSuccess: again })
  const replace = useMutation({
    mutationFn: ({ id }: { id: string; name: string }) => replaceForwardSecret(id),
    onSuccess: (r, v) => setSecret({ name: v.name, secret: r.forward_secret, path: r.forward_path }),
  })
  const origin = window.location.origin
  return (
    <>
      <GlassCard sx={{ p: 2 }}>
        <Typography variant="subtitle2" sx={{ mb: 1 }}>The platform's Traccar</Typography>
        {isLoading ? null : !data ? null : data.builtin.available && data.builtin.forwarding ? (
          <Typography variant="body2">
            Set up on this server. Vehicles report through it unless they are given one of the organisation's own
            connections below. It forwards positions to <code>{data.builtin.forward_path}</code>.
          </Typography>
        ) : (
          <EmptyState compact title="Not set up on this server"
                      hint="Whoever runs the server starts it and sets TRACCAR_FORWARD_SECRET (docs/FLEET_GPS_PROVIDERS.md). Until then vehicles can only report through the organisation's own Traccar." />
        )}
      </GlassCard>
      <GlassCard sx={{ p: 0 }}>
        <Stack direction="row" sx={{ p: 2, pb: 1, gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
          <Typography variant="subtitle2" sx={{ flex: 1, minWidth: 220 }}>The organisation's own Traccar</Typography>
          <Button variant="outlined" startIcon={<AddIcon />} onClick={() => setMaking(true)}>Connect a Traccar</Button>
        </Stack>
        <SaveStatus state={test.isPending || retire.isPending || replace.isPending ? 'saving' : test.isError || retire.isError || replace.isError ? 'failed' : 'idle'}
                    error={test.error ?? retire.error ?? replace.error} savingText="Working…" />
        <TableContainer>
          <Table size="small" aria-label="Provider connections">
            <TableHead><TableRow><TableCell>Name</TableCell><TableCell>Address</TableCell><TableCell>Last test</TableCell><TableCell>Status</TableCell><TableCell align="right">Actions</TableCell></TableRow></TableHead>
            <TableBody>
              {isLoading ? (
                <SkeletonRows cols={COLS} rows={2} />
              ) : providersFailed ? (
                <TableErrorRow cols={COLS} title="The connections could not be loaded" error={providersError} onRetry={refetch} />
              ) : !data?.items.length ? (
                <TableEmptyRow cols={COLS} title="None connected" hint="Connect one only if the organisation runs its own Traccar." />
              ) : data.items.map((p) => (
                <TableRow key={p.id} sx={{ opacity: p.is_active ? 1 : 0.6 }}>
                  <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{p.name}</Typography>
                    <Typography variant="caption" color="text.secondary">{p.auth_kind === 'token' ? 'Token sign-in' : `Signs in as ${p.username}`}</Typography></TableCell>
                  <TableCell><code>{p.base_url}</code></TableCell>
                  <TableCell>
                    {tested[p.id]?.message ?? p.last_test_message ?? 'Not tested'}
                    {p.last_test_at && <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>{new Date(p.last_test_at).toLocaleString()}</Typography>}
                  </TableCell>
                  <TableCell>
                    {!p.is_active ? <Chip size="small" label="Retired" /> : p.last_test_ok === false
                      ? <Chip size="small" color="warning" label="Last test failed" />
                      : <Chip size="small" color="success" variant="outlined" label="In use" />}
                  </TableCell>
                  <TableCell align="right">
                    {p.is_active && (
                      <>
                        <Tooltip title="Test the connection"><IconButton size="small" aria-label={`Test ${p.name}`} onClick={() => test.mutate(p.id)}><TestIcon fontSize="small" /></IconButton></Tooltip>
                        <Tooltip title="New forwarding secret (the old one stops working)"><IconButton size="small" aria-label={`New forwarding secret for ${p.name}`} onClick={() => replace.mutate({ id: p.id, name: p.name })}><KeyIcon fontSize="small" /></IconButton></Tooltip>
                        <Tooltip title="Retire: stop accepting its positions"><IconButton size="small" aria-label={`Retire ${p.name}`} onClick={() => retire.mutate(p.id)}><RetireIcon fontSize="small" /></IconButton></Tooltip>
                      </>
                    )}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>
      {making && (
        <ConnectDialog onClose={() => setMaking(false)}
                       onMade={(made) => { setMaking(false); again(); setSecret(made) }} />
      )}
      {secret && (
        <Dialog open onClose={() => setSecret(null)} maxWidth="sm" fullWidth aria-labelledby="secret-title">
          <DialogTitle id="secret-title">Forwarding for {secret.name}</DialogTitle>
          <DialogContent>
            <Alert severity="warning" sx={{ mb: 2 }}>This secret is shown once. Put it in this Traccar's settings now.</Alert>
            <Typography variant="body2" sx={{ mb: 1 }}>In that Traccar's configuration:</Typography>
            <Box component="pre" sx={{ p: 1.5, borderRadius: 1, bgcolor: 'action.hover', overflowX: 'auto', fontSize: 13, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
              {`forward.type = json\nforward.url = ${origin}${secret.path}\nforward.header = X-Fleet-Secret: ${secret.secret}\nforward.retry.enable = true`}
            </Box>
          </DialogContent>
          <DialogActions><Button variant="contained" onClick={() => setSecret(null)}>I have put it in</Button></DialogActions>
        </Dialog>
      )}
    </>
  )
}

function ConnectDialog({ onClose, onMade }: { onClose: () => void; onMade: (m: { name: string; secret: string; path: string }) => void }) {
  const [name, setName] = useState('')
  const [url, setUrl] = useState('')
  const [kind, setKind] = useState<'token' | 'basic'>('token')
  const [username, setUsername] = useState('')
  const [secret, setSecret] = useState('')
  const make = useMutation({
    mutationFn: () => createProvider({ name: name.trim(), base_url: url.trim(), auth_kind: kind, username: kind === 'basic' ? username.trim() : null, secret }),
    onSuccess: (r) => onMade({ name: r.name, secret: r.forward_secret, path: r.forward_path }),
  })
  const ready = name.trim() && /^https?:\/\//.test(url.trim()) && secret && (kind === 'token' || username.trim())
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth aria-labelledby="connect-title">
      <DialogTitle id="connect-title">Connect the organisation's own Traccar</DialogTitle>
      <DialogContent>
        <Box sx={{ display: 'grid', gap: 2, pt: 1 }}>
          <TextField size="small" label="Name" required value={name} onChange={(e) => setName(e.target.value)} />
          <TextField size="small" label="Address" required placeholder="https://traccar.example.com" value={url} onChange={(e) => setUrl(e.target.value)} />
          <TextField select size="small" label="Signs in with" value={kind} onChange={(e) => setKind(e.target.value as 'token' | 'basic')}>
            <MenuItem value="token">A token (made in Traccar under the account's settings)</MenuItem>
            <MenuItem value="basic">A user and password</MenuItem>
          </TextField>
          {kind === 'basic' && <TextField size="small" label="User" required value={username} onChange={(e) => setUsername(e.target.value)} />}
          <TextField size="small" label={kind === 'token' ? 'Token' : 'Password'} type="password" required value={secret}
                     onChange={(e) => setSecret(e.target.value)} autoComplete="off"
                     helperText="Kept encrypted on the server; it never comes back to this page" />
        </Box>
      </DialogContent>
      <DialogActions sx={{ gap: 1 }}>
        <SaveStatus state={make.isPending ? 'saving' : make.isError ? 'failed' : 'idle'} error={make.error} />
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" disabled={!ready || make.isPending} onClick={() => make.mutate()}>Connect</Button>
      </DialogActions>
    </Dialog>
  )
}

/** How long positions and archived vehicles are kept: nothing is removed until a period is set, and setting one that
 *  already has older records is asked twice - the server says how many first. */
function Retention() {
  const qc = useQueryClient()
  const { data, isLoading, isLoadingError: retentionFailed, error: retentionError, refetch } = useQuery({
    queryKey: ['fleet-retention'], queryFn: getFleetRetention,
  })
  if (isLoading) return null
  if (retentionFailed || !data) {
    return <ErrorState compact title="How long the fleet's records are kept could not be loaded" error={retentionError} onRetry={refetch} />
  }
  return (
    <GlassCard sx={{ p: 2 }}>
      <Typography variant="subtitle2" sx={{ mb: 1 }}>How long the fleet's records are kept</Typography>
      <Box sx={{ display: 'grid', gap: 2 }}>
        {data.kinds.map((k) => (
          <Period key={k.key} kind={k} mayChange={data.may_set} least={data.least_days}
                  onSaved={() => qc.invalidateQueries({ queryKey: ['fleet-retention'] })} />
        ))}
      </Box>
      {!data.may_set && (
        <Typography variant="caption" color="text.secondary">
          A period is set by somebody who may change the organisation's settings and is not held to particular sites.
        </Typography>
      )}
    </GlassCard>
  )
}

function Period({ kind, mayChange, least, onSaved }: { kind: RetentionKind; mayChange: boolean; least: number; onSaved: () => void }) {
  const [days, setDays] = useState(kind.days != null ? String(kind.days) : '')
  const [confirm, setConfirm] = useState<{ older: number; message: string } | null>(null)
  const save = useMutation({
    mutationFn: (older?: number) => setFleetRetention(kind.key, days ? Number(days) : null, older),
    onSuccess: () => { setConfirm(null); onSaved() },
    onError: (err: unknown) => {
      const e = err as { response?: { status?: number; data?: { detail?: { already_older?: number; message?: string } } } }
      const d = e.response?.data?.detail
      if (e.response?.status === 409 && d?.already_older != null) setConfirm({ older: d.already_older, message: d.message ?? '' })
    },
  })
  const asked = days ? Number(days) : null
  const valid = asked === null || (Number.isInteger(asked) && asked >= least)
  return (
    <Box>
      <Typography variant="body2" sx={{ fontWeight: 600 }}>{kind.label}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        {kind.days != null ? `Kept ${kind.days} days, counted from ${kind.counted_from}.` : 'No period is set: everything is kept.'}{' '}
        Removed: {kind.removes}.{kind.kept_whatever ? ` Kept whatever its age: ${kind.kept_whatever}.` : ''}
      </Typography>
      {mayChange && (
        <Stack direction="row" sx={{ gap: 1, mt: 1, alignItems: 'center', flexWrap: 'wrap' }}>
          <TextField size="small" type="number" label="Days" value={days} onChange={(e) => { setDays(e.target.value); setConfirm(null) }}
                     sx={{ width: 140 }} helperText={`Empty: kept. At least ${least}.`} error={!valid} />
          <Button variant="outlined" disabled={!valid || save.isPending} onClick={() => save.mutate(undefined)}>Save</Button>
          <SaveStatus state={save.isPending ? 'saving' : save.isError && !confirm ? 'failed' : save.isSuccess ? 'saved' : 'idle'} error={save.error} />
        </Stack>
      )}
      {confirm && (
        <Alert severity="warning" sx={{ mt: 1 }} action={
          <Button color="inherit" size="small" disabled={save.isPending} onClick={() => save.mutate(confirm.older)}>Set it</Button>}>
          {confirm.message}
        </Alert>
      )}
    </Box>
  )
}

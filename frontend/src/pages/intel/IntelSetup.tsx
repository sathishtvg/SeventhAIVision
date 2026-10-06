/**
 * Security Intelligence — setup. Three things an administrator says, and the
 * layer never assumes: whether it runs for this organisation at all, who may
 * decide and how far, and what each site expects (its hours, how critical it
 * is). Everyone who can read the layer can read these; changing them needs
 * the permission to manage it.
 */
import { useState } from 'react'
import {
  Alert, Box, Button, Checkbox, Chip, Dialog, DialogActions, DialogContent, DialogTitle, FormControlLabel, Grid,
  MenuItem, Skeleton, Switch, Table, TableBody, TableCell, TableHead, TableRow, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { usePermission } from '@/hooks/usePermission'
import { getSites } from '@/api/sites'
import { upsertSetting } from '@/api/settings'
import {
  apiError, deleteSiteDecisionPolicy, getDecisionPolicy, getIntelStatus, getPipeline, listSiteProfiles,
  putDecisionPolicy, putSiteDecisionPolicy, putSiteProfile,
} from '@/api/securityIntelligence'
import type { BusinessHours, DecisionPolicy, PolicyRoles, RiskLevel, SiteProfile } from '@/api/securityIntelligence'
import { SOURCE_LABEL, fmt, lag, pretty } from '@/components/intel/intelFormat'
import { IntelNav } from './IntelNav'

export default function IntelSetup() {
  const canManage = usePermission('intel:manage')
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title="Security Intelligence Setup"
                  subtitle="Whether it runs, who may decide, and what each site expects — said by you, never assumed" />
      <IntelNav />
      {!canManage && <Alert severity="info" sx={{ mb: 2 }}>You can read this setup. Changing it needs an administrator.</Alert>}
      <Grid container spacing={2}>
        <Grid size={{ xs: 12, lg: 5 }}><Running /></Grid>
        <Grid size={{ xs: 12, lg: 7 }}><Policy canManage={canManage} /></Grid>
        <Grid size={{ xs: 12 }}><Sites canManage={canManage} /></Grid>
        <Grid size={{ xs: 12 }}><Pace /></Grid>
      </Grid>
    </Box>
  )
}

// ── Whether it runs ──────────────────────────────────────────────────────────

const RUNNER_WORDS = {
  running: 'Running', degraded: 'Running, with a failure on its last pass', stopped: 'Not running',
  unknown: 'Could not be asked',
}

function Running() {
  const qc = useQueryClient()
  const canSwitch = usePermission('settings:write')
  const { data, isLoading } = useQuery({ queryKey: ['intel-status'], queryFn: getIntelStatus, refetchInterval: 30_000 })
  const flip = useMutation({
    mutationFn: (on: boolean) => upsertSetting('intel.enabled', on),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['intel-status'] }),
  })
  if (isLoading || !data) return <GlassCard sx={{ p: 2 }}><Skeleton height={200} /></GlassCard>
  const total = Object.values(data.last_24_hours).reduce((n, v) => n + v, 0)
  return (
    <GlassCard sx={{ p: 2, height: '100%' }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>Whether it runs</Typography>
      <FormControlLabel
        label={data.enabled ? 'On for this organisation' : 'Off for this organisation'}
        control={<Switch checked={data.enabled} disabled={!canSwitch || flip.isPending}
                         onChange={(_, v) => flip.mutate(v)} />} />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Off, nothing of this organisation's is read or assessed. On, the layer reads what the platform already
        records, assesses it and suggests. It never acts: alerts, pushes, incidents and video are unchanged either way.
      </Typography>
      {flip.error && <Alert severity="error" sx={{ mb: 1 }}>{apiError(flip.error)}</Alert>}
      <Typography variant="body2">Runner: <b>{RUNNER_WORDS[data.runner.state]}</b>
        {data.runner.last_seen_at ? ` · last heard ${fmt(data.runner.last_seen_at)}` : ''}</Typography>
      <Typography variant="body2" sx={{ mt: 1 }}>Events read in the last 24 hours: <b>{total}</b></Typography>
      <Stack direction="row" sx={{ gap: 0.5, flexWrap: 'wrap', mt: 0.5 }}>
        {Object.entries(data.last_24_hours).map(([source, n]) => (
          <Chip key={source} size="small" variant="outlined"
                label={`${SOURCE_LABEL[source as keyof typeof SOURCE_LABEL] ?? source} ${n}`} />))}
      </Stack>
      {data.sources.some((s) => s.last_error) && (
        <Alert severity="warning" sx={{ mt: 1 }}>
          Could not read: {data.sources.filter((s) => s.last_error).map((s) => `${s.source} (${s.last_error})`).join(', ')}
        </Alert>)}
    </GlassCard>
  )
}

// ── How long it takes ────────────────────────────────────────────────────────

/** Each stage of the layer, timed from what its own records carry. A stage
 *  with too few to say gives how many there were and no figure. */
function Pace() {
  const { data, error } = useQuery({ queryKey: ['intel-pipeline'], queryFn: () => getPipeline(24), refetchInterval: 60_000 })
  return (
    <GlassCard sx={{ p: 2 }} data-testid="pace-card">
      <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>How long it takes</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        Over the last 24 hours, measured from the times the layer's own records carry.
      </Typography>
      {error ? <Alert severity="error">{apiError(error)}</Alert> : !data ? <Skeleton height={160} /> : (
        <>
          <Table size="small">
            <TableHead><TableRow><TableCell>Stage</TableCell><TableCell align="right">Measured</TableCell>
              <TableCell align="right">Usually</TableCell><TableCell align="right">19 in 20 within</TableCell></TableRow></TableHead>
            <TableBody>
              {data.stages.map((s) => (
                <TableRow key={s.code} data-testid="pace-row">
                  <TableCell sx={s.code === 'IN_ALL' ? { fontWeight: 700 } : undefined}>{s.label}</TableCell>
                  <TableCell align="right">{s.measured}</TableCell>
                  <TableCell align="right">{lag(s.median_seconds)}</TableCell>
                  <TableCell align="right">{lag(s.p95_seconds)}</TableCell>
                </TableRow>))}
            </TableBody>
          </Table>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 1 }}>
            {data.note} A stage with fewer than {data.floor} measured gives no figure.
          </Typography>
        </>
      )}
    </GlassCard>
  )
}

// ── Who may decide ───────────────────────────────────────────────────────────

const ROLE_ORDER = ['2', '8', '3', '4', '5']
const PRESETS: { name: string; words: string; guard: PolicyRoles[string] }[] = [
  { name: 'A', words: 'The command centre decides; a guard does not', guard: {} },
  { name: 'B', words: 'A guard handles low and medium risk', guard: { alone: 'MEDIUM' } },
  { name: 'C', words: 'High risk needs the command centre’s approval', guard: { alone: 'MEDIUM', with_approval: 'CRITICAL' } },
]

function RolesEditor({ policy, roles, onChange, disabled }: {
  policy: DecisionPolicy; roles: PolicyRoles; onChange: (r: PolicyRoles) => void; disabled: boolean
}) {
  const set = (role: string, key: 'alone' | 'with_approval', value: string) => {
    const rule = { ...(roles[role] ?? policy.default[role] ?? {}) }
    if (value) rule[key] = value as RiskLevel
    else delete rule[key]
    // With approval must be a higher risk than alone, or it means nothing.
    if (rule.alone && rule.with_approval
        && policy.levels.indexOf(rule.with_approval) <= policy.levels.indexOf(rule.alone)) delete rule.with_approval
    onChange({ ...roles, [role]: rule })
  }
  return (
    <Table size="small">
      <TableHead><TableRow><TableCell>Role</TableCell><TableCell>May decide alone, up to</TableCell>
        <TableCell>With a second person's approval, up to</TableCell></TableRow></TableHead>
      <TableBody>
        {ROLE_ORDER.filter((r) => policy.roles[r]).map((role) => {
          const rule = roles[role] ?? policy.default[role] ?? {}
          const aloneAt = rule.alone ? policy.levels.indexOf(rule.alone) : -1
          return (
            <TableRow key={role}>
              <TableCell>{policy.roles[role]}</TableCell>
              <TableCell>
                <TextField select size="small" value={rule.alone ?? ''} disabled={disabled} sx={{ minWidth: 150 }}
                           slotProps={{ htmlInput: { 'aria-label': `${policy.roles[role]} alone` },
                                        select: { displayEmpty: true } }}
                           onChange={(e) => set(role, 'alone', e.target.value)}>
                  <MenuItem value="">Not at all</MenuItem>
                  {policy.levels.map((l) => <MenuItem key={l} value={l}>{pretty(l)}</MenuItem>)}
                </TextField>
              </TableCell>
              <TableCell>
                <TextField select size="small" value={rule.with_approval ?? ''} disabled={disabled} sx={{ minWidth: 150 }}
                           slotProps={{ htmlInput: { 'aria-label': `${policy.roles[role]} with approval` },
                                        select: { displayEmpty: true } }}
                           onChange={(e) => set(role, 'with_approval', e.target.value)}>
                  <MenuItem value="">No further</MenuItem>
                  {policy.levels.filter((_, i) => i > aloneAt).map((l) => <MenuItem key={l} value={l}>{pretty(l)}</MenuItem>)}
                </TextField>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

function Policy({ canManage }: { canManage: boolean }) {
  const qc = useQueryClient()
  const { data: policy, isLoading } = useQuery({ queryKey: ['intel-policy'], queryFn: getDecisionPolicy })
  const { data: sites } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(true) })
  const [draft, setDraft] = useState<PolicyRoles | null>(null)
  const [siteEdit, setSiteEdit] = useState<{ siteId: string; roles: PolicyRoles } | null>(null)
  const done = (p: DecisionPolicy) => { qc.setQueryData(['intel-policy'], p); setDraft(null); setSiteEdit(null) }
  const save = useMutation({ mutationFn: (roles: PolicyRoles) => putDecisionPolicy(roles), onSuccess: done })
  const saveSite = useMutation({
    mutationFn: (v: { siteId: string; roles: PolicyRoles }) => putSiteDecisionPolicy(v.siteId, v.roles), onSuccess: done })
  const removeSite = useMutation({ mutationFn: (siteId: string) => deleteSiteDecisionPolicy(siteId), onSuccess: done })
  if (isLoading || !policy) return <GlassCard sx={{ p: 2 }}><Skeleton height={300} /></GlassCard>
  const roles = draft ?? policy.tenant?.roles ?? {}
  const withoutOwn = (sites ?? []).filter((s) => !policy.sites.some((p) => p.site_id === s.id))
  return (
    <GlassCard sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>Who may decide</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        By the risk the layer assessed. A role also needs the permission to decide, and the platform's own permission
        for whatever the decision sets off: this only ever narrows. Asking the command centre for help is always open.
        {!policy.tenant && ' Nothing has been set, so the default applies: the command centre decides, a guard does not.'}
      </Typography>
      <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', mb: 1 }}>
        {PRESETS.map((p) => (
          <Button key={p.name} size="small" variant="outlined" disabled={!canManage}
                  onClick={() => setDraft({ ...roles, 5: p.guard })}>{p.name}: {p.words}</Button>))}
      </Stack>
      <RolesEditor policy={policy} roles={roles} onChange={setDraft} disabled={!canManage} />
      {save.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(save.error)}</Alert>}
      {canManage && (
        <Stack direction="row" sx={{ gap: 1, mt: 1 }}>
          <Button variant="contained" size="small" disabled={!draft || save.isPending} onClick={() => save.mutate(roles)}>
            {save.isPending ? 'Saving…' : 'Save the policy'}</Button>
          {draft && <Button size="small" onClick={() => setDraft(null)}>Discard</Button>}
        </Stack>)}

      <Typography variant="subtitle2" sx={{ fontWeight: 600, mt: 2, mb: 0.5 }}>Sites with their own policy</Typography>
      {!policy.sites.length
        ? <Typography variant="body2" color="text.secondary">None: every site follows the policy above.</Typography>
        : policy.sites.map((s) => (
          <Stack key={s.site_id} direction="row" sx={{ alignItems: 'center', gap: 1, py: 0.5 }}>
            <Typography variant="body2" sx={{ flex: 1 }}>{s.site_name}</Typography>
            {canManage && <Button size="small" onClick={() => setSiteEdit({ siteId: s.site_id, roles: s.roles })}>Change</Button>}
            {canManage && <Button size="small" color="error" disabled={removeSite.isPending}
                                  onClick={() => removeSite.mutate(s.site_id)}>Remove</Button>}
          </Stack>))}
      {removeSite.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(removeSite.error)}</Alert>}
      {canManage && !!withoutOwn.length && (
        <TextField select size="small" label="Give a site its own policy" value="" sx={{ minWidth: 260, mt: 1 }}
                   onChange={(e) => setSiteEdit({ siteId: e.target.value, roles: policy.tenant?.roles ?? {} })}>
          {withoutOwn.map((s) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
        </TextField>)}
      {siteEdit && (
        <Dialog open onClose={() => setSiteEdit(null)} maxWidth="md" fullWidth>
          <DialogTitle>
            Decision policy for {(sites ?? []).find((s) => s.id === siteEdit.siteId)?.name ?? 'this site'}</DialogTitle>
          <DialogContent>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
              It stands in place of the organisation's policy at this site, as a whole.</Typography>
            <RolesEditor policy={policy} roles={siteEdit.roles} disabled={false}
                         onChange={(r) => setSiteEdit({ ...siteEdit, roles: r })} />
            {saveSite.error && <Alert severity="error" sx={{ mt: 1 }}>{apiError(saveSite.error)}</Alert>}
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setSiteEdit(null)}>Cancel</Button>
            <Button variant="contained" disabled={saveSite.isPending} onClick={() => saveSite.mutate(siteEdit)}>Save</Button>
          </DialogActions>
        </Dialog>)}
    </GlassCard>
  )
}

// ── What each site expects ───────────────────────────────────────────────────

const DAYS: [string, string][] = [['mon', 'Monday'], ['tue', 'Tuesday'], ['wed', 'Wednesday'], ['thu', 'Thursday'],
                                  ['fri', 'Friday'], ['sat', 'Saturday'], ['sun', 'Sunday']]
const CRITICALITY = ['low', 'medium', 'high', 'critical']

function hoursText(hours: BusinessHours | null): string {
  if (hours == null) return 'Not defined'
  const open = DAYS.filter(([d]) => hours[d]?.length)
  if (!open.length) return 'Closed every day'
  return open.map(([d, name]) => `${name.slice(0, 3)} ${hours[d].map((p) => p.join('–')).join(', ')}`).join(' · ')
}

function Sites({ canManage }: { canManage: boolean }) {
  const { data, isLoading, error } = useQuery({ queryKey: ['intel-site-profiles'], queryFn: listSiteProfiles })
  const [editing, setEditing] = useState<SiteProfile | null>(null)
  return (
    <GlassCard sx={{ p: 2 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 0.5 }}>What each site expects</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
        Until a site's hours are defined, the layer never says "after hours" there; until its criticality is set, it
        adds nothing for it. What is not known lowers the risk confidence instead of being guessed.
      </Typography>
      {error ? <Alert severity="error">{apiError(error)}</Alert> : isLoading ? <Skeleton height={160} /> : (
        <Table size="small">
          <TableHead><TableRow><TableCell>Site</TableCell><TableCell>Criticality</TableCell><TableCell>Business hours</TableCell>
            <TableCell>Public holidays</TableCell><TableCell /></TableRow></TableHead>
          <TableBody>
            {(data ?? []).map((s) => (
              <TableRow key={s.site_id}>
                <TableCell>{s.site_name}</TableCell>
                <TableCell>{s.criticality ? pretty(s.criticality)
                  : <Typography variant="body2" color="text.secondary">Not set</Typography>}</TableCell>
                <TableCell><Typography variant="body2" color={s.business_hours == null ? 'text.secondary' : 'inherit'}>
                  {hoursText(s.business_hours)}</Typography></TableCell>
                <TableCell>{s.business_hours == null ? '—' : s.closed_on_public_holidays ? 'Closed' : 'Open as usual'}</TableCell>
                <TableCell align="right">
                  {canManage && <Button size="small" onClick={() => setEditing(s)}>Describe</Button>}</TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      )}
      {editing && <SiteDialog site={editing} onClose={() => setEditing(null)} />}
    </GlassCard>
  )
}

function SiteDialog({ site, onClose }: { site: SiteProfile; onClose: () => void }) {
  const qc = useQueryClient()
  const [criticality, setCriticality] = useState(site.criticality ?? '')
  const [defined, setDefined] = useState(site.business_hours != null)
  const [hours, setHours] = useState<BusinessHours>(site.business_hours ?? {})
  const [holidays, setHolidays] = useState(site.closed_on_public_holidays)
  const save = useMutation({
    mutationFn: () => putSiteProfile(site.site_id, {
      timezone: site.timezone, notes: site.notes, criticality: criticality || null,
      business_hours: defined ? hours : null, closed_on_public_holidays: holidays }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['intel-site-profiles'] }); onClose() },
  })
  const period = (day: string): [string, string] => hours[day]?.[0] ?? ['08:00', '18:00']
  const setDay = (day: string, open: boolean, from?: string, to?: string) => {
    const next = { ...hours }
    if (!open) delete next[day]
    else next[day] = [[from ?? period(day)[0], to ?? period(day)[1]], ...(hours[day]?.slice(1) ?? [])]
    setHours(next)
  }
  return (
    <Dialog open onClose={save.isPending ? undefined : onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{site.site_name}</DialogTitle>
      <DialogContent>
        <TextField select fullWidth size="small" label="Criticality" value={criticality} sx={{ mt: 1, mb: 2 }}
                   onChange={(e) => setCriticality(e.target.value)}
                   helperText="How much an event here matters. Left unset, the layer adds nothing for it.">
          <MenuItem value="">Not set</MenuItem>
          {CRITICALITY.map((c) => <MenuItem key={c} value={c}>{pretty(c)}</MenuItem>)}
        </TextField>
        <FormControlLabel label="Business hours are defined for this site"
                          control={<Switch checked={defined} onChange={(_, v) => setDefined(v)} />} />
        {!defined ? (
          <Typography variant="body2" color="text.secondary">
            Not defined: the layer will not say "after hours" for this site.</Typography>
        ) : (
          <>
            {DAYS.map(([day, name]) => {
              const open = !!hours[day]?.length
              return (
                <Stack key={day} direction="row" sx={{ alignItems: 'center', gap: 1, py: 0.25 }}>
                  <FormControlLabel sx={{ width: 150, m: 0 }} label={name}
                                    control={<Checkbox size="small" checked={open}
                                                       onChange={(_, v) => setDay(day, v)} />} />
                  {open ? (
                    <>
                      <TextField type="time" size="small" value={period(day)[0]}
                                 slotProps={{ htmlInput: { 'aria-label': `${name} opens` } }}
                                 onChange={(e) => setDay(day, true, e.target.value, undefined)} />
                      <Typography variant="body2">to</Typography>
                      <TextField type="time" size="small" value={period(day)[1]}
                                 slotProps={{ htmlInput: { 'aria-label': `${name} closes` } }}
                                 onChange={(e) => setDay(day, true, undefined, e.target.value)} />
                      {(hours[day]?.length ?? 0) > 1 && (
                        <Typography variant="caption" color="text.secondary">
                          +{hours[day].length - 1} more period(s), kept</Typography>)}
                    </>
                  ) : <Typography variant="body2" color="text.secondary">Closed</Typography>}
                </Stack>
              )
            })}
            <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
              A closing time earlier than the opening time runs past midnight.</Typography>
            <FormControlLabel sx={{ mt: 1 }} label="Closed on public holidays"
                              control={<Switch checked={holidays} onChange={(_, v) => setHolidays(v)} />} />
          </>
        )}
        {save.error && <Alert severity="error" sx={{ mt: 2 }}>{apiError(save.error)}</Alert>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={save.isPending}>Cancel</Button>
        <Button variant="contained" disabled={save.isPending} onClick={() => save.mutate()}>
          {save.isPending ? 'Saving…' : 'Save'}</Button>
      </DialogActions>
    </Dialog>
  )
}

/**
 * Plans and the module price list — what Seventh AI sells and for how much.
 *
 * The two are separate on purpose. A plan is a package with allowances and
 * unit rates; a module is a thing that can be switched on, priced by how it is
 * sold. Changing a module's list price here moves every plan that has not
 * deliberately negotiated something else, which is why overrides are the
 * exception rather than a copy of the price on every row.
 *
 * The allowance columns are the part worth reading carefully: a plan including
 * fifty cameras and charging five dollars above that bills sixty cameras as
 * ten. Getting that backwards is the oldest billing complaint there is, so the
 * table shows the allowance and the rate side by side rather than burying
 * either in a dialog.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, InputLabel, MenuItem, Select, Skeleton, Switch, Tab, Table,
  TableBody, TableCell, TableContainer, TableHead, TableRow, Tabs, TextField,
  Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/Edit'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import {
  createBillingPlan, getBillingModules, getBillingPlans, updateBillingModule,
  updateBillingPlan, type BillingModule, type BillingPlan,
} from '@/api/platformBilling'

const BILLING_TYPE_LABEL: Record<string, string> = {
  included: 'Included',
  flat: 'Flat / cycle',
  per_camera: 'Per camera',
  per_site: 'Per site',
  per_user: 'Per user',
}

const CYCLES = ['monthly', 'quarterly', 'half_yearly', 'yearly', 'custom'] as const

function money(v: string | number | null) {
  if (v === null || v === undefined) return '—'
  return Number(v).toLocaleString(undefined,
    { style: 'currency', currency: 'USD', minimumFractionDigits: 2 })
}

const EMPTY_PLAN = {
  name: '', billing_cycle: 'monthly', price_monthly: '0',
  included_cameras: 0, included_sites: 0, included_users: 0,
  included_storage_gb: 0,
  price_per_camera: '0', price_per_site: '0', price_per_user: '0',
  price_per_gb: '0', support_level: 'standard', is_active: true,
}

export default function PlatformBilling() {
  const queryClient = useQueryClient()
  const [tab, setTab] = useState(0)
  const [planOpen, setPlanOpen] = useState(false)
  const [editing, setEditing] = useState<BillingPlan | null>(null)
  const [form, setForm] = useState<Record<string, unknown>>({ ...EMPTY_PLAN })
  const [moduleEdit, setModuleEdit] = useState<BillingModule | null>(null)
  const [error, setError] = useState('')

  const { data: plans, isLoading: plansLoading } = useQuery({
    queryKey: ['platform-plans'], queryFn: getBillingPlans,
  })
  const { data: modules, isLoading: modulesLoading } = useQuery({
    queryKey: ['platform-modules'], queryFn: getBillingModules,
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['platform-plans'] })
    queryClient.invalidateQueries({ queryKey: ['platform-modules'] })
  }

  const savePlan = useMutation({
    mutationFn: () => editing
      ? updateBillingPlan(editing.id, form)
      : createBillingPlan(form),
    onSuccess: () => { setPlanOpen(false); setEditing(null); setError(''); invalidate() },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not save the plan'),
  })

  const saveModule = useMutation({
    mutationFn: (m: BillingModule) => updateBillingModule(m.code, {
      billing_type: m.billing_type, unit_price: m.unit_price, is_active: m.is_active,
    }),
    onSuccess: () => { setModuleEdit(null); invalidate() },
  })

  function openPlan(plan: BillingPlan | null) {
    setEditing(plan)
    setError('')
    setForm(plan
      ? Object.fromEntries(Object.entries(plan).filter(([k]) =>
          k in EMPTY_PLAN || ['max_cameras', 'max_sites', 'max_users',
                              'description', 'price_yearly'].includes(k)))
      : { ...EMPTY_PLAN })
    setPlanOpen(true)
  }

  const field = (key: string, label: string, type = 'text') => (
    <TextField
      key={key} label={label} size="small" type={type}
      value={(form[key] ?? '') as string | number}
      onChange={(e) => setForm({ ...form, [key]: e.target.value })}
    />
  )

  return (
    <Box>
      <PageHeader
        title="Plans & Pricing"
        subtitle="What Seventh AI sells, and what each part of it costs"
        action={
          <Button
            variant="contained" size="small" startIcon={<AddIcon />}
            onClick={() => openPlan(null)}
          >
            New plan
          </Button>
        }
      />

      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Plans" />
        <Tab label="Module price list" />
      </Tabs>

      {tab === 0 && (
        <GlassCard sx={{ p: 0 }}>
          {plansLoading ? <Skeleton variant="rectangular" height={200} /> : (
            <TableContainer sx={{ overflowX: 'auto' }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Plan</TableCell>
                    <TableCell>Cycle</TableCell>
                    <TableCell align="right">Base</TableCell>
                    <TableCell align="right">Cameras</TableCell>
                    <TableCell align="right">Sites</TableCell>
                    <TableCell align="right">Users</TableCell>
                    <TableCell align="right">Subscribers</TableCell>
                    <TableCell align="right" />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {!plans?.length ? (
                    <TableRow><TableCell colSpan={8}>
                      <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
                        No plans yet.
                      </Typography>
                    </TableCell></TableRow>
                  ) : plans.map((p) => (
                    <TableRow key={p.id} hover>
                      <TableCell>
                        <Typography variant="body2" sx={{ fontWeight: 600 }}>
                          {p.name}
                        </Typography>
                        {!p.is_active && (
                          <Chip label="inactive" size="small" variant="outlined"
                                sx={{ height: 17, fontSize: '0.58rem' }} />
                        )}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption">{p.billing_cycle}</Typography>
                      </TableCell>
                      <TableCell align="right">{money(p.price_monthly)}</TableCell>
                      {/* Allowance and rate together: "50 incl, then $5" is the
                          whole commercial shape in one cell, and splitting them
                          is how people misread the bill. */}
                      <TableCell align="right">
                        <Tooltip title="Included, then the rate above it">
                          <Typography variant="caption">
                            {p.included_cameras} incl, then {money(p.price_per_camera)}
                          </Typography>
                        </Tooltip>
                      </TableCell>
                      <TableCell align="right">
                        <Typography variant="caption">
                          {p.included_sites} incl, then {money(p.price_per_site)}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Typography variant="caption">
                          {p.included_users} incl, then {money(p.price_per_user)}
                        </Typography>
                      </TableCell>
                      <TableCell align="right">{p.subscribers}</TableCell>
                      <TableCell align="right">
                        <Button size="small" startIcon={<EditIcon />}
                                onClick={() => openPlan(p)}>
                          Edit
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </GlassCard>
      )}

      {tab === 1 && (
        <GlassCard sx={{ p: 0 }}>
          <Alert severity="info" sx={{ m: 2 }}>
            Changing a price here moves every plan that has not negotiated
            something else. Invoices already issued are frozen and are not
            affected.
          </Alert>
          {modulesLoading ? <Skeleton variant="rectangular" height={220} /> : (
            <TableContainer sx={{ overflowX: 'auto' }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Module</TableCell>
                    <TableCell>Code</TableCell>
                    <TableCell>Charged</TableCell>
                    <TableCell align="right">Price</TableCell>
                    <TableCell align="right">Active</TableCell>
                    <TableCell align="right" />
                  </TableRow>
                </TableHead>
                <TableBody>
                  {(modules ?? []).map((m) => (
                    <TableRow key={m.code} hover>
                      <TableCell>{m.name}</TableCell>
                      <TableCell>
                        <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                          {m.code}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Chip
                          label={BILLING_TYPE_LABEL[m.billing_type] ?? m.billing_type}
                          size="small" variant="outlined"
                          sx={{ height: 19, fontSize: '0.62rem' }}
                        />
                      </TableCell>
                      <TableCell align="right">
                        {m.billing_type === 'included' ? '—' : money(m.unit_price)}
                      </TableCell>
                      <TableCell align="right">
                        <Switch
                          size="small" checked={m.is_active}
                          onChange={(e) =>
                            saveModule.mutate({ ...m, is_active: e.target.checked })}
                        />
                      </TableCell>
                      <TableCell align="right">
                        <Button size="small" onClick={() => setModuleEdit(m)}>
                          Price
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </GlassCard>
      )}

      {/* ── Plan editor ─────────────────────────────────────────────────── */}
      <Dialog open={planOpen} onClose={() => setPlanOpen(false)} maxWidth="md" fullWidth>
        <DialogTitle>{editing ? `Edit ${editing.name}` : 'New plan'}</DialogTitle>
        <DialogContent dividers>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="warning">{error}</Alert>}
            {editing && (
              <Alert severity="info">
                Changing a plan does not re-bill anybody. Invoices already issued
                are frozen; the next one is priced from the plan as it stands then.
              </Alert>
            )}

            <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', gap: 2 }}>
              {field('name', 'Plan name')}
              <FormControl size="small" sx={{ minWidth: 160 }}>
                <InputLabel>Billing cycle</InputLabel>
                <Select
                  value={form.billing_cycle as string} label="Billing cycle"
                  onChange={(e) => setForm({ ...form, billing_cycle: e.target.value })}
                >
                  {CYCLES.map((c) => (
                    <MenuItem key={c} value={c}>{c.replace('_', ' ')}</MenuItem>
                  ))}
                </Select>
              </FormControl>
              {field('price_monthly', 'Base price', 'number')}
            </Stack>

            <Typography variant="caption" color="text.secondary">
              Included with the plan — charged only above these
            </Typography>
            <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', gap: 2 }}>
              {field('included_cameras', 'Cameras included', 'number')}
              {field('included_sites', 'Sites included', 'number')}
              {field('included_users', 'Users included', 'number')}
              {field('included_storage_gb', 'Storage GB included', 'number')}
            </Stack>

            <Typography variant="caption" color="text.secondary">
              Rates above the allowance
            </Typography>
            <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', gap: 2 }}>
              {field('price_per_camera', 'Per camera', 'number')}
              {field('price_per_site', 'Per site', 'number')}
              {field('price_per_user', 'Per user', 'number')}
              {field('price_per_gb', 'Per GB', 'number')}
            </Stack>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setPlanOpen(false)}>Cancel</Button>
          <Button
            variant="contained" disabled={!form.name || savePlan.isPending}
            onClick={() => savePlan.mutate()}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>

      {/* ── Module price ────────────────────────────────────────────────── */}
      <Dialog open={Boolean(moduleEdit)} onClose={() => setModuleEdit(null)}
              maxWidth="xs" fullWidth>
        <DialogTitle>{moduleEdit?.name}</DialogTitle>
        <DialogContent dividers>
          {moduleEdit && (
            <Stack spacing={2} sx={{ mt: 1 }}>
              <FormControl size="small" fullWidth>
                <InputLabel>How it is charged</InputLabel>
                <Select
                  value={moduleEdit.billing_type} label="How it is charged"
                  onChange={(e) => setModuleEdit({
                    ...moduleEdit,
                    billing_type: e.target.value as BillingModule['billing_type'],
                  })}
                >
                  {Object.entries(BILLING_TYPE_LABEL).map(([k, v]) => (
                    <MenuItem key={k} value={k}>{v}</MenuItem>
                  ))}
                </Select>
              </FormControl>
              <TextField
                label="Unit price" size="small" type="number" fullWidth
                value={moduleEdit.unit_price}
                disabled={moduleEdit.billing_type === 'included'}
                onChange={(e) =>
                  setModuleEdit({ ...moduleEdit, unit_price: e.target.value })}
                helperText={moduleEdit.billing_type === 'included'
                  ? 'An included module is free with the plan.'
                  : 'Applies to every plan without its own override.'}
              />
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setModuleEdit(null)}>Cancel</Button>
          <Button
            variant="contained" disabled={saveModule.isPending}
            onClick={() => moduleEdit && saveModule.mutate(moduleEdit)}
          >
            Save
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

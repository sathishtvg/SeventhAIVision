import { useState } from 'react'
import {
  Box, Typography, Chip, Button, IconButton, Tooltip,
  Dialog, DialogTitle, DialogContent, DialogActions, TextField,
  Table, TableBody, TableCell, TableHead, TableRow,
  Switch, FormControlLabel, Divider, MenuItem,
  Drawer, Stack, CircularProgress, Tabs, Tab,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import EditIcon from '@mui/icons-material/Edit'
import PersonAddIcon from '@mui/icons-material/PersonAdd'
import PowerSettingsNewIcon from '@mui/icons-material/PowerSettingsNew'
import ExtensionIcon from '@mui/icons-material/Extension'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import {
  getTenants, createTenant, updateTenant, deactivateTenant, createTenantUser,
} from '@/api/tenants'
import { getTenantLicenses, upsertLicense, revokeLicense, MODULE_LABELS, ALL_AI_MODULES } from '@/api/licenses'
import {
  getTenantProducts, assignProduct, revokeProduct, toggleProductModule,
  PRODUCT_LABELS, PLATFORM_MODULE_LABELS,
} from '@/api/platform_licenses'
import type { TenantProduct } from '@/api/platform_licenses'
import type { Tenant } from '@/types/api'
import { PageHeader } from '@/components/common/PageHeader'

const TIMEZONES = [
  'Asia/Singapore',
  'Asia/Kuala_Lumpur',
  'Asia/Manila',
  'Asia/Jakarta',
  'Asia/Bangkok',
  'Asia/Hong_Kong',
  'Asia/Shanghai',
  'Asia/Tokyo',
  'Asia/Kolkata',
  'Australia/Sydney',
  'Europe/London',
  'UTC',
]

const ROLES = [
  { id: 2, label: 'Admin' },
  { id: 3, label: 'Supervisor' },
  { id: 4, label: 'Operator' },
  { id: 5, label: 'Security Guard' },
  { id: 6, label: 'Viewer' },
]

// ──────────────────────────────────────────────────────────
// AI Modules panel (inside drawer tab 0)
// ──────────────────────────────────────────────────────────

function AiModulesPanel({ tenant }: { tenant: Tenant }) {
  const qc = useQueryClient()

  const { data: licenses = [], isLoading } = useQuery({
    queryKey: ['licenses', tenant.id],
    queryFn: () => getTenantLicenses(tenant.id),
  })

  const toggleMutation = useMutation({
    mutationFn: ({ moduleType, enabled }: { moduleType: string; enabled: boolean }) =>
      enabled
        ? upsertLicense(tenant.id, moduleType, { is_enabled: true })
        : revokeLicense(tenant.id, moduleType),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['licenses', tenant.id] })
      qc.invalidateQueries({ queryKey: ['all-licenses-summary'] })
    },
  })

  const licenseMap = Object.fromEntries((licenses as any[]).map((l) => [l.module_type, l]))
  const enabledCount = (licenses as any[]).filter((l) => l.is_enabled).length

  return (
    <Box>
      <Chip
        label={`${enabledCount} / ${ALL_AI_MODULES.length} modules active`}
        size="small"
        color={enabledCount > 0 ? 'success' : 'default'}
        sx={{ mb: 2 }}
      />

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      ) : (
        <Stack spacing={1.5}>
          {ALL_AI_MODULES.map((moduleType) => {
            const lic = licenseMap[moduleType]
            const isEnabled = lic?.is_enabled ?? false

            return (
              <Box
                key={moduleType}
                sx={{
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'space-between',
                  p: 1.5,
                  borderRadius: 1,
                  border: '1px solid',
                  borderColor: isEnabled ? 'success.dark' : 'rgba(255,255,255,0.08)',
                  bgcolor: isEnabled ? 'rgba(0,227,150,0.05)' : 'rgba(255,255,255,0.02)',
                  transition: 'all 0.2s',
                }}
              >
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>
                    {MODULE_LABELS[moduleType as keyof typeof MODULE_LABELS]}
                  </Typography>
                  {lic?.licensed_at && (
                    <Typography variant="caption" color="text.secondary">
                      {isEnabled ? `Licensed ${new Date(lic.licensed_at).toLocaleDateString()}` : 'Disabled'}
                    </Typography>
                  )}
                </Box>
                <FormControlLabel
                  control={
                    <Switch
                      size="small"
                      checked={isEnabled}
                      onChange={(e) => toggleMutation.mutate({ moduleType, enabled: e.target.checked })}
                      disabled={toggleMutation.isPending}
                      color="success"
                    />
                  }
                  label=""
                  sx={{ m: 0 }}
                />
              </Box>
            )
          })}
        </Stack>
      )}
    </Box>
  )
}

// ──────────────────────────────────────────────────────────
// Platform Products panel (inside drawer tab 1)
// ──────────────────────────────────────────────────────────

function PlatformProductsPanel({ tenant }: { tenant: Tenant }) {
  const qc = useQueryClient()

  const { data: products = [], isLoading } = useQuery({
    queryKey: ['tenant-products', tenant.id],
    queryFn: () => getTenantProducts(tenant.id),
  })

  const assignMutation = useMutation({
    mutationFn: ({ productId, isEnabled }: { productId: string; isEnabled: boolean }) =>
      assignProduct(tenant.id, productId, isEnabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tenant-products', tenant.id] }),
  })

  const revokeMutation = useMutation({
    mutationFn: (productId: string) => revokeProduct(tenant.id, productId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tenant-products', tenant.id] }),
  })

  const moduleMutation = useMutation({
    mutationFn: ({
      productId,
      moduleCode,
      isEnabled,
    }: {
      productId: string
      moduleCode: string
      isEnabled: boolean
    }) => toggleProductModule(tenant.id, productId, moduleCode, isEnabled),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tenant-products', tenant.id] }),
  })

  const handleProductToggle = (product: TenantProduct, checked: boolean) => {
    if (checked) {
      assignMutation.mutate({ productId: product.product_id, isEnabled: true })
    } else {
      revokeMutation.mutate(product.product_id)
    }
  }

  const licensedCount = (products as TenantProduct[]).filter((p) => p.is_licensed).length

  return (
    <Box>
      <Chip
        label={`${licensedCount} / ${(products as TenantProduct[]).length} products licensed`}
        size="small"
        color={licensedCount > 0 ? 'primary' : 'default'}
        sx={{ mb: 2 }}
      />

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
          <CircularProgress size={32} />
        </Box>
      ) : (
        <Stack spacing={2}>
          {(products as TenantProduct[]).map((product) => {
            const isPending = assignMutation.isPending || revokeMutation.isPending

            return (
              <Box
                key={product.product_id}
                sx={{
                  border: '1px solid',
                  borderColor: product.is_licensed ? 'primary.dark' : 'rgba(255,255,255,0.08)',
                  borderRadius: 1,
                  p: 2,
                  bgcolor: product.is_licensed
                    ? 'rgba(108,99,255,0.05)'
                    : 'rgba(255,255,255,0.02)',
                  transition: 'all 0.2s',
                }}
              >
                {/* Product header */}
                <Box sx={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', mb: 1.5 }}>
                  <Box>
                    <Typography variant="body2" sx={{ fontWeight: 700 }}>
                      {PRODUCT_LABELS[product.product_id] ?? product.name}
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      {product.is_licensed
                        ? `${product.modules.length} modules · Licensed`
                        : 'Not licensed'}
                    </Typography>
                  </Box>
                  <Switch
                    checked={product.is_licensed}
                    onChange={(e) => handleProductToggle(product, e.target.checked)}
                    disabled={isPending}
                    color="primary"
                    size="small"
                  />
                </Box>

                {/* Module toggles */}
                {product.modules.length > 0 && (
                  <Stack
                    spacing={0.5}
                    sx={{
                      opacity: product.is_licensed ? 1 : 0.35,
                      pointerEvents: product.is_licensed ? 'auto' : 'none',
                    }}
                  >
                    {product.modules.map((mod) => (
                      <Box
                        key={mod.module_code}
                        sx={{
                          display: 'flex',
                          justifyContent: 'space-between',
                          alignItems: 'center',
                          py: 0.5,
                          px: 1,
                          borderRadius: 0.5,
                          bgcolor: 'rgba(255,255,255,0.03)',
                        }}
                      >
                        <Typography variant="caption" color="text.secondary">
                          {PLATFORM_MODULE_LABELS[mod.module_code] ?? mod.module_code}
                        </Typography>
                        <Switch
                          size="small"
                          checked={mod.is_enabled}
                          onChange={(e) =>
                            moduleMutation.mutate({
                              productId: product.product_id,
                              moduleCode: mod.module_code,
                              isEnabled: e.target.checked,
                            })
                          }
                          disabled={moduleMutation.isPending || !product.is_licensed}
                          color="success"
                        />
                      </Box>
                    ))}
                  </Stack>
                )}
              </Box>
            )
          })}
        </Stack>
      )}
    </Box>
  )
}

// ──────────────────────────────────────────────────────────
// Unified Manage Licenses Drawer
// ──────────────────────────────────────────────────────────

interface ManageLicensesDrawerProps {
  tenant: Tenant | null
  initialTab?: number
  onClose: () => void
}

function ManageLicensesDrawer({ tenant, initialTab = 0, onClose }: ManageLicensesDrawerProps) {
  const [tab, setTab] = useState(initialTab)

  return (
    <Drawer
      anchor="right"
      open={!!tenant}
      onClose={onClose}
      slotProps={{ paper: { sx: { width: 440, p: 0, display: 'flex', flexDirection: 'column' } } }}
    >
      {tenant && (
        <>
          {/* Header */}
          <Box sx={{ px: 3, pt: 3, pb: 0 }}>
            <Typography variant="h6" sx={{ fontWeight: 700 }} gutterBottom>
              Manage Licenses
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 1.5 }}>
              {tenant.name}
            </Typography>
            <Tabs
              value={tab}
              onChange={(_, v) => setTab(v)}
              sx={{ borderBottom: 1, borderColor: 'divider', mx: -3, px: 3 }}
            >
              <Tab label="AI Modules" />
              <Tab label="Platform Products" />
            </Tabs>
          </Box>

          {/* Content */}
          <Box sx={{ flex: 1, overflowY: 'auto', px: 3, pt: 2, pb: 3 }}>
            {tab === 0 && <AiModulesPanel tenant={tenant} />}
            {tab === 1 && <PlatformProductsPanel tenant={tenant} />}
          </Box>
        </>
      )}
    </Drawer>
  )
}

// ──────────────────────────────────────────────────────────
// Create / Edit Tenant Dialog
// ──────────────────────────────────────────────────────────

interface TenantDialogProps {
  open: boolean
  onClose: () => void
  existing?: Tenant
}

function TenantDialog({ open, onClose, existing }: TenantDialogProps) {
  const qc = useQueryClient()
  const isEdit = !!existing

  const [name, setName] = useState(existing?.name ?? '')
  const [slug, setSlug] = useState(existing?.slug ?? '')
  const [subdomain, setSubdomain] = useState(existing?.subdomain ?? '')
  const [timezone, setTimezone] = useState(existing?.timezone ?? 'Asia/Singapore')
  const [brandingColor, setBrandingColor] = useState(existing?.branding?.primary_color ?? '')
  const [brandingLogo, setBrandingLogo] = useState(existing?.branding?.logo_url ?? '')
  const [isActive, setIsActive] = useState(existing?.is_active ?? true)
  const [adminEmail, setAdminEmail] = useState('')
  const [adminPassword, setAdminPassword] = useState('')
  const [adminFullName, setAdminFullName] = useState('')

  const mutation = useMutation({
    mutationFn: () => {
      const branding: Record<string, string> = {}
      if (brandingColor) branding.primary_color = brandingColor
      if (brandingLogo) branding.logo_url = brandingLogo

      return isEdit
        ? updateTenant(existing!.id, {
            name,
            slug,
            subdomain: subdomain || undefined,
            timezone,
            branding: Object.keys(branding).length ? branding : undefined,
            is_active: isActive,
          })
        : createTenant({
            name,
            slug,
            subdomain: subdomain || slug,
            timezone,
            branding: Object.keys(branding).length ? branding : undefined,
            admin_email: adminEmail || undefined,
            admin_password: adminPassword || undefined,
            admin_full_name: adminFullName || undefined,
          })
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tenants'] })
      onClose()
    },
  })

  const autoSlug = (v: string) =>
    v.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{isEdit ? 'Edit Tenant' : 'Add Tenant'}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        <TextField
          label="Organization Name"
          value={name}
          onChange={(e) => {
            setName(e.target.value)
            if (!isEdit) {
              const s = autoSlug(e.target.value)
              setSlug(s)
              setSubdomain(s)
            }
          }}
          fullWidth
          required
        />
        <TextField
          label="Slug (URL-safe identifier)"
          value={slug}
          onChange={(e) => setSlug(autoSlug(e.target.value))}
          fullWidth
          required
          helperText="Lowercase letters, numbers and hyphens only"
        />
        <TextField
          label="Subdomain"
          value={subdomain}
          onChange={(e) => setSubdomain(autoSlug(e.target.value))}
          fullWidth
          helperText="Used for tenant login URL (e.g. acme.seventh.ai)"
        />
        <TextField
          select
          label="Timezone"
          value={timezone}
          onChange={(e) => setTimezone(e.target.value)}
          fullWidth
        >
          {TIMEZONES.map((tz) => (
            <MenuItem key={tz} value={tz}>
              {tz}
            </MenuItem>
          ))}
        </TextField>

        <Divider sx={{ borderColor: 'rgba(255,255,255,0.08)' }} />
        <Typography variant="caption" color="text.secondary">
          Branding (optional)
        </Typography>
        <TextField
          label="Primary Color"
          value={brandingColor}
          onChange={(e) => setBrandingColor(e.target.value)}
          fullWidth
          placeholder="#6C63FF"
          helperText="Hex color code for tenant's brand color"
        />
        <TextField
          label="Logo URL"
          value={brandingLogo}
          onChange={(e) => setBrandingLogo(e.target.value)}
          fullWidth
          placeholder="https://example.com/logo.png"
        />

        {isEdit && (
          <FormControlLabel
            control={<Switch checked={isActive} onChange={(e) => setIsActive(e.target.checked)} />}
            label="Active"
          />
        )}

        {!isEdit && (
          <>
            <Divider sx={{ borderColor: 'rgba(255,255,255,0.08)' }} />
            <Typography variant="caption" color="text.secondary">
              Initial Admin User (optional — can be created later)
            </Typography>
            <TextField
              label="Admin Email"
              value={adminEmail}
              onChange={(e) => setAdminEmail(e.target.value)}
              fullWidth
              type="email"
            />
            <TextField
              label="Admin Password"
              value={adminPassword}
              onChange={(e) => setAdminPassword(e.target.value)}
              fullWidth
              type="password"
            />
            <TextField
              label="Admin Full Name"
              value={adminFullName}
              onChange={(e) => setAdminFullName(e.target.value)}
              fullWidth
            />
          </>
        )}

        {mutation.error && (
          <Typography color="error" variant="caption">
            {String(mutation.error)}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => mutation.mutate()}
          disabled={!name || !slug || mutation.isPending}
        >
          {isEdit ? 'Save' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ──────────────────────────────────────────────────────────
// Add User to Tenant Dialog
// ──────────────────────────────────────────────────────────

interface AddUserDialogProps {
  open: boolean
  onClose: () => void
  tenant: Tenant
}

function AddUserDialog({ open, onClose, tenant }: AddUserDialogProps) {
  const qc = useQueryClient()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [fullName, setFullName] = useState('')
  const [roleId, setRoleId] = useState(2)

  const mutation = useMutation({
    mutationFn: () =>
      createTenantUser(tenant.id, {
        email,
        password,
        role_id: roleId,
        full_name: fullName || undefined,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['tenants'] })
      onClose()
      setEmail('')
      setPassword('')
      setFullName('')
      setRoleId(2)
    },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Add User to {tenant.name}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        <TextField
          label="Email"
          value={email}
          onChange={(e) => setEmail(e.target.value)}
          fullWidth
          required
          type="email"
        />
        <TextField
          label="Password"
          value={password}
          onChange={(e) => setPassword(e.target.value)}
          fullWidth
          required
          type="password"
        />
        <TextField
          label="Full Name"
          value={fullName}
          onChange={(e) => setFullName(e.target.value)}
          fullWidth
        />
        <TextField
          select
          label="Role"
          value={roleId}
          onChange={(e) => setRoleId(Number(e.target.value))}
          fullWidth
        >
          {ROLES.map((r) => (
            <MenuItem key={r.id} value={r.id}>
              {r.label}
            </MenuItem>
          ))}
        </TextField>
        {mutation.error && (
          <Typography color="error" variant="caption">
            {String(mutation.error)}
          </Typography>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => mutation.mutate()}
          disabled={!email || !password || mutation.isPending}
        >
          Add User
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ──────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────

export default function Tenants() {
  const qc = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)
  const [editTenant, setEditTenant] = useState<Tenant | null>(null)
  const [addUserTenant, setAddUserTenant] = useState<Tenant | null>(null)
  const [licenseTenant, setLicenseTenant] = useState<Tenant | null>(null)
  const [licenseInitialTab, setLicenseInitialTab] = useState(0)

  const { data: tenants = [], isLoading } = useQuery({
    queryKey: ['tenants'],
    queryFn: getTenants,
  })

  const { data: allLicenses } = useQuery({
    queryKey: ['all-licenses-summary'],
    queryFn: async () => {
      const result: Record<string, number> = {}
      for (const t of tenants as Tenant[]) {
        try {
          const lics = await getTenantLicenses(t.id)
          result[t.id] = (lics as any[]).filter((l) => l.is_enabled).length
        } catch {
          result[t.id] = 0
        }
      }
      return result
    },
    enabled: (tenants as Tenant[]).length > 0,
  })

  const { data: allProducts } = useQuery({
    queryKey: ['all-products-summary'],
    queryFn: async () => {
      const result: Record<string, number> = {}
      for (const t of tenants as Tenant[]) {
        try {
          const prods = await getTenantProducts(t.id)
          result[t.id] = (prods as TenantProduct[]).filter((p) => p.is_licensed).length
        } catch {
          result[t.id] = 0
        }
      }
      return result
    },
    enabled: (tenants as Tenant[]).length > 0,
  })

  const deactivate = useMutation({
    mutationFn: (id: string) => deactivateTenant(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['tenants'] }),
  })

  const openLicenses = (tenant: Tenant, tab: number) => {
    setLicenseTenant(tenant)
    setLicenseInitialTab(tab)
  }

  return (
    <Box>
      <PageHeader title="Tenants" subtitle="Customer organisations on the platform, their module licensing and account status" />
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 3 }}>
        <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setAddOpen(true)}>
          Add Tenant
        </Button>
      </Box>

      <GlassCard>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Slug / Subdomain</TableCell>
              <TableCell>AI Modules</TableCell>
              <TableCell>Platform</TableCell>
              <TableCell>Status</TableCell>
              <TableCell>Created</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading ? (
              <TableRow>
                <TableCell colSpan={7}>
                  <Typography variant="caption" color="text.secondary">
                    Loading…
                  </Typography>
                </TableCell>
              </TableRow>
            ) : (tenants as Tenant[]).length === 0 ? (
              <TableRow>
                <TableCell colSpan={7}>
                  <Typography variant="caption" color="text.secondary">
                    No tenants found
                  </Typography>
                </TableCell>
              </TableRow>
            ) : (
              (tenants as Tenant[]).map((t) => (
                <TableRow key={t.id} hover>
                  <TableCell>
                    <Typography variant="body2" sx={{ fontWeight: 600 }}>
                      {t.name}
                    </Typography>
                  </TableCell>
                  <TableCell>
                    <Typography
                      variant="caption"
                      sx={{ fontFamily: 'monospace', color: 'text.secondary' }}
                    >
                      {t.slug}
                    </Typography>
                    {t.subdomain && t.subdomain !== t.slug && (
                      <Typography
                        variant="caption"
                        display="block"
                        sx={{ color: 'text.disabled', fontSize: '0.65rem' }}
                      >
                        {t.subdomain}
                      </Typography>
                    )}
                  </TableCell>
                  <TableCell>
                    <Tooltip title="Manage AI module licenses">
                      <Chip
                        label={`${allLicenses?.[t.id] ?? '?'} / ${ALL_AI_MODULES.length}`}
                        size="small"
                        color={(allLicenses?.[t.id] ?? 0) > 0 ? 'success' : 'default'}
                        variant="outlined"
                        onClick={() => openLicenses(t, 0)}
                        sx={{ cursor: 'pointer' }}
                      />
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <Tooltip title="Manage platform products">
                      <Chip
                        label={allProducts?.[t.id] !== undefined ? `${allProducts[t.id]} licensed` : '?'}
                        size="small"
                        color={(allProducts?.[t.id] ?? 0) > 0 ? 'primary' : 'default'}
                        variant="outlined"
                        onClick={() => openLicenses(t, 1)}
                        sx={{ cursor: 'pointer' }}
                      />
                    </Tooltip>
                  </TableCell>
                  <TableCell>
                    <Chip
                      label={t.is_active ? 'Active' : 'Inactive'}
                      size="small"
                      color={t.is_active ? 'success' : 'default'}
                      variant="outlined"
                    />
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {new Date(t.created_at).toLocaleDateString()}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">
                    <Tooltip title="Manage licenses">
                      <IconButton
                        size="small"
                        onClick={() => openLicenses(t, 0)}
                        color="secondary"
                      >
                        <ExtensionIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Add user to this tenant">
                      <IconButton
                        size="small"
                        onClick={() => setAddUserTenant(t)}
                        disabled={!t.is_active}
                      >
                        <PersonAddIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Edit tenant">
                      <IconButton size="small" onClick={() => setEditTenant(t)}>
                        <EditIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                    <Tooltip title="Deactivate tenant">
                      <IconButton
                        size="small"
                        color="error"
                        onClick={() => deactivate.mutate(t.id)}
                        disabled={!t.is_active || deactivate.isPending}
                      >
                        <PowerSettingsNewIcon fontSize="small" />
                      </IconButton>
                    </Tooltip>
                  </TableCell>
                </TableRow>
              ))
            )}
          </TableBody>
        </Table>
      </GlassCard>

      {addOpen && <TenantDialog open onClose={() => setAddOpen(false)} />}
      {editTenant && (
        <TenantDialog open onClose={() => setEditTenant(null)} existing={editTenant} />
      )}
      {addUserTenant && (
        <AddUserDialog open onClose={() => setAddUserTenant(null)} tenant={addUserTenant} />
      )}
      <ManageLicensesDrawer
        tenant={licenseTenant}
        initialTab={licenseInitialTab}
        onClose={() => setLicenseTenant(null)}
      />
    </Box>
  )
}

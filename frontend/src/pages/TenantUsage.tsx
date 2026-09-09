/**
 * Every customer on one page: what they run, and what they pay for.
 *
 * The point of §9 is that none of this requires entering a tenant. A platform
 * owner asking "how many guards does ABC Security have" should get a number,
 * not have to open a support session and walk through a staff list — a count
 * is running a business, and reading those people's records is not.
 *
 * So this page shows totals and never names. Seeing a customer's actual data
 * is still a support session, and still logged.
 */
import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Chip, Divider, Skeleton, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import SearchIcon from '@mui/icons-material/Search'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { getTenantDetail, getTenantUsage, getTenantUserStats } from '@/api/platform'

const STATUS_COLOUR: Record<string, string> = {
  active: '#00D9C0', trial: '#6C63FF', pending: '#8B85FF',
  suspended: '#FFB020', expired: '#FF7A45', cancelled: '#FF4560',
}

function StatusChip({ status }: { status: string }) {
  const colour = STATUS_COLOUR[status] ?? '#8B85FF'
  return (
    <Chip
      label={status} size="small"
      sx={{ height: 19, fontSize: '0.62rem', color: colour, bgcolor: `${colour}22` }}
    />
  )
}

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : '—'
}


/** One customer in full — company, usage, modules and subscription. (§8, §9) */
function TenantDetail({ tenantId }: { tenantId: string }) {
  const navigate = useNavigate()
  const { data: detail, isLoading } = useQuery({
    queryKey: ['platform-tenant', tenantId],
    queryFn: () => getTenantDetail(tenantId),
  })
  const { data: stats } = useQuery({
    queryKey: ['platform-tenant-users', tenantId],
    queryFn: () => getTenantUserStats(tenantId),
  })

  if (isLoading) return <Skeleton variant="rectangular" height={320} />
  if (!detail) return <Alert severity="warning">Tenant not found.</Alert>

  const t = detail.tenant as Record<string, string | null>

  return (
    <Box>
      <Button
        size="small" startIcon={<ArrowBackIcon />} sx={{ mb: 1 }}
        onClick={() => navigate('/platform/tenants')}
      >
        All tenants
      </Button>

      <PageHeader
        title={String(t.name)}
        subtitle={`${t.slug} · created ${when(t.created_at)}`}
        action={<StatusChip status={String(t.status)} />}
      />

      <Stack direction="row" spacing={1.5} sx={{ flexWrap: 'wrap', gap: 1.5, mb: 2 }}>
        {([
          ['Users', detail.users],
          ['Sites', detail.sites],
          ['Cameras', detail.cameras],
          ['Cameras active', detail.cameras_active],
          ['Recordings', detail.recordings],
        ] as const).map(([label, value]) => (
          <GlassCard key={label} sx={{ p: 2, flex: '1 1 150px', minWidth: 140 }}>
            <Typography
              variant="caption"
              sx={{ color: 'text.secondary', textTransform: 'uppercase',
                    letterSpacing: '0.08em', fontSize: '0.6rem' }}
            >
              {label}
            </Typography>
            <Typography variant="h5" sx={{ fontWeight: 700 }}>{value}</Typography>
          </GlassCard>
        ))}
      </Stack>

      <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', gap: 2 }}>
        {/* §9 — the role breakdown, without entering the tenant. */}
        <GlassCard sx={{ p: 2, flex: '1 1 320px' }}>
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            Users by role
          </Typography>
          {!stats ? <Skeleton height={120} /> : (
            <>
              <Stack direction="row" spacing={2} sx={{ mb: 1.5 }}>
                <Box>
                  <Typography variant="h6" sx={{ fontWeight: 700 }}>{stats.total}</Typography>
                  <Typography variant="caption" color="text.secondary">total</Typography>
                </Box>
                <Box>
                  <Typography variant="h6" sx={{ fontWeight: 700 }}>{stats.active}</Typography>
                  <Typography variant="caption" color="text.secondary">active</Typography>
                </Box>
                <Box>
                  <Typography variant="h6" sx={{ fontWeight: 700 }}>
                    {stats.created_this_month}
                  </Typography>
                  <Typography variant="caption" color="text.secondary">this month</Typography>
                </Box>
              </Stack>
              {stats.by_role.map((r) => (
                <Stack
                  key={r.role_id} direction="row"
                  sx={{ justifyContent: 'space-between', py: 0.4 }}
                >
                  <Typography variant="body2">{r.role_name ?? `Role ${r.role_id}`}</Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{r.count}</Typography>
                </Stack>
              ))}
              <Divider sx={{ my: 1, borderColor: 'rgba(255,255,255,0.06)' }} />
              <Typography variant="caption" color="text.disabled">
                Last login {when(stats.last_login_at)} · {stats.active_last_7_days} signed in
                this week
              </Typography>
            </>
          )}
        </GlassCard>

        <GlassCard sx={{ p: 2, flex: '1 1 320px' }}>
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            Subscription
          </Typography>
          {!detail.subscription ? (
            <Typography variant="body2" color="text.secondary">
              No subscription on record — this customer is not being billed.
            </Typography>
          ) : (
            <Stack spacing={0.6}>
              <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
                <Typography variant="body2" color="text.secondary">Plan</Typography>
                <Typography variant="body2">{detail.subscription.plan_name ?? '—'}</Typography>
              </Stack>
              <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
                <Typography variant="body2" color="text.secondary">Status</Typography>
                <StatusChip status={detail.subscription.status} />
              </Stack>
              <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
                <Typography variant="body2" color="text.secondary">Renews</Typography>
                <Typography variant="body2">
                  {when(detail.subscription.current_period_end)}
                </Typography>
              </Stack>
              <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
                <Typography variant="body2" color="text.secondary">Camera allowance</Typography>
                <Typography variant="body2">
                  {detail.subscription.max_cameras ?? 'unlimited'}
                </Typography>
              </Stack>
            </Stack>
          )}
        </GlassCard>

        <GlassCard sx={{ p: 2, flex: '1 1 320px' }}>
          <Typography variant="subtitle2" sx={{ mb: 1, fontWeight: 600 }}>
            Licensed modules
          </Typography>
          {detail.modules.length === 0 ? (
            <Typography variant="body2" color="text.secondary">
              No module licences.
            </Typography>
          ) : (
            <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.5 }}>
              {detail.modules.map((m) => (
                <Tooltip
                  key={m.module_type}
                  title={m.expires_at ? `Expires ${when(m.expires_at)}` : 'No expiry'}
                >
                  <Chip
                    label={m.module_type} size="small"
                    variant={m.is_enabled ? 'filled' : 'outlined'}
                    sx={{ height: 21, fontSize: '0.62rem',
                          ...(m.is_enabled
                            ? { bgcolor: 'rgba(0,217,192,0.16)', color: '#00D9C0' }
                            : { color: 'text.disabled' }) }}
                  />
                </Tooltip>
              ))}
            </Stack>
          )}
        </GlassCard>
      </Stack>
    </Box>
  )
}


/** The table §7 asks for: one row per customer. */
export default function TenantUsage() {
  const navigate = useNavigate()
  const { tenantId } = useParams()
  const [search, setSearch] = useState('')

  const { data: rows, isLoading } = useQuery({
    queryKey: ['platform-tenants'],
    queryFn: getTenantUsage,
    enabled: !tenantId,
  })

  if (tenantId) return <TenantDetail tenantId={tenantId} />

  const filtered = (rows ?? []).filter((r) => {
    const q = search.trim().toLowerCase()
    return !q || r.name.toLowerCase().includes(q) || r.slug.toLowerCase().includes(q)
  })

  return (
    <Box>
      <PageHeader
        title="Tenant Usage"
        subtitle="What every customer runs — counts only, never their records"
      />

      <TextField
        size="small" fullWidth value={search} sx={{ mb: 2, maxWidth: 380 }}
        placeholder="Search tenant name or slug"
        onChange={(e) => setSearch(e.target.value)}
        InputProps={{ startAdornment: <SearchIcon fontSize="small" sx={{ mr: 1 }} /> }}
      />

      <GlassCard sx={{ p: 0 }}>
        {isLoading ? (
          <Skeleton variant="rectangular" height={220} />
        ) : filtered.length === 0 ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No customer tenants match.
          </Typography>
        ) : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Tenant</TableCell>
                  <TableCell>Plan</TableCell>
                  <TableCell align="right">Users</TableCell>
                  <TableCell align="right">Sites</TableCell>
                  <TableCell align="right">Cameras</TableCell>
                  <TableCell align="right">Modules</TableCell>
                  <TableCell>Subscription</TableCell>
                  <TableCell>Last login</TableCell>
                  <TableCell align="right">Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {filtered.map((t) => (
                  <TableRow
                    key={t.id} hover sx={{ cursor: 'pointer' }}
                    onClick={() => navigate(`/platform/tenants/${t.id}`)}
                  >
                    <TableCell>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>{t.name}</Typography>
                      <Typography variant="caption" color="text.secondary">{t.slug}</Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption">{t.plan_name ?? '—'}</Typography>
                    </TableCell>
                    <TableCell align="right">{t.users}</TableCell>
                    <TableCell align="right">{t.sites}</TableCell>
                    <TableCell align="right">{t.cameras}</TableCell>
                    <TableCell align="right">{t.modules}</TableCell>
                    <TableCell>
                      <Typography variant="caption">
                        {t.subscription_status ?? 'none'}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {when(t.last_login_at)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right"><StatusChip status={t.status} /></TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>
    </Box>
  )
}

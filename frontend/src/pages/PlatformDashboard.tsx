/**
 * The platform owner's dashboard: how the BUSINESS is doing.
 *
 * The question this page answers is "how is my Seventh AI Vision business and
 * platform performing", never "what is happening at my customer's sites". A
 * camera going offline at a guarded warehouse is that company's emergency and
 * none of the vendor's; the vendor's emergency is the AI worker that stopped
 * processing for all of them.
 *
 * So there is no live wall here, no incident queue, no patrol status. Those
 * belong to the tenant application and stay there. What is here is customers,
 * revenue, what they run, and what is broken.
 *
 * Every figure excludes Seventh AI's own tenant, in SQL rather than here —
 * counting yourself as a customer is how a dashboard starts lying.
 */
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Chip, Skeleton, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import BusinessIcon from '@mui/icons-material/Business'
import PeopleIcon from '@mui/icons-material/People'
import ApartmentIcon from '@mui/icons-material/Apartment'
import VideocamIcon from '@mui/icons-material/Videocam'
import PaymentsIcon from '@mui/icons-material/Payments'
import AutoAwesomeIcon from '@mui/icons-material/AutoAwesome'
import ReportProblemIcon from '@mui/icons-material/ReportProblem'
import EventRepeatIcon from '@mui/icons-material/EventRepeat'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import {
  getPlatformDashboard, getPlatformRevenue, getTenantUsage,
} from '@/api/platform'

const STATUS_COLOUR: Record<string, string> = {
  active: '#00D9C0',
  trial: '#6C63FF',
  pending: '#8B85FF',
  suspended: '#FFB020',
  expired: '#FF7A45',
  cancelled: '#FF4560',
}

function money(n: number) {
  return n.toLocaleString(undefined, { style: 'currency', currency: 'USD',
                                       maximumFractionDigits: 0 })
}

/** A KPI card. `sub` is the second line — the movement behind the number,
 *  because a total with no direction is a number nobody acts on. */
function Kpi({ icon, label, value, sub, tone }: {
  icon: React.ReactNode
  label: string
  value: string | number
  sub?: string
  tone?: string
}) {
  return (
    <GlassCard sx={{ p: 2, flex: '1 1 190px', minWidth: 175 }}>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
        <Box sx={{ color: tone ?? '#6C63FF', display: 'flex' }}>{icon}</Box>
        <Typography
          variant="caption"
          sx={{ color: 'text.secondary', textTransform: 'uppercase',
                letterSpacing: '0.08em', fontSize: '0.62rem' }}
        >
          {label}
        </Typography>
      </Stack>
      <Typography variant="h4" sx={{ fontWeight: 700, lineHeight: 1.1 }}>
        {value}
      </Typography>
      {sub && (
        <Typography variant="caption" sx={{ color: 'text.disabled' }}>{sub}</Typography>
      )}
    </GlassCard>
  )
}

export default function PlatformDashboard() {
  const navigate = useNavigate()

  const { data: kpi, isLoading } = useQuery({
    queryKey: ['platform-dashboard'],
    queryFn: getPlatformDashboard,
  })
  const { data: revenue } = useQuery({
    queryKey: ['platform-revenue'],
    queryFn: getPlatformRevenue,
  })
  const { data: tenants } = useQuery({
    queryKey: ['platform-tenants'],
    queryFn: getTenantUsage,
  })

  // The customers worth looking at first are the biggest ones, and "biggest"
  // for a video platform is cameras: it drives storage, AI load and the bill.
  const topCustomers = (tenants ?? [])
    .slice()
    .sort((a, b) => b.cameras - a.cameras)
    .slice(0, 6)

  return (
    <Box>
      <PageHeader
        title="Platform Owner Dashboard"
        subtitle="Seventh AI Vision — customers, revenue, and what the platform is doing"
      />

      {isLoading ? (
        <Skeleton variant="rectangular" height={140} sx={{ mb: 2 }} />
      ) : !kpi ? (
        <Alert severity="warning">Could not load the platform figures.</Alert>
      ) : (
        <>
          <Stack direction="row" spacing={1.5} sx={{ flexWrap: 'wrap', gap: 1.5, mb: 1.5 }}>
            <Kpi
              icon={<BusinessIcon fontSize="small" />} label="Tenants"
              value={kpi.tenants_total}
              sub={`${kpi.tenants_active} active · ${kpi.tenants_trial} trial`}
            />
            <Kpi
              icon={<PeopleIcon fontSize="small" />} label="Users"
              value={kpi.users_total.toLocaleString()}
              sub={`${kpi.users_active_today} active today`}
            />
            <Kpi
              icon={<ApartmentIcon fontSize="small" />} label="Sites"
              value={kpi.sites_total.toLocaleString()}
              sub={`${kpi.sites_new_this_month} added this month`}
            />
            <Kpi
              icon={<VideocamIcon fontSize="small" />} label="Cameras"
              value={kpi.cameras_total.toLocaleString()}
              sub={`${kpi.cameras_active} active`}
              tone="#00D9C0"
            />
          </Stack>

          <Stack direction="row" spacing={1.5} sx={{ flexWrap: 'wrap', gap: 1.5, mb: 2 }}>
            <Kpi
              icon={<PaymentsIcon fontSize="small" />} label="MRR"
              value={revenue ? money(revenue.mrr) : '—'}
              sub={revenue ? `${money(revenue.arr)} ARR` : undefined}
              tone="#00D9C0"
            />
            <Kpi
              icon={<EventRepeatIcon fontSize="small" />} label="Subscriptions"
              value={revenue?.subscriptions_active ?? '—'}
              sub={revenue
                ? `${revenue.subscriptions_trial} trial · ${revenue.renewals_next_30_days} renewing in 30d`
                : undefined}
            />
            <Kpi
              icon={<AutoAwesomeIcon fontSize="small" />} label="AI Modules"
              value={kpi.modules_active}
              sub={`${kpi.modules_licensed} licensed`}
            />
            <Kpi
              icon={<ReportProblemIcon fontSize="small" />} label="Open Issues"
              value={kpi.errors_critical + kpi.errors_warning}
              sub={`${kpi.errors_critical} critical · ${kpi.errors_resolved_today} resolved today`}
              tone={kpi.errors_critical > 0 ? '#FF4560' : '#00D9C0'}
            />
          </Stack>

          {/* New business is the number a platform owner checks first, and a
              total alone never shows it. */}
          {(kpi.tenants_new_this_month > 0 || kpi.users_new_this_month > 0) && (
            <Alert severity="success" sx={{ mb: 2 }}>
              This month: {kpi.tenants_new_this_month} new{' '}
              {kpi.tenants_new_this_month === 1 ? 'tenant' : 'tenants'} and{' '}
              {kpi.users_new_this_month} new{' '}
              {kpi.users_new_this_month === 1 ? 'user' : 'users'} across the platform.
            </Alert>
          )}
        </>
      )}

      <GlassCard sx={{ p: 0 }}>
        <Box sx={{ px: 2, pt: 2, pb: 1 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
            Top customers by cameras
          </Typography>
          <Typography variant="caption" color="text.secondary">
            Cameras drive storage, AI load and the bill, so it is the honest measure of size
          </Typography>
        </Box>
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
                <TableCell align="right">Status</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {topCustomers.length === 0 ? (
                <TableRow>
                  <TableCell colSpan={7}>
                    <Typography variant="body2" color="text.secondary" sx={{ py: 2 }}>
                      No customer tenants yet.
                    </Typography>
                  </TableCell>
                </TableRow>
              ) : topCustomers.map((t) => (
                <TableRow
                  key={t.id} hover sx={{ cursor: 'pointer' }}
                  onClick={() => navigate(`/platform/tenants/${t.id}`)}
                >
                  <TableCell>{t.name}</TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {t.plan_name ?? '—'}
                    </Typography>
                  </TableCell>
                  <TableCell align="right">{t.users}</TableCell>
                  <TableCell align="right">{t.sites}</TableCell>
                  <TableCell align="right">{t.cameras}</TableCell>
                  <TableCell align="right">{t.modules}</TableCell>
                  <TableCell align="right">
                    <Chip
                      label={t.status} size="small"
                      sx={{ height: 19, fontSize: '0.62rem',
                            color: STATUS_COLOUR[t.status] ?? '#8B85FF',
                            bgcolor: `${STATUS_COLOUR[t.status] ?? '#8B85FF'}22` }}
                    />
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>
    </Box>
  )
}

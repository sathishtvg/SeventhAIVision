/**
 * Client Portal (Gap 89) — the ENTIRE app surface for role-7 client users
 * (building owners). Rendered standalone by App.tsx instead of the internal
 * AppShell: no operational sidebar, just a branded, read-only view of THEIR
 * site. Server-side site scoping (Gap 81) guarantees a client with no site
 * assignment sees nothing, and a client assigned to Site A sees only Site A.
 */
import { useState } from 'react'
import {
  AppBar, Box, Button, Chip, Container, Dialog, DialogContent, DialogTitle,
  Grid, IconButton, List, ListItem, ListItemText, Stack, Toolbar, Tooltip,
  Typography,
} from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import LogoutIcon from '@mui/icons-material/Logout'
import NotificationsActiveIcon from '@mui/icons-material/NotificationsActive'
import ReportProblemIcon from '@mui/icons-material/ReportProblem'
import ShieldIcon from '@mui/icons-material/Shield'
import VideocamIcon from '@mui/icons-material/Videocam'
import PlayCircleOutlineIcon from '@mui/icons-material/PlayCircleOutlined'
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong'
import DownloadIcon from '@mui/icons-material/Download'
import { useQuery } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { getAlerts } from '@/api/alerts'
import { listAllStreams } from '@/api/recordings'
import { listInvoices, invoicePdfUrl, type Invoice } from '@/api/invoicing'
import { useAuthStore } from '@/store/auth'
import { GlassCard } from '@/components/common/GlassCard'
import { SeverityChip } from '@/components/common/SeverityChip'
import type { AlertSeverity } from '@/types/api'

const INVOICE_STATUS_COLOR: Record<Invoice['status'], 'default' | 'warning' | 'success'> = {
  draft: 'default',
  finalized: 'warning',
  paid: 'success',
  void: 'default',
}

function KpiCard({ icon, label, value, accent }: {
  icon: React.ReactNode; label: string; value: number | string; accent: string
}) {
  return (
    <GlassCard>
      <Stack direction="row" spacing={1.5} alignItems="center" sx={{ p: 2 }}>
        <Box sx={{
          width: 40, height: 40, borderRadius: 2, display: 'flex',
          alignItems: 'center', justifyContent: 'center',
          background: `${accent}22`, color: accent,
        }}>
          {icon}
        </Box>
        <Box>
          <Typography variant="h5" fontWeight={800}>{value}</Typography>
          <Typography variant="caption" color="text.secondary">{label}</Typography>
        </Box>
      </Stack>
    </GlassCard>
  )
}

export default function ClientPortal() {
  const logout = useAuthStore((s) => s.logout)
  const token = useAuthStore((s) => s.accessToken)
  const [liveCam, setLiveCam] = useState<{ camera_id: string; stream_id: string; name: string } | null>(null)

  const { data: branding } = useQuery({
    queryKey: ['branding'],
    queryFn: () => apiClient.get('/api/v1/branding').then((r) => r.data),
  })
  const { data: alertsPage } = useQuery({
    queryKey: ['client-alerts'],
    queryFn: () => getAlerts('open'),
    refetchInterval: 30_000,
  })
  const { data: incidents } = useQuery({
    queryKey: ['client-incidents'],
    queryFn: () => apiClient.get('/api/v1/incidents?status_filter=open').then((r) => r.data),
    refetchInterval: 60_000,
  })
  const { data: streams = [] } = useQuery({
    queryKey: ['client-streams'],
    queryFn: () => listAllStreams(),
    refetchInterval: 60_000,
  })
  const { data: dobEntries = [] } = useQuery({
    queryKey: ['client-dob'],
    queryFn: () => apiClient.get('/api/v1/dob?limit=10').then((r) => r.data),
  })
  const { data: invoices = [] } = useQuery({
    queryKey: ['client-invoices'],
    queryFn: () => listInvoices(),
    refetchInterval: 120_000,
  })

  const alerts = alertsPage?.items ?? []
  const openIncidents = incidents?.items ?? []
  const onlineCams = streams.filter((s) => s.status === 'online').length
  const siteName = streams[0]?.site_name ?? alerts[0]?.site_name ?? null
  const brandName = branding?.name || 'Security Portal'
  const unpaidInvoices = invoices.filter((i) => i.status === 'finalized').length

  return (
    <Box sx={{ minHeight: '100vh' }}>
      {/* Branded top bar — no internal navigation */}
      <AppBar position="sticky" elevation={0}>
        <Toolbar sx={{ gap: 1.5 }}>
          {branding?.branding?.logo_url ? (
            <Box component="img" src={branding.branding.logo_url} alt=""
                 sx={{ height: 28, borderRadius: 0.5 }} />
          ) : (
            <ShieldIcon />
          )}
          <Box sx={{ flex: 1 }}>
            <Typography variant="subtitle1" fontWeight={800} lineHeight={1.1}>
              {brandName}
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Client security portal{siteName ? ` — ${siteName}` : ''}
            </Typography>
          </Box>
          <Button size="small" startIcon={<LogoutIcon />} onClick={logout} color="inherit">
            Sign out
          </Button>
        </Toolbar>
      </AppBar>

      <Container maxWidth="lg" sx={{ py: 3 }}>
        {/* KPI row */}
        <Grid container spacing={1.5} sx={{ mb: 2 }}>
          <Grid size={{ xs: 12, sm: 3 }}>
            <KpiCard icon={<NotificationsActiveIcon />} label="Open alerts"
                     value={alerts.length} accent="#FF4560" />
          </Grid>
          <Grid size={{ xs: 12, sm: 3 }}>
            <KpiCard icon={<ReportProblemIcon />} label="Open incidents"
                     value={incidents?.total ?? openIncidents.length} accent="#FFA500" />
          </Grid>
          <Grid size={{ xs: 12, sm: 3 }}>
            <KpiCard icon={<VideocamIcon />} label="Cameras online"
                     value={`${onlineCams}/${streams.length}`} accent="#00E396" />
          </Grid>
          <Grid size={{ xs: 12, sm: 3 }}>
            <KpiCard icon={<ReceiptLongIcon />} label="Unpaid invoices"
                     value={unpaidInvoices} accent="#6C63FF" />
          </Grid>
        </Grid>

        <Grid container spacing={1.5}>
          {/* Alerts */}
          <Grid size={{ xs: 12, md: 6 }}>
            <GlassCard>
              <Box sx={{ p: 2 }}>
                <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1 }}>
                  Recent Alerts
                </Typography>
                {alerts.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No open alerts at your site.
                  </Typography>
                ) : (
                  <List dense disablePadding>
                    {alerts.slice(0, 8).map((a) => (
                      <ListItem key={a.id} disableGutters divider>
                        <ListItemText
                          primary={a.title}
                          secondary={new Date(a.created_at).toLocaleString()}
                          primaryTypographyProps={{ variant: 'body2' }}
                          secondaryTypographyProps={{ variant: 'caption' }}
                        />
                        <SeverityChip severity={a.severity as AlertSeverity} />
                      </ListItem>
                    ))}
                  </List>
                )}
              </Box>
            </GlassCard>
          </Grid>

          {/* Incidents */}
          <Grid size={{ xs: 12, md: 6 }}>
            <GlassCard>
              <Box sx={{ p: 2 }}>
                <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1 }}>
                  Open Incidents
                </Typography>
                {openIncidents.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No open incidents.
                  </Typography>
                ) : (
                  <List dense disablePadding>
                    {openIncidents.slice(0, 8).map((i: any) => (
                      <ListItem key={i.id} disableGutters divider>
                        <ListItemText
                          primary={i.title}
                          secondary={new Date(i.created_at).toLocaleString()}
                          primaryTypographyProps={{ variant: 'body2' }}
                          secondaryTypographyProps={{ variant: 'caption' }}
                        />
                        <SeverityChip severity={i.severity as AlertSeverity} />
                      </ListItem>
                    ))}
                  </List>
                )}
              </Box>
            </GlassCard>
          </Grid>

          {/* Cameras */}
          <Grid size={{ xs: 12, md: 6 }}>
            <GlassCard>
              <Box sx={{ p: 2 }}>
                <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1 }}>
                  Cameras
                </Typography>
                {streams.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No cameras are assigned to your account yet — contact your
                    security provider.
                  </Typography>
                ) : (
                  <List dense disablePadding>
                    {streams.map((s) => (
                      <ListItem key={s.id} disableGutters divider>
                        <ListItemText
                          primary={s.camera_name}
                          secondary={s.location ?? undefined}
                          primaryTypographyProps={{ variant: 'body2' }}
                          secondaryTypographyProps={{ variant: 'caption' }}
                        />
                        <Chip
                          label={s.status}
                          size="small"
                          color={s.status === 'online' ? 'success'
                            : s.status === 'degraded' ? 'warning' : 'error'}
                          variant="outlined"
                          sx={{ mr: 1, height: 20 }}
                        />
                        <Tooltip title="Watch live">
                          <span>
                            <IconButton
                              size="small"
                              disabled={s.status === 'offline'}
                              onClick={() => setLiveCam({
                                camera_id: s.camera_id, stream_id: s.id, name: s.camera_name,
                              })}
                            >
                              <PlayCircleOutlineIcon fontSize="small" />
                            </IconButton>
                          </span>
                        </Tooltip>
                      </ListItem>
                    ))}
                  </List>
                )}
              </Box>
            </GlassCard>
          </Grid>

          {/* Occurrence book */}
          <Grid size={{ xs: 12, md: 6 }}>
            <GlassCard>
              <Box sx={{ p: 2 }}>
                <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1 }}>
                  Occurrence Book
                </Typography>
                {dobEntries.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No recent entries.
                  </Typography>
                ) : (
                  <List dense disablePadding>
                    {dobEntries.map((e: any) => (
                      <ListItem key={e.id} disableGutters divider>
                        <ListItemText
                          primary={e.body}
                          secondary={`${e.entry_type.replace(/_/g, ' ')} · ${new Date(e.occurred_at).toLocaleString()}`}
                          primaryTypographyProps={{
                            variant: 'body2',
                            sx: { display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden' },
                          }}
                          secondaryTypographyProps={{ variant: 'caption' }}
                        />
                      </ListItem>
                    ))}
                  </List>
                )}
              </Box>
            </GlassCard>
          </Grid>

          {/* Invoices */}
          <Grid size={{ xs: 12, md: 6 }}>
            <GlassCard>
              <Box sx={{ p: 2 }}>
                <Typography variant="subtitle2" fontWeight={700} sx={{ mb: 1 }}>
                  Invoices
                </Typography>
                {invoices.length === 0 ? (
                  <Typography variant="body2" color="text.secondary">
                    No invoices yet.
                  </Typography>
                ) : (
                  <List dense disablePadding>
                    {invoices.slice(0, 8).map((inv) => (
                      <ListItem key={inv.id} disableGutters divider>
                        <ListItemText
                          primary={inv.invoice_number ?? 'Draft'}
                          secondary={`${inv.period_start} – ${inv.period_end} · $${inv.total_amount.toFixed(2)}`}
                          primaryTypographyProps={{ variant: 'body2' }}
                          secondaryTypographyProps={{ variant: 'caption' }}
                        />
                        <Chip
                          label={inv.status}
                          size="small"
                          color={INVOICE_STATUS_COLOR[inv.status]}
                          variant="outlined"
                          sx={{ mr: 1, height: 20, textTransform: 'capitalize' }}
                        />
                        {inv.status !== 'draft' && (
                          <Tooltip title="Download PDF">
                            <span>
                              <IconButton
                                size="small"
                                component="a"
                                href={invoicePdfUrl(inv.id, token) ?? undefined}
                                target="_blank"
                                rel="noopener"
                              >
                                <DownloadIcon fontSize="small" />
                              </IconButton>
                            </span>
                          </Tooltip>
                        )}
                      </ListItem>
                    ))}
                  </List>
                )}
              </Box>
            </GlassCard>
          </Grid>
        </Grid>

        <Typography variant="caption" color="text.disabled"
                    sx={{ display: 'block', textAlign: 'center', mt: 3 }}>
          Read-only client portal · data limited to your site
        </Typography>
      </Container>

      {/* Live view dialog */}
      {liveCam && token && (
        <Dialog open onClose={() => setLiveCam(null)} maxWidth="md" fullWidth>
          <DialogTitle sx={{ py: 1 }}>
            {liveCam.name}
            <IconButton sx={{ float: 'right' }} size="small" onClick={() => setLiveCam(null)}>
              <CloseIcon />
            </IconButton>
          </DialogTitle>
          <DialogContent sx={{ p: 0 }}>
            <Box
              component="img"
              src={`${apiClient.defaults.baseURL}/api/v1/cameras/${liveCam.camera_id}/streams/${liveCam.stream_id}/live?token=${token}`}
              alt={liveCam.name}
              sx={{ width: '100%', display: 'block', background: '#000', minHeight: 240 }}
            />
          </DialogContent>
        </Dialog>
      )}
    </Box>
  )
}

import { useState } from 'react'
import {
  Box, Typography, Tab, Tabs, Grid, Chip, Button, Dialog,
  DialogTitle, DialogContent, DialogActions, TextField,
  MenuItem, Table, TableHead, TableRow, TableCell, TableBody,
  CircularProgress, Skeleton, Alert, Collapse, IconButton,
  LinearProgress, Tooltip,
} from '@mui/material'
import CampaignIcon from '@mui/icons-material/Campaign'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
import ExpandLessIcon from '@mui/icons-material/ExpandLess'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import SendIcon from '@mui/icons-material/Send'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  listBroadcasts, getMyBroadcasts, sendBroadcast, acknowledgeBroadcast, getBroadcast,
  type EmergencyBroadcast,
} from '@/api/emergency'
import GlassCard from '@/components/common/GlassCard'
import { usePermission } from '@/hooks/usePermission'
import { useAuthStore } from '@/store/auth'
import { PageHeader } from '@/components/common/PageHeader'

const SEV_CONFIG = {
  info:     { color: '#2196F3', label: 'Info',     bg: 'rgba(33,150,243,0.15)' },
  warning:  { color: '#FF9800', label: 'Warning',  bg: 'rgba(255,152,0,0.15)' },
  critical: { color: '#FF4560', label: 'CRITICAL', bg: 'rgba(255,69,96,0.15)' },
  drill:    { color: '#6C63FF', label: 'Drill',    bg: 'rgba(108,99,255,0.15)' },
}

const ROLE_OPTIONS = [
  { id: 2, label: 'Administrators' },
  { id: 3, label: 'Supervisors' },
  { id: 4, label: 'Operators' },
  { id: 5, label: 'Security Guards' },
  { id: 6, label: 'Viewers' },
]

function SeverityChip({ severity }: { severity: string }) {
  const cfg = SEV_CONFIG[severity as keyof typeof SEV_CONFIG] ?? SEV_CONFIG.warning
  return (
    <Chip label={cfg.label} size="small"
      sx={{ bgcolor: cfg.bg, color: cfg.color, fontSize: '0.72rem', fontWeight: 700 }} />
  )
}

function AckProgress({ acked, total }: { acked: number; total: number }) {
  const pct = total > 0 ? Math.round((acked / total) * 100) : 0
  return (
    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
      <LinearProgress variant="determinate" value={pct}
        sx={{ flex: 1, height: 6, borderRadius: 3,
              '& .MuiLinearProgress-bar': { bgcolor: pct === 100 ? '#00E396' : '#6C63FF' } }} />
      <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.6)', whiteSpace: 'nowrap' }}>
        {acked}/{total}
      </Typography>
    </Box>
  )
}

// ── Broadcast detail row (expandable) ────────────────────────────────────────

function BroadcastRow({ b, showAck = false }: { b: any; showAck?: boolean }) {
  const [expanded, setExpanded] = useState(false)
  const [detailOpen, setDetailOpen] = useState(false)
  const qc = useQueryClient()
  const user = useAuthStore(s => s.user)

  const { data: detail, isLoading: detailLoading } = useQuery({
    queryKey: ['broadcast-detail', b.id],
    queryFn: () => getBroadcast(b.id),
    enabled: detailOpen,
  })

  const ackMut = useMutation({
    mutationFn: () => acknowledgeBroadcast(b.id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['my-broadcasts'] })
      qc.invalidateQueries({ queryKey: ['broadcasts'] })
    },
  })

  const sevCfg = SEV_CONFIG[b.severity as keyof typeof SEV_CONFIG] ?? SEV_CONFIG.warning
  const isAcked = !!b.acknowledged_at

  return (
    <>
      <TableRow hover
        sx={{ borderLeft: `3px solid ${sevCfg.color}`, cursor: 'pointer' }}
        onClick={() => setExpanded(e => !e)}>
        <TableCell>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
            {expanded ? <ExpandLessIcon fontSize="small" sx={{ color: 'rgba(255,255,255,0.4)' }} />
                      : <ExpandMoreIcon fontSize="small" sx={{ color: 'rgba(255,255,255,0.4)' }} />}
            <Box>
              <Typography fontWeight={600} fontSize="0.88rem">{b.title}</Typography>
              <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)' }}>
                {b.sender_name ?? 'Operations'} · {new Date(b.sent_at || b.created_at).toLocaleString()}
              </Typography>
            </Box>
          </Box>
        </TableCell>
        <TableCell><SeverityChip severity={b.severity} /></TableCell>
        <TableCell>
          <Chip label={b.broadcast_type === 'all' ? 'All Staff' : 'By Role'}
            size="small" sx={{ bgcolor: 'rgba(255,255,255,0.06)', fontSize: '0.7rem' }} />
        </TableCell>
        {!showAck && (
          <TableCell>
            <AckProgress acked={b.acknowledged_count ?? 0} total={b.recipient_count ?? 0} />
          </TableCell>
        )}
        {showAck && (
          <TableCell>
            {isAcked
              ? <Chip icon={<CheckCircleIcon sx={{ fontSize: '14px !important' }} />}
                  label="Acknowledged" size="small"
                  sx={{ bgcolor: 'rgba(0,227,150,0.15)', color: '#00E396', fontSize: '0.7rem' }} />
              : <Button size="small" variant="outlined" color="warning"
                  disabled={ackMut.isPending}
                  onClick={e => { e.stopPropagation(); ackMut.mutate() }}>
                  {ackMut.isPending ? <CircularProgress size={14} /> : 'Acknowledge'}
                </Button>
            }
          </TableCell>
        )}
        <TableCell align="right">
          <Button size="small" variant="text"
            onClick={e => { e.stopPropagation(); setDetailOpen(true) }}
            sx={{ fontSize: '0.72rem' }}>
            Recipients
          </Button>
        </TableCell>
      </TableRow>

      <TableRow>
        <TableCell colSpan={showAck ? 5 : 5} sx={{ py: 0, border: 0 }}>
          <Collapse in={expanded} timeout="auto">
            <Box sx={{ py: 1.5, px: 2, bgcolor: 'rgba(255,255,255,0.02)', borderRadius: 1, mb: 0.5 }}>
              <Typography variant="body2" sx={{ color: 'rgba(255,255,255,0.75)', whiteSpace: 'pre-wrap' }}>
                {b.message}
              </Typography>
            </Box>
          </Collapse>
        </TableCell>
      </TableRow>

      <Dialog open={detailOpen} onClose={() => setDetailOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Recipient List — {b.title}</DialogTitle>
        <DialogContent>
          {detailLoading && <CircularProgress />}
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Name</TableCell>
                <TableCell>Role</TableCell>
                <TableCell>Acknowledged</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {detail?.recipients?.map(r => (
                <TableRow key={r.user_id} hover>
                  <TableCell>
                    <Typography fontSize="0.82rem">{r.full_name}</Typography>
                    <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)' }}>{r.email}</Typography>
                  </TableCell>
                  <TableCell sx={{ fontSize: '0.78rem', color: 'rgba(255,255,255,0.5)' }}>
                    {r.role_name}
                  </TableCell>
                  <TableCell>
                    {r.acknowledged_at
                      ? <Chip icon={<CheckCircleIcon sx={{ fontSize: '13px !important' }} />}
                          label={new Date(r.acknowledged_at).toLocaleTimeString()} size="small"
                          sx={{ bgcolor: 'rgba(0,227,150,0.12)', color: '#00E396', fontSize: '0.65rem' }} />
                      : <Chip label="Pending" size="small"
                          sx={{ bgcolor: 'rgba(255,152,0,0.12)', color: '#FF9800', fontSize: '0.65rem' }} />
                    }
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setDetailOpen(false)}>Close</Button>
        </DialogActions>
      </Dialog>
    </>
  )
}

// ── History tab ───────────────────────────────────────────────────────────────

function HistoryTab() {
  const { data = [], isLoading } = useQuery({
    queryKey: ['broadcasts'],
    queryFn: () => listBroadcasts(),
    refetchInterval: 30_000,
  })

  return (
    <GlassCard sx={{ p: 0 }}>
      <Table>
        <TableHead>
          <TableRow>
            <TableCell>Broadcast</TableCell>
            <TableCell>Severity</TableCell>
            <TableCell>Target</TableCell>
            <TableCell>Acknowledgements</TableCell>
            <TableCell />
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading && [...Array(3)].map((_, i) => (
            <TableRow key={i}>
              {[...Array(5)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}
            </TableRow>
          ))}
          {data.map(b => <BroadcastRow key={b.id} b={b} />)}
          {!isLoading && !data.length && (
            <TableRow>
              <TableCell colSpan={5} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 4 }}>
                No emergency broadcasts sent yet
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
    </GlassCard>
  )
}

// ── My Broadcasts tab ─────────────────────────────────────────────────────────

function MyBroadcastsTab() {
  const { data = [], isLoading } = useQuery({
    queryKey: ['my-broadcasts'],
    queryFn: getMyBroadcasts,
    refetchInterval: 30_000,
  })

  const unacked = data.filter(b => !b.acknowledged_at).length

  return (
    <Box>
      {unacked > 0 && (
        <Alert severity="warning"
          sx={{ mb: 2, bgcolor: 'rgba(255,152,0,0.1)', color: '#FF9800',
                '& .MuiAlert-icon': { color: '#FF9800' } }}>
          You have {unacked} unacknowledged broadcast{unacked > 1 ? 's' : ''}.
          Please acknowledge receipt.
        </Alert>
      )}
      <GlassCard sx={{ p: 0 }}>
        <Table>
          <TableHead>
            <TableRow>
              <TableCell>Broadcast</TableCell>
              <TableCell>Severity</TableCell>
              <TableCell>Target</TableCell>
              <TableCell>Your Status</TableCell>
              <TableCell />
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading && [...Array(3)].map((_, i) => (
              <TableRow key={i}>
                {[...Array(5)].map((_, j) => <TableCell key={j}><Skeleton /></TableCell>)}
              </TableRow>
            ))}
            {data.map(b => <BroadcastRow key={b.id} b={b} showAck />)}
            {!isLoading && !data.length && (
              <TableRow>
                <TableCell colSpan={5} align="center" sx={{ color: 'rgba(255,255,255,0.3)', py: 4 }}>
                  No broadcasts received
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </GlassCard>
    </Box>
  )
}

// ── Compose tab ───────────────────────────────────────────────────────────────

function ComposeTab() {
  const canSend = usePermission('broadcast:send')
  const qc = useQueryClient()
  const [sent, setSent] = useState<{ recipient_count: number } | null>(null)
  const [form, setForm] = useState({
    title: '',
    message: '',
    severity: 'warning',
    broadcast_type: 'all',
    target_role_ids: [] as number[],
  })

  const sendMut = useMutation({
    mutationFn: sendBroadcast,
    onSuccess: (data) => {
      setSent(data)
      qc.invalidateQueries({ queryKey: ['broadcasts'] })
      qc.invalidateQueries({ queryKey: ['my-broadcasts'] })
      setForm({ title: '', message: '', severity: 'warning', broadcast_type: 'all', target_role_ids: [] })
    },
  })

  if (!canSend) {
    return (
      <Alert severity="info" sx={{ bgcolor: 'rgba(33,150,243,0.1)', color: '#2196F3' }}>
        You need Supervisor or higher permissions to send emergency broadcasts.
      </Alert>
    )
  }

  if (sent) {
    return (
      <GlassCard sx={{ p: 4, textAlign: 'center', maxWidth: 480, mx: 'auto' }}>
        <CheckCircleIcon sx={{ fontSize: 64, color: '#00E396', mb: 2 }} />
        <Typography variant="h6" fontWeight={700} gutterBottom>
          Broadcast Sent
        </Typography>
        <Typography sx={{ color: 'rgba(255,255,255,0.6)', mb: 3 }}>
          Delivered to {sent.recipient_count} recipient{sent.recipient_count !== 1 ? 's' : ''}.
          Email notifications are on their way.
        </Typography>
        <Button variant="outlined" onClick={() => setSent(null)}>
          Send Another
        </Button>
      </GlassCard>
    )
  }

  const sevCfg = SEV_CONFIG[form.severity as keyof typeof SEV_CONFIG] ?? SEV_CONFIG.warning

  return (
    <Grid container spacing={3}>
      <Grid size={{ xs: 12, md: 7 }}>
        <GlassCard sx={{ p: 3 }}>
          <Typography variant="subtitle1" fontWeight={700} gutterBottom>
            Compose Broadcast
          </Typography>

          {form.severity === 'critical' && (
            <Alert severity="error"
              sx={{ mb: 2, bgcolor: 'rgba(255,69,96,0.1)', color: '#FF4560',
                    '& .MuiAlert-icon': { color: '#FF4560' } }}>
              Critical broadcasts will be sent immediately via email to all recipients.
            </Alert>
          )}

          <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
            <TextField label="Title" value={form.title} required fullWidth size="small"
              placeholder="e.g. Security Alert: Perimeter Breach — Building A"
              onChange={e => setForm(f => ({ ...f, title: e.target.value }))} />

            <TextField label="Message" value={form.message} required fullWidth
              multiline rows={5} size="small"
              placeholder="Describe the situation clearly. Include location, instructions, and any actions required by staff."
              onChange={e => setForm(f => ({ ...f, message: e.target.value }))} />

            <Box sx={{ display: 'flex', gap: 2 }}>
              <TextField label="Severity" value={form.severity} size="small" select sx={{ flex: 1 }}
                onChange={e => setForm(f => ({ ...f, severity: e.target.value }))}>
                {Object.entries(SEV_CONFIG).map(([k, v]) => (
                  <MenuItem key={k} value={k}>
                    <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                      <Box sx={{ width: 8, height: 8, borderRadius: '50%', bgcolor: v.color }} />
                      {v.label}
                    </Box>
                  </MenuItem>
                ))}
              </TextField>

              <TextField label="Target Audience" value={form.broadcast_type} size="small" select sx={{ flex: 1 }}
                onChange={e => setForm(f => ({ ...f, broadcast_type: e.target.value, target_role_ids: [] }))}>
                <MenuItem value="all">All Staff</MenuItem>
                <MenuItem value="role">Specific Roles</MenuItem>
              </TextField>
            </Box>

            {form.broadcast_type === 'role' && (
              <Box>
                <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.5)', mb: 0.5, display: 'block' }}>
                  Select target roles
                </Typography>
                <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
                  {ROLE_OPTIONS.map(r => {
                    const selected = form.target_role_ids.includes(r.id)
                    return (
                      <Chip key={r.id} label={r.label} size="small" clickable
                        onClick={() => setForm(f => ({
                          ...f,
                          target_role_ids: selected
                            ? f.target_role_ids.filter(id => id !== r.id)
                            : [...f.target_role_ids, r.id]
                        }))}
                        sx={{
                          bgcolor: selected ? 'rgba(108,99,255,0.25)' : 'rgba(255,255,255,0.06)',
                          color: selected ? '#6C63FF' : 'rgba(255,255,255,0.6)',
                          border: selected ? '1px solid #6C63FF' : '1px solid rgba(255,255,255,0.1)',
                          cursor: 'pointer',
                          fontSize: '0.75rem',
                        }} />
                    )
                  })}
                </Box>
              </Box>
            )}

            <Button
              variant="contained" startIcon={sendMut.isPending ? <CircularProgress size={18} /> : <SendIcon />}
              disabled={!form.title || !form.message || sendMut.isPending ||
                (form.broadcast_type === 'role' && form.target_role_ids.length === 0)}
              onClick={() => sendMut.mutate({
                title: form.title, message: form.message, severity: form.severity,
                broadcast_type: form.broadcast_type,
                target_role_ids: form.broadcast_type === 'role' ? form.target_role_ids : undefined,
              })}
              sx={{
                bgcolor: sevCfg.color,
                '&:hover': { bgcolor: sevCfg.color, filter: 'brightness(1.1)' },
                fontWeight: 700,
              }}>
              {sendMut.isPending ? 'Sending…' : 'Send Broadcast'}
            </Button>

            {sendMut.isError && (
              <Alert severity="error" sx={{ bgcolor: 'rgba(255,69,96,0.1)', color: '#FF4560' }}>
                Failed to send broadcast. Please try again.
              </Alert>
            )}
          </Box>
        </GlassCard>
      </Grid>

      <Grid size={{ xs: 12, md: 5 }}>
        <GlassCard sx={{ p: 3 }}>
          <Typography variant="subtitle2" fontWeight={600} gutterBottom>
            Preview
          </Typography>
          <Box sx={{
            bgcolor: `${sevCfg.color}11`,
            border: `1px solid ${sevCfg.color}44`,
            borderRadius: 2, p: 2,
          }}>
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 1 }}>
              <CampaignIcon sx={{ color: sevCfg.color, fontSize: 20 }} />
              <Chip label={sevCfg.label} size="small"
                sx={{ bgcolor: `${sevCfg.color}22`, color: sevCfg.color, fontSize: '0.7rem' }} />
              <Chip label={form.broadcast_type === 'all' ? 'All Staff' : 'Selected Roles'}
                size="small" sx={{ bgcolor: 'rgba(255,255,255,0.06)', fontSize: '0.7rem' }} />
            </Box>
            <Typography fontWeight={700} fontSize="0.9rem" gutterBottom>
              {form.title || 'Broadcast Title'}
            </Typography>
            <Typography variant="body2" sx={{ color: 'rgba(255,255,255,0.65)', whiteSpace: 'pre-wrap' }}>
              {form.message || 'Your message will appear here…'}
            </Typography>
          </Box>

          <Box sx={{ mt: 2 }}>
            <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.4)', display: 'block', mb: 1 }}>
              Delivery channels
            </Typography>
            {['In-app (WebSocket push)', 'Email to all recipients'].map(ch => (
              <Box key={ch} sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 0.5 }}>
                <CheckCircleIcon sx={{ fontSize: 14, color: '#00E396' }} />
                <Typography variant="caption" sx={{ color: 'rgba(255,255,255,0.6)' }}>{ch}</Typography>
              </Box>
            ))}
          </Box>
        </GlassCard>
      </Grid>
    </Grid>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────

export default function EmergencyBroadcastPage() {
  const [tab, setTab] = useState(0)
  const canSend = usePermission('broadcast:send')

  return (
    <Box sx={{ p: 3 }}>
      <PageHeader pageKey="emergency-broadcast" />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 3 }}>
        <CampaignIcon sx={{ color: '#FF4560', fontSize: 28 }} />
      </Box>

      <Tabs value={tab} onChange={(_, v) => setTab(v)}
        sx={{ mb: 3, '& .MuiTab-root': { fontSize: '0.85rem', minWidth: 120 } }}>
        <Tab label="History" />
        <Tab label="My Broadcasts" />
        {canSend && <Tab label="Send Broadcast" />}
      </Tabs>

      {tab === 0 && <HistoryTab />}
      {tab === 1 && <MyBroadcastsTab />}
      {tab === 2 && canSend && <ComposeTab />}
    </Box>
  )
}

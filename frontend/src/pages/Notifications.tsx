import { useState } from 'react'
import {
  Box, Tabs, Tab, Typography, Table, TableBody, TableCell, TableContainer,
  TableHead, TableRow, Chip, Skeleton, Paper, Button, IconButton, Dialog,
  DialogTitle, DialogContent, DialogActions, TextField, Select, MenuItem,
  FormControl, InputLabel, Tooltip,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import SendIcon from '@mui/icons-material/Send'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import {
  getChannels, createChannel, deleteChannel, testChannel,
  getRules, createRule, deleteRule,
  getLogs,
} from '@/api/notifications'
import type { NotificationChannel } from '@/types/api'
import { PageHeader } from '@/components/common/PageHeader'

interface TabPanelProps { children: React.ReactNode; value: number; index: number }
function TabPanel({ children, value, index }: TabPanelProps) {
  return <Box hidden={value !== index}>{value === index && children}</Box>
}

function SkeletonRows({ cols, rows = 4 }: { cols: number; rows?: number }) {
  return (
    <>
      {Array.from({ length: rows }).map((_, i) => (
        <TableRow key={i}>
          {Array.from({ length: cols }).map((__, j) => <TableCell key={j}><Skeleton /></TableCell>)}
        </TableRow>
      ))}
    </>
  )
}

const STATUS_COLORS = { sent: 'success', failed: 'error', pending: 'warning' } as const
const SEVERITIES = ['info', 'low', 'medium', 'high', 'critical']

// ──────────────────────────────────────────────────────────
// Channels tab
// ──────────────────────────────────────────────────────────

function ChannelDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [name, setName] = useState('')
  const [type, setType] = useState<'email' | 'sms' | 'webhook'>('email')
  const [configJson, setConfigJson] = useState('{}')
  const [jsonError, setJsonError] = useState('')

  const mutation = useMutation({
    mutationFn: () => {
      let config: Record<string, unknown> = {}
      try { config = JSON.parse(configJson) } catch { throw new Error('Invalid JSON in config') }
      return createChannel({ name, channel_type: type, config })
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['notification-channels'] }); onClose(); setName(''); setConfigJson('{}') },
  })

  const handleConfigChange = (val: string) => {
    setConfigJson(val)
    try { JSON.parse(val); setJsonError('') } catch { setJsonError('Invalid JSON') }
  }

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Notification Channel</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        <TextField label="Name" value={name} onChange={(e) => setName(e.target.value)} fullWidth />
        <FormControl fullWidth>
          <InputLabel>Type</InputLabel>
          <Select value={type} label="Type" onChange={(e) => setType(e.target.value as typeof type)}>
            <MenuItem value="email">Email</MenuItem>
            <MenuItem value="sms">SMS (Twilio)</MenuItem>
            <MenuItem value="webhook">Webhook</MenuItem>
          </Select>
        </FormControl>
        <TextField
          label="Config (JSON)"
          value={configJson}
          onChange={(e) => handleConfigChange(e.target.value)}
          multiline rows={4}
          error={!!jsonError}
          helperText={jsonError || (type === 'email' ? 'e.g. {"to_addresses": ["ops@example.com"]}' : type === 'sms' ? 'e.g. {"to_numbers": ["+6591234567"]}' : 'e.g. {"url": "https://hooks.example.com/alert"}')}
          fullWidth
        />
        {mutation.error && <Typography color="error" variant="caption">{String(mutation.error)}</Typography>}
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => mutation.mutate()} disabled={!name || !!jsonError || mutation.isPending}>Add</Button>
      </DialogActions>
    </Dialog>
  )
}

function ChannelsTable() {
  const qc = useQueryClient()
  const { data, isLoading } = useQuery({ queryKey: ['notification-channels'], queryFn: getChannels })
  const [dialogOpen, setDialogOpen] = useState(false)
  const [testingId, setTestingId] = useState<string | null>(null)

  const deleteMutation = useMutation({
    mutationFn: deleteChannel,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notification-channels'] }),
  })

  const handleTest = async (id: string) => {
    setTestingId(id)
    try { await testChannel(id) } catch { /* toast would go here */ } finally { setTestingId(null) }
  }

  return (
    <>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
        <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setDialogOpen(true)}>
          Add Channel
        </Button>
      </Box>
      <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Name</TableCell>
              <TableCell>Type</TableCell>
              <TableCell>Active</TableCell>
              <TableCell>Created</TableCell>
              <TableCell align="right">Actions</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {isLoading
              ? <SkeletonRows cols={5} />
              : data?.map((ch) => (
                  <TableRow key={ch.id} hover>
                    <TableCell><Typography variant="body2" sx={{ fontWeight: 600 }}>{ch.name}</Typography></TableCell>
                    <TableCell><Chip label={ch.channel_type} size="small" variant="outlined" /></TableCell>
                    <TableCell>
                      <Chip label={ch.is_active ? 'active' : 'inactive'} size="small"
                        color={ch.is_active ? 'success' : 'default'} />
                    </TableCell>
                    <TableCell><Typography variant="caption" color="text.secondary">{new Date(ch.created_at).toLocaleDateString()}</Typography></TableCell>
                    <TableCell align="right">
                      <Tooltip title="Send test notification">
                        <IconButton size="small" onClick={() => handleTest(ch.id)} disabled={testingId === ch.id}>
                          <SendIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                      <Tooltip title="Delete channel">
                        <IconButton size="small" color="error" onClick={() => deleteMutation.mutate(ch.id)}>
                          <DeleteIcon fontSize="small" />
                        </IconButton>
                      </Tooltip>
                    </TableCell>
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </TableContainer>
      <ChannelDialog open={dialogOpen} onClose={() => setDialogOpen(false)} />
    </>
  )
}

// ──────────────────────────────────────────────────────────
// Rules tab
// ──────────────────────────────────────────────────────────

function RuleDialog({ open, onClose, channels }: { open: boolean; onClose: () => void; channels: NotificationChannel[] }) {
  const qc = useQueryClient()
  const [channelId, setChannelId] = useState('')
  const [minSeverity, setMinSeverity] = useState('medium')
  const [moduleTypes, setModuleTypes] = useState('')
  const [alertCodes, setAlertCodes] = useState('')
  const [triggerEvents, setTriggerEvents] = useState('')

  const splitList = (val: string) => val ? val.split(',').map((s) => s.trim()).filter(Boolean) : []

  const mutation = useMutation({
    mutationFn: () => createRule({
      channel_id: channelId,
      min_severity: minSeverity,
      module_types: splitList(moduleTypes),
      alert_codes: splitList(alertCodes),
      trigger_events: splitList(triggerEvents),
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['notification-rules'] }); onClose() },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add Notification Rule</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '16px !important' }}>
        <FormControl fullWidth>
          <InputLabel>Channel</InputLabel>
          <Select value={channelId} label="Channel" onChange={(e) => setChannelId(e.target.value)}>
            {channels.map((ch) => <MenuItem key={ch.id} value={ch.id}>{ch.name}</MenuItem>)}
          </Select>
        </FormControl>
        <FormControl fullWidth>
          <InputLabel>Min Severity</InputLabel>
          <Select value={minSeverity} label="Min Severity" onChange={(e) => setMinSeverity(e.target.value)}>
            {SEVERITIES.map((s) => <MenuItem key={s} value={s}>{s}</MenuItem>)}
          </Select>
        </FormControl>
        <TextField
          label="Module Types filter (comma-separated, empty = all)"
          value={moduleTypes} onChange={(e) => setModuleTypes(e.target.value)}
          helperText="e.g. intrusion,fire_smoke  — leave empty to match all modules"
          fullWidth
        />
        <TextField
          label="Alert Codes filter (comma-separated, empty = all)"
          value={alertCodes} onChange={(e) => setAlertCodes(e.target.value)}
          helperText="e.g. lpr.blocklist_hit  — leave empty to match all codes"
          fullWidth
        />
        <TextField
          label="Trigger Events (comma-separated, empty = alert_created only)"
          value={triggerEvents} onChange={(e) => setTriggerEvents(e.target.value)}
          helperText="e.g. sos_triggered,incident_created  — use * to match all event types"
          fullWidth
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => mutation.mutate()} disabled={!channelId || mutation.isPending}>Add</Button>
      </DialogActions>
    </Dialog>
  )
}

function RulesTable() {
  const qc = useQueryClient()
  const { data: rules, isLoading: rulesLoading } = useQuery({ queryKey: ['notification-rules'], queryFn: getRules })
  const { data: channels = [] } = useQuery({ queryKey: ['notification-channels'], queryFn: getChannels })
  const [dialogOpen, setDialogOpen] = useState(false)

  const deleteMutation = useMutation({
    mutationFn: deleteRule,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['notification-rules'] }),
  })

  const channelName = (id: string) => channels.find((c) => c.id === id)?.name ?? id.slice(0, 8) + '…'

  return (
    <>
      <Box sx={{ display: 'flex', justifyContent: 'flex-end', mb: 1 }}>
        <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setDialogOpen(true)}>
          Add Rule
        </Button>
      </Box>
      <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
        <Table size="small">
          <TableHead>
            <TableRow>
              <TableCell>Channel</TableCell>
              <TableCell>Min Severity</TableCell>
              <TableCell>Modules</TableCell>
              <TableCell>Alert Codes</TableCell>
              <TableCell>Trigger Events</TableCell>
              <TableCell>Active</TableCell>
              <TableCell align="right">Delete</TableCell>
            </TableRow>
          </TableHead>
          <TableBody>
            {rulesLoading
              ? <SkeletonRows cols={7} />
              : rules?.map((rule) => (
                  <TableRow key={rule.id} hover>
                    <TableCell>{channelName(rule.channel_id)}</TableCell>
                    <TableCell><Chip label={rule.min_severity} size="small" color="warning" /></TableCell>
                    <TableCell>
                      {rule.module_types.length === 0
                        ? <Typography variant="caption" color="text.secondary">all</Typography>
                        : rule.module_types.map((m) => <Chip key={m} label={m} size="small" sx={{ mr: 0.5 }} />)}
                    </TableCell>
                    <TableCell>
                      {rule.alert_codes.length === 0
                        ? <Typography variant="caption" color="text.secondary">all</Typography>
                        : rule.alert_codes.map((c) => <Chip key={c} label={c} size="small" sx={{ mr: 0.5 }} />)}
                    </TableCell>
                    <TableCell>
                      {!rule.trigger_events || rule.trigger_events.length === 0
                        ? <Typography variant="caption" color="text.secondary">alert_created</Typography>
                        : rule.trigger_events.map((e) => <Chip key={e} label={e} size="small" variant="outlined" sx={{ mr: 0.5 }} />)}
                    </TableCell>
                    <TableCell>
                      <Chip label={rule.is_active ? 'active' : 'inactive'} size="small"
                        color={rule.is_active ? 'success' : 'default'} />
                    </TableCell>
                    <TableCell align="right">
                      <IconButton size="small" color="error" onClick={() => deleteMutation.mutate(rule.id)}>
                        <DeleteIcon fontSize="small" />
                      </IconButton>
                    </TableCell>
                  </TableRow>
                ))}
          </TableBody>
        </Table>
      </TableContainer>
      <RuleDialog open={dialogOpen} onClose={() => setDialogOpen(false)} channels={channels} />
    </>
  )
}

// ──────────────────────────────────────────────────────────
// Logs tab
// ──────────────────────────────────────────────────────────

function LogsTable() {
  const { data, isLoading } = useQuery({ queryKey: ['notification-logs'], queryFn: () => getLogs(undefined, undefined, 200) })
  return (
    <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
      <Table size="small">
        <TableHead>
          <TableRow>
            <TableCell>Alert ID</TableCell>
            <TableCell>Channel Type</TableCell>
            <TableCell>Status</TableCell>
            <TableCell>Error</TableCell>
            <TableCell>Sent At</TableCell>
            <TableCell>Created</TableCell>
          </TableRow>
        </TableHead>
        <TableBody>
          {isLoading
            ? <SkeletonRows cols={6} />
            : data?.map((log) => (
                <TableRow key={log.id} hover>
                  <TableCell><Typography variant="caption" sx={{ fontFamily: 'monospace' }}>{log.alert_id.slice(0, 8)}…</Typography></TableCell>
                  <TableCell><Chip label={log.channel_type} size="small" variant="outlined" /></TableCell>
                  <TableCell>
                    <Chip label={log.status} size="small"
                      color={STATUS_COLORS[log.status] ?? 'default'} />
                  </TableCell>
                  <TableCell>
                    {log.error_detail
                      ? <Typography variant="caption" color="error.main" sx={{ maxWidth: 200, display: 'block', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{log.error_detail}</Typography>
                      : '—'}
                  </TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{log.sent_at ? new Date(log.sent_at).toLocaleString() : '—'}</Typography></TableCell>
                  <TableCell><Typography variant="caption" color="text.secondary">{new Date(log.created_at).toLocaleString()}</Typography></TableCell>
                </TableRow>
              ))}
        </TableBody>
      </Table>
    </TableContainer>
  )
}

// ──────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────

export default function Notifications() {
  const [tab, setTab] = useState(0)
  return (
    <Box>
      <PageHeader title="Notifications" subtitle="Where alerts get sent — email, SMS and webhook channels, and their delivery history" />
      <GlassCard>
        <Box sx={{ borderBottom: 1, borderColor: 'rgba(255,255,255,0.1)' }}>
          <Tabs value={tab} onChange={(_, v) => setTab(v)}>
            <Tab label="Channels" />
            <Tab label="Rules" />
            <Tab label="Delivery Logs" />
          </Tabs>
        </Box>
        <TabPanel value={tab} index={0}><ChannelsTable /></TabPanel>
        <TabPanel value={tab} index={1}><RulesTable /></TabPanel>
        <TabPanel value={tab} index={2}><LogsTable /></TabPanel>
      </GlassCard>
    </Box>
  )
}

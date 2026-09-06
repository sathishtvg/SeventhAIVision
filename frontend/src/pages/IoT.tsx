import React, { useState } from 'react'
import {
  Box, Typography, Grid, Paper, Chip, Stack, Divider, CircularProgress,
  Button, Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, MenuItem, Tab, Tabs, Table, TableBody, TableCell,
  TableHead, TableRow, Tooltip, IconButton, Alert,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import CheckIcon from '@mui/icons-material/Check'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import ErrorIcon from '@mui/icons-material/Error'
import SignalWifiOffIcon from '@mui/icons-material/SignalWifiOff'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import SensorsIcon from '@mui/icons-material/Sensors'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  getIoTDashboard, listIoTAlerts, createIoTSensor, acknowledgeIoTAlert,
  SENSOR_TYPES,
} from '@/api/iot'
import type { IoTSensor, IoTAlert } from '@/api/iot'
import { getSites } from '@/api/sites'
import { usePermission } from '@/hooks/usePermission'
import { PageHeader } from '@/components/common/PageHeader'

// ── constants / helpers ───────────────────────────────────────────────────────

const STATUS_CONFIG: Record<string, { color: string; icon: React.ReactNode; label: string }> = {
  normal:   { color: '#00E396', icon: <CheckCircleIcon sx={{ fontSize: 14 }} />,   label: 'Normal'   },
  warning:  { color: '#FF9800', icon: <WarningAmberIcon sx={{ fontSize: 14 }} />, label: 'Warning'  },
  critical: { color: '#FF4560', icon: <ErrorIcon sx={{ fontSize: 14 }} />,         label: 'Critical' },
  offline:  { color: '#9E9E9E', icon: <SignalWifiOffIcon sx={{ fontSize: 14 }} />, label: 'Offline'  },
  unknown:  { color: '#9E9E9E', icon: <SignalWifiOffIcon sx={{ fontSize: 14 }} />, label: 'Unknown'  },
}

const SEV_COLOR: Record<string, string> = {
  critical: '#FF4560', high: '#FF4560', medium: '#FF9800', low: '#00E396', info: '#6C63FF',
}

function sensorTypeLabel(type: string) {
  return SENSOR_TYPES.find(t => t.value === type)?.label ?? type
}

function relativeTime(iso: string | null) {
  if (!iso) return 'Never'
  const diff = Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  return `${Math.floor(diff / 3600)}h ago`
}

// ── Sensor Card ───────────────────────────────────────────────────────────────
function SensorCard({ sensor, onViewChart }: { sensor: IoTSensor; onViewChart: (s: IoTSensor) => void }) {
  const st = STATUS_CONFIG[sensor.current_status] ?? STATUS_CONFIG.unknown
  const val = sensor.last_reading_value

  // Percentage bar for tank/generator/humidity
  const showBar = ['water_tank', 'generator', 'humidity'].includes(sensor.sensor_type)
  const barPct = showBar && val != null ? Math.min(Math.max(val, 0), 100) : 0
  const barColor = sensor.current_status === 'critical' ? '#FF4560'
    : sensor.current_status === 'warning' ? '#FF9800' : '#00E396'

  return (
    <Paper
      sx={{
        p: 1.75,
        height: '100%',
        border: `1px solid ${st.color}40`,
        borderRadius: 2,
        background: `linear-gradient(135deg, ${st.color}0d 0%, transparent 100%)`,
        cursor: 'pointer',
        transition: 'all 0.2s',
        '&:hover': { boxShadow: `0 4px 20px ${st.color}30`, transform: 'translateY(-1px)' },
      }}
      onClick={() => onViewChart(sensor)}
    >
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 1, mb: 1 }}>
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: '0.08em' }}>
            {sensorTypeLabel(sensor.sensor_type)}
          </Typography>
          <Typography variant="subtitle2" sx={{ fontWeight: 700, fontSize: '0.82rem', lineHeight: 1.3 }} noWrap>
            {sensor.name}
          </Typography>
          {sensor.site_name && (
            <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.62rem' }} noWrap>
              {sensor.site_name}{sensor.location ? ` · ${sensor.location}` : ''}
            </Typography>
          )}
        </Box>
        <Chip
          size="small"
          icon={st.icon as any}
          label={st.label}
          sx={{
            height: 20, fontSize: '0.6rem', fontWeight: 700,
            bgcolor: `${st.color}20`, color: st.color,
            border: `1px solid ${st.color}40`,
            '& .MuiChip-icon': { color: st.color + ' !important' },
          }}
        />
      </Box>

      {/* Value */}
      <Box sx={{ display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', mb: showBar ? 1 : 0.5 }}>
        <Box>
          <Typography variant="h5" sx={{ fontWeight: 800, lineHeight: 1, color: st.color }}>
            {val != null ? val.toFixed(1) : '—'}
          </Typography>
          <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.65rem' }}>
            {sensor.unit || ''}
          </Typography>
        </Box>
        {sensor.open_alerts > 0 && (
          <Chip size="small" label={`${sensor.open_alerts} alert${sensor.open_alerts > 1 ? 's' : ''}`}
            sx={{ height: 18, fontSize: '0.58rem', bgcolor: 'rgba(255,69,96,0.15)', color: '#FF4560' }} />
        )}
      </Box>

      {/* Bar for percentage-type sensors */}
      {showBar && (
        <Box sx={{ mb: 0.75 }}>
          <Box sx={{ height: 5, bgcolor: 'rgba(255,255,255,0.08)', borderRadius: 1, overflow: 'hidden' }}>
            <Box sx={{ width: `${barPct}%`, height: '100%', bgcolor: barColor, borderRadius: 1, transition: 'width 0.5s' }} />
          </Box>
        </Box>
      )}

      <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.6rem' }}>
        Updated {relativeTime(sensor.last_reading_at)}
      </Typography>
    </Paper>
  )
}

// ── Add Sensor Dialog ─────────────────────────────────────────────────────────
function AddSensorDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const qc = useQueryClient()
  const [form, setForm] = useState({
    name: '', sensor_type: 'temperature', unit: '',
    location: '', site_id: '', description: '',
    threshold_warning_low: '', threshold_warning_high: '',
    threshold_critical_low: '', threshold_critical_high: '',
    expected_interval_seconds: '300',
  })
  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const create = useMutation({
    mutationFn: () => createIoTSensor({
      name: form.name, sensor_type: form.sensor_type,
      unit: form.unit || undefined,
      location: form.location || undefined,
      description: form.description || undefined,
      site_id: form.site_id || undefined,
      threshold_warning_low:  form.threshold_warning_low  ? +form.threshold_warning_low  : undefined,
      threshold_warning_high: form.threshold_warning_high ? +form.threshold_warning_high : undefined,
      threshold_critical_low:  form.threshold_critical_low  ? +form.threshold_critical_low  : undefined,
      threshold_critical_high: form.threshold_critical_high ? +form.threshold_critical_high : undefined,
      expected_interval_seconds: +form.expected_interval_seconds || 300,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['iot-dashboard'] }); onClose() },
  })

  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setForm(f => ({ ...f, [k]: e.target.value }))

  const defaultUnit = SENSOR_TYPES.find(t => t.value === form.sensor_type)?.unit ?? ''

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Add IoT Sensor</DialogTitle>
      <DialogContent>
        <Stack spacing={2} sx={{ mt: 1 }}>
          <TextField label="Sensor Name" value={form.name} onChange={set('name')} required fullWidth size="small" />
          <TextField label="Type" value={form.sensor_type} onChange={set('sensor_type')} select required fullWidth size="small">
            {SENSOR_TYPES.map(t => <MenuItem key={t.value} value={t.value}>{t.label}</MenuItem>)}
          </TextField>
          <TextField label={`Unit (default: ${defaultUnit})`} value={form.unit} onChange={set('unit')} fullWidth size="small"
            placeholder={defaultUnit} />
          <TextField label="Site" value={form.site_id} onChange={set('site_id')} select fullWidth size="small">
            <MenuItem value="">— No site —</MenuItem>
            {(sites as any[]).map((s: any) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </TextField>
          <TextField label="Location / Description" value={form.location} onChange={set('location')} fullWidth size="small" />
          <Divider />
          <Typography variant="caption" sx={{ color: 'text.secondary', fontWeight: 600 }}>
            Thresholds (leave blank to disable)
          </Typography>
          <Box sx={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 1.5 }}>
            <TextField label="Warning Low"  value={form.threshold_warning_low}  onChange={set('threshold_warning_low')}  size="small" type="number" />
            <TextField label="Warning High" value={form.threshold_warning_high} onChange={set('threshold_warning_high')} size="small" type="number" />
            <TextField label="Critical Low"  value={form.threshold_critical_low}  onChange={set('threshold_critical_low')}  size="small" type="number" />
            <TextField label="Critical High" value={form.threshold_critical_high} onChange={set('threshold_critical_high')} size="small" type="number" />
          </Box>
          <TextField label="Reporting interval (seconds)" value={form.expected_interval_seconds}
            onChange={set('expected_interval_seconds')} size="small" type="number" />
        </Stack>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => create.mutate()} disabled={!form.name || create.isPending}
          startIcon={<AddIcon />}>
          {create.isPending ? 'Adding…' : 'Add Sensor'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

// ── Alerts Table ──────────────────────────────────────────────────────────────
function AlertsTab() {
  const qc = useQueryClient()
  const { data: alerts = [], isLoading } = useQuery({
    queryKey: ['iot-alerts'],
    queryFn: () => listIoTAlerts({ status_filter: 'open' }),
  })
  const ack = useMutation({
    mutationFn: acknowledgeIoTAlert,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['iot-alerts'] }),
  })

  if (isLoading) return <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}><CircularProgress size={32} /></Box>
  if (!alerts.length) return <Alert severity="success" sx={{ mt: 2 }}>No open IoT alerts.</Alert>

  return (
    <Table size="small" sx={{ mt: 1 }}>
      <TableHead>
        <TableRow>
          <TableCell>Sensor</TableCell>
          <TableCell>Site</TableCell>
          <TableCell>Message</TableCell>
          <TableCell>Value</TableCell>
          <TableCell>Severity</TableCell>
          <TableCell>Time</TableCell>
          <TableCell />
        </TableRow>
      </TableHead>
      <TableBody>
        {(alerts as IoTAlert[]).map((a) => (
          <TableRow key={a.id}>
            <TableCell>
              <Typography variant="caption" sx={{ fontWeight: 600 }}>{a.sensor_name}</Typography>
              <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.6rem', display: "block" }}>
                {sensorTypeLabel(a.sensor_type)}
              </Typography>
            </TableCell>
            <TableCell><Typography variant="caption">{a.site_name ?? '—'}</Typography></TableCell>
            <TableCell><Typography variant="caption" sx={{ maxWidth: 250, display: 'block' }} noWrap>{a.message}</Typography></TableCell>
            <TableCell>
              <Typography variant="caption" sx={{ fontWeight: 700, color: SEV_COLOR[a.severity] }}>
                {a.value != null ? `${a.value} ${a.unit ?? ''}` : '—'}
              </Typography>
            </TableCell>
            <TableCell>
              <Chip size="small" label={a.severity.toUpperCase()}
                sx={{ height: 18, fontSize: '0.58rem', fontWeight: 700,
                  bgcolor: `${SEV_COLOR[a.severity]}20`, color: SEV_COLOR[a.severity] }} />
            </TableCell>
            <TableCell><Typography variant="caption" sx={{ color: 'text.secondary' }}>{relativeTime(a.created_at)}</Typography></TableCell>
            <TableCell>
              <Tooltip title="Acknowledge">
                <span>
                  <IconButton size="small" onClick={() => ack.mutate(a.id)} disabled={ack.isPending}>
                    <CheckIcon sx={{ fontSize: 14 }} />
                  </IconButton>
                </span>
              </Tooltip>
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  )
}

// ── Main page ─────────────────────────────────────────────────────────────────
export default function IoTPage() {
  const canWrite = usePermission('iot:write')
  const [tab, setTab] = useState(0)
  const [addOpen, setAddOpen] = useState(false)
  const [chartSensor, setChartSensor] = useState<IoTSensor | null>(null)

  const { data, isLoading } = useQuery({
    queryKey: ['iot-dashboard'],
    queryFn:  () => getIoTDashboard(),
    refetchInterval: 30_000,
  })

  const summary = data?.summary
  const sensors = data?.sensors ?? []

  // Group by type
  const byType: Record<string, IoTSensor[]> = {}
  sensors.forEach(s => {
    byType[s.sensor_type] = byType[s.sensor_type] ?? []
    byType[s.sensor_type].push(s)
  })

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
      <PageHeader pageKey="iot" />
      {/* Header */}
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2 }}>
        <SensorsIcon sx={{ color: 'primary.main', fontSize: 28 }} />
        <Box sx={{ flex: 1 }}>
          <Typography variant="caption" sx={{ color: 'text.secondary' }}>IoT Sensor Monitoring</Typography>
        </Box>
        {canWrite && (
          <Button variant="contained" size="small" startIcon={<AddIcon />} onClick={() => setAddOpen(true)}>
            Add Sensor
          </Button>
        )}
      </Box>

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 6 }}><CircularProgress /></Box>
      ) : (
        <>
          {/* KPI Summary */}
          {summary && (
            <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
              {[
                { label: 'Total Sensors', value: summary.total,       color: '#6C63FF' },
                { label: 'Normal',        value: summary.normal,      color: '#00E396' },
                { label: 'Warning',       value: summary.warning,     color: '#FF9800' },
                { label: 'Critical',      value: summary.critical,    color: '#FF4560' },
                { label: 'Offline',       value: summary.offline,     color: '#9E9E9E' },
                { label: 'Open Alerts',   value: summary.open_alerts, color: '#FF4560' },
              ].map(({ label, value, color }) => (
                <Paper key={label} sx={{
                  flex: 1, minWidth: 90, p: 1.5,
                  background: `linear-gradient(135deg, ${color}15 0%, transparent 100%)`,
                  border: `1px solid ${color}35`, borderRadius: 2,
                }}>
                  <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.62rem', textTransform: 'uppercase', letterSpacing: '0.07em', display: 'block' }}>{label}</Typography>
                  <Typography variant="h5" sx={{ fontWeight: 800, color }}>{value}</Typography>
                </Paper>
              ))}
            </Box>
          )}

          {/* Tabs */}
          <Paper sx={{ px: 1 }}>
            <Tabs value={tab} onChange={(_, v) => setTab(v)} textColor="inherit" indicatorColor="primary">
              <Tab label="Sensor Dashboard" />
              <Tab label={`Alerts${summary?.open_alerts ? ` (${summary.open_alerts})` : ''}`} />
            </Tabs>
          </Paper>

          {tab === 0 && (
            <Box>
              {sensors.length === 0 ? (
                <Paper sx={{ p: 4, textAlign: 'center' }}>
                  <SensorsIcon sx={{ fontSize: 48, color: 'text.disabled', mb: 1 }} />
                  <Typography variant="body2" color="text.secondary">No IoT sensors configured.</Typography>
                  {canWrite && <Button sx={{ mt: 2 }} variant="outlined" onClick={() => setAddOpen(true)}>Add First Sensor</Button>}
                </Paper>
              ) : (
                Object.entries(byType).map(([type, sensorList]) => (
                  <Box key={type} sx={{ mb: 3 }}>
                    <Typography variant="caption" sx={{
                      display: 'flex', alignItems: 'center', gap: 1, mb: 1.5,
                      fontWeight: 700, letterSpacing: '0.1em', textTransform: 'uppercase',
                      color: 'text.secondary', fontSize: '0.65rem',
                    }}>
                      {SENSOR_TYPES.find(t => t.value === type)?.icon} {sensorTypeLabel(type)}
                      <Chip size="small" label={sensorList.length} sx={{ height: 16, fontSize: '0.58rem' }} />
                    </Typography>
                    <Grid container spacing={1.5}>
                      {sensorList.map(s => (
                        <Grid size={{ xs: 12, sm: 6, md: 4, lg: 3 }} key={s.id}>
                          <SensorCard sensor={s} onViewChart={setChartSensor} />
                        </Grid>
                      ))}
                    </Grid>
                  </Box>
                ))
              )}
            </Box>
          )}

          {tab === 1 && <AlertsTab />}
        </>
      )}

      {/* Sensor history dialog */}
      {chartSensor && (
        <Dialog open maxWidth="md" fullWidth onClose={() => setChartSensor(null)}>
          <DialogTitle>
            {chartSensor.name}
            <Typography variant="caption" sx={{ display: 'block', color: 'text.secondary' }}>
              {sensorTypeLabel(chartSensor.sensor_type)} · {chartSensor.unit}
            </Typography>
          </DialogTitle>
          <DialogContent>
            <Stack spacing={1.5}>
              <Box sx={{ display: 'flex', gap: 3, flexWrap: 'wrap' }}>
                <Box>
                  <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.62rem' }}>Current Value</Typography>
                  <Typography variant="h4" sx={{ fontWeight: 800, color: STATUS_CONFIG[chartSensor.current_status]?.color }}>
                    {chartSensor.last_reading_value != null ? chartSensor.last_reading_value.toFixed(2) : '—'}
                    <Typography component="span" variant="caption" sx={{ ml: 0.5, color: 'text.secondary' }}>{chartSensor.unit}</Typography>
                  </Typography>
                </Box>
                <Box>
                  <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.62rem' }}>Last Updated</Typography>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{relativeTime(chartSensor.last_reading_at)}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.62rem' }}>Status</Typography>
                  <Chip size="small" label={STATUS_CONFIG[chartSensor.current_status]?.label ?? 'Unknown'}
                    sx={{ bgcolor: `${STATUS_CONFIG[chartSensor.current_status]?.color}20`, color: STATUS_CONFIG[chartSensor.current_status]?.color }} />
                </Box>
              </Box>
              {(chartSensor.threshold_warning_low != null || chartSensor.threshold_warning_high != null ||
                chartSensor.threshold_critical_low != null || chartSensor.threshold_critical_high != null) && (
                <Box>
                  <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.62rem', display: 'block', mb: 0.5 }}>Thresholds</Typography>
                  <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap' }}>
                    {chartSensor.threshold_critical_low != null && <Chip size="small" label={`Crit. Low: ${chartSensor.threshold_critical_low}`} sx={{ bgcolor: 'rgba(255,69,96,0.12)', color: '#FF4560', fontSize: '0.65rem' }} />}
                    {chartSensor.threshold_critical_high != null && <Chip size="small" label={`Crit. High: ${chartSensor.threshold_critical_high}`} sx={{ bgcolor: 'rgba(255,69,96,0.12)', color: '#FF4560', fontSize: '0.65rem' }} />}
                    {chartSensor.threshold_warning_low != null && <Chip size="small" label={`Warn. Low: ${chartSensor.threshold_warning_low}`} sx={{ bgcolor: 'rgba(255,152,0,0.12)', color: '#FF9800', fontSize: '0.65rem' }} />}
                    {chartSensor.threshold_warning_high != null && <Chip size="small" label={`Warn. High: ${chartSensor.threshold_warning_high}`} sx={{ bgcolor: 'rgba(255,152,0,0.12)', color: '#FF9800', fontSize: '0.65rem' }} />}
                  </Box>
                </Box>
              )}
            </Stack>
          </DialogContent>
          <DialogActions>
            <Button onClick={() => setChartSensor(null)}>Close</Button>
          </DialogActions>
        </Dialog>
      )}

      <AddSensorDialog open={addOpen} onClose={() => setAddOpen(false)} />
    </Box>
  )
}

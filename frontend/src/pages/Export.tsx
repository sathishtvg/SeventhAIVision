import { useState } from 'react'
import {
  Box, Grid, Typography, Button, TextField, MenuItem,
  CircularProgress, Alert as MuiAlert,
} from '@mui/material'
import DownloadIcon from '@mui/icons-material/Download'
import { GlassCard } from '@/components/common/GlassCard'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { exportAlerts, exportIncidents, exportDetections, exportAuditLogs } from '@/api/exports'
import { PageHeader } from '@/components/common/PageHeader'

const SEVERITIES = ['', 'info', 'low', 'medium', 'high', 'critical']
const ALERT_STATUSES = ['', 'open', 'acknowledged', 'resolved', 'dismissed']
const INCIDENT_STATUSES = ['', 'open', 'investigating', 'resolved', 'closed']
const MODULE_TYPES = ['', 'lpr', 'face', 'intrusion', 'ppe', 'crowd', 'fire_smoke', 'weapon', 'behavior']

function DateRangeFields({
  dateFrom, dateTo, onFromChange, onToChange,
}: {
  dateFrom: string; dateTo: string
  onFromChange: (v: string) => void; onToChange: (v: string) => void
}) {
  return (
    <>
      <TextField
        label="From"
        type="date"
        value={dateFrom}
        onChange={(e) => onFromChange(e.target.value)}
        size="small"
        fullWidth
        slotProps={{ inputLabel: { shrink: true } }}
      />
      <TextField
        label="To"
        type="date"
        value={dateTo}
        onChange={(e) => onToChange(e.target.value)}
        size="small"
        fullWidth
        slotProps={{ inputLabel: { shrink: true } }}
      />
    </>
  )
}

// ──────────────────────────────────────────────────────────
// Generic export card
// ──────────────────────────────────────────────────────────

function ExportCard({
  title, description, permission, onExport, children,
}: {
  title: string
  description: string
  permission: string
  onExport: () => Promise<void>
  children?: React.ReactNode
}) {
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const handleExport = async () => {
    setLoading(true)
    setError(null)
    try {
      await onExport()
    } catch (err) {
      setError(String(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <PermissionGuard permission={permission}>
      <GlassCard sx={{ p: 3, height: '100%', display: 'flex', flexDirection: 'column', gap: 2 }}>
        <Box>
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>{title}</Typography>
          <Typography variant="caption" color="text.secondary">{description}</Typography>
        </Box>

        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, flex: 1 }}>
          {children}
        </Box>

        {error && <MuiAlert severity="error" sx={{ py: 0 }}>{error}</MuiAlert>}

        <Button
          variant="outlined"
          startIcon={loading ? <CircularProgress size={14} /> : <DownloadIcon />}
          onClick={handleExport}
          disabled={loading}
          fullWidth
        >
          {loading ? 'Exporting…' : 'Download CSV'}
        </Button>
      </GlassCard>
    </PermissionGuard>
  )
}

// ──────────────────────────────────────────────────────────
// Page
// ──────────────────────────────────────────────────────────

export default function Export() {
  // Alerts filters
  const [aDateFrom, setADateFrom] = useState('')
  const [aDateTo, setADateTo] = useState('')
  const [aSeverity, setASeverity] = useState('')
  const [aStatus, setAStatus] = useState('')
  const [aModule, setAModule] = useState('')

  // Incidents filters
  const [iDateFrom, setIDateFrom] = useState('')
  const [iDateTo, setIDateTo] = useState('')
  const [iSeverity, setISeverity] = useState('')
  const [iStatus, setIStatus] = useState('')

  // Detections filters
  const [dDateFrom, setDDateFrom] = useState('')
  const [dDateTo, setDDateTo] = useState('')
  const [dModule, setDModule] = useState('')

  // Audit filters
  const [auDateFrom, setAuDateFrom] = useState('')
  const [auDateTo, setAuDateTo] = useState('')
  const [auAction, setAuAction] = useState('')

  return (
    <Box>
      <PageHeader pageKey="export" />
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Download up to 10,000 rows per export. Apply filters to narrow the result.
      </Typography>

      <Grid container spacing={3}>
        {/* Alerts */}
        <Grid size={{ xs: 12, md: 6 }}>
          <ExportCard
            title="Alerts"
            description="Export alert records with camera, severity, status, and timestamps."
            permission="alert:read"
            onExport={() =>
              exportAlerts({
                date_from: aDateFrom || undefined,
                date_to: aDateTo || undefined,
                severity: aSeverity || undefined,
                status: aStatus || undefined,
                module_type: aModule || undefined,
              })
            }
          >
            <DateRangeFields dateFrom={aDateFrom} dateTo={aDateTo} onFromChange={setADateFrom} onToChange={setADateTo} />
            <TextField select label="Severity" value={aSeverity} onChange={(e) => setASeverity(e.target.value)} size="small" fullWidth>
              {SEVERITIES.map((s) => <MenuItem key={s} value={s}>{s || 'All'}</MenuItem>)}
            </TextField>
            <TextField select label="Status" value={aStatus} onChange={(e) => setAStatus(e.target.value)} size="small" fullWidth>
              {ALERT_STATUSES.map((s) => <MenuItem key={s} value={s}>{s || 'All'}</MenuItem>)}
            </TextField>
            <TextField select label="Module" value={aModule} onChange={(e) => setAModule(e.target.value)} size="small" fullWidth>
              {MODULE_TYPES.map((m) => <MenuItem key={m} value={m}>{m || 'All'}</MenuItem>)}
            </TextField>
          </ExportCard>
        </Grid>

        {/* Incidents */}
        <Grid size={{ xs: 12, md: 6 }}>
          <ExportCard
            title="Incidents"
            description="Export incident records with title, severity, status, and resolution time."
            permission="incident:read"
            onExport={() =>
              exportIncidents({
                date_from: iDateFrom || undefined,
                date_to: iDateTo || undefined,
                severity: iSeverity || undefined,
                status: iStatus || undefined,
              })
            }
          >
            <DateRangeFields dateFrom={iDateFrom} dateTo={iDateTo} onFromChange={setIDateFrom} onToChange={setIDateTo} />
            <TextField select label="Severity" value={iSeverity} onChange={(e) => setISeverity(e.target.value)} size="small" fullWidth>
              {SEVERITIES.filter((s) => s !== 'info').map((s) => <MenuItem key={s} value={s}>{s || 'All'}</MenuItem>)}
            </TextField>
            <TextField select label="Status" value={iStatus} onChange={(e) => setIStatus(e.target.value)} size="small" fullWidth>
              {INCIDENT_STATUSES.map((s) => <MenuItem key={s} value={s}>{s || 'All'}</MenuItem>)}
            </TextField>
          </ExportCard>
        </Grid>

        {/* Detections */}
        <Grid size={{ xs: 12, md: 6 }}>
          <ExportCard
            title="Detections"
            description="Export raw detection records — one row per AI detection event."
            permission="detection:read"
            onExport={() =>
              exportDetections({
                date_from: dDateFrom || undefined,
                date_to: dDateTo || undefined,
                module_type: dModule || undefined,
              })
            }
          >
            <DateRangeFields dateFrom={dDateFrom} dateTo={dDateTo} onFromChange={setDDateFrom} onToChange={setDDateTo} />
            <TextField select label="Module" value={dModule} onChange={(e) => setDModule(e.target.value)} size="small" fullWidth>
              {MODULE_TYPES.map((m) => <MenuItem key={m} value={m}>{m || 'All'}</MenuItem>)}
            </TextField>
          </ExportCard>
        </Grid>

        {/* Audit Logs */}
        <Grid size={{ xs: 12, md: 6 }}>
          <ExportCard
            title="Audit Logs"
            description="Export immutable audit trail for compliance and forensic review."
            permission="audit:read"
            onExport={() =>
              exportAuditLogs({
                date_from: auDateFrom || undefined,
                date_to: auDateTo || undefined,
                action: auAction || undefined,
              })
            }
          >
            <DateRangeFields dateFrom={auDateFrom} dateTo={auDateTo} onFromChange={setAuDateFrom} onToChange={setAuDateTo} />
            <TextField
              label="Action keyword"
              value={auAction}
              onChange={(e) => setAuAction(e.target.value)}
              size="small"
              fullWidth
              placeholder="e.g. login, create, update"
            />
          </ExportCard>
        </Grid>
      </Grid>
    </Box>
  )
}

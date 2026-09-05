/**
 * Alert Rules — which detections raise an alert, at what severity, and which
 * of those open an incident.
 *
 * These values were hardcoded constants in eleven worker task files until
 * Module 14 moved them into the database. The workers already read the
 * tenant's overrides through a 30s cache, so a change here reaches the
 * detection pipeline within about half a minute without a redeploy.
 *
 * Its own route and its own window on purpose: an admin tuning alert noise is
 * doing that while watching the wall or the alert list, not instead of.
 */
import { useMemo, useState } from 'react'
import {
  Box, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Select, MenuItem, Switch, Chip, IconButton, Tooltip, Typography, Paper,
  Alert as MuiAlert, Skeleton, Snackbar,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import RestartAltIcon from '@mui/icons-material/RestartAlt'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { openInNewWindow } from '@/lib/popoutWindow'
import { usePermission } from '@/hooks/usePermission'
import {
  listAlertRules, getAlertRuleCatalogue, upsertAlertRule, resetAlertRule,
  type EffectiveAlertRule,
} from '@/api/alertRules'

const SEVERITY_COLOR: Record<string, string> = {
  info: '#6C63FF',
  low: '#00D9C0',
  medium: '#FF9800',
  high: '#FF6B35',
  critical: '#FF4560',
}

const MODULE_LABELS: Record<string, string> = {
  lpr: 'License Plate',
  face: 'Face Recognition',
  intrusion: 'Intrusion',
  crowd: 'Crowd Density',
  weapon: 'Weapon',
  behavior: 'Behavior',
  fire_smoke: 'Fire / Smoke',
  ppe: 'PPE',
  tampering: 'Camera Tampering',
  abandoned: 'Abandoned Object',
  fall: 'Slip / Fall',
}

/** Turns `blocklist_hit` into `Blocklist hit` — the trigger keys are stable
 *  machine identifiers, so they are prettified for display rather than
 *  renamed at the source. */
const prettyTrigger = (k: string) =>
  k.replace(/_/g, ' ').replace(/^./, (c) => c.toUpperCase())

function SeveritySelect({
  value, options, disabled, onChange,
}: {
  value: string
  options: string[]
  disabled: boolean
  onChange: (v: string) => void
}) {
  return (
    <Select
      size="small"
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      sx={{
        minWidth: 116,
        color: SEVERITY_COLOR[value] ?? 'inherit',
        fontWeight: 600,
      }}
    >
      {options.map((s) => (
        <MenuItem key={s} value={s} sx={{ color: SEVERITY_COLOR[s], fontWeight: 600 }}>
          {s}
        </MenuItem>
      ))}
    </Select>
  )
}

export default function AlertRules() {
  const qc = useQueryClient()
  const canManage = usePermission('alert_rule:manage')
  const [toast, setToast] = useState<string | null>(null)

  const { data: rules, isLoading } = useQuery({
    queryKey: ['alert-rules'],
    queryFn: listAlertRules,
  })
  const { data: catalogue } = useQuery({
    queryKey: ['alert-rule-catalogue'],
    queryFn: getAlertRuleCatalogue,
  })

  const severities = catalogue?.severities ?? ['info', 'low', 'medium', 'high', 'critical']

  const save = useMutation({
    mutationFn: (r: EffectiveAlertRule) =>
      upsertAlertRule(r.module_type, r.trigger_key, {
        severity: r.severity,
        create_incident: r.create_incident,
        incident_severity: r.incident_severity,
        is_enabled: r.is_enabled,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alert-rules'] })
      setToast('Saved — workers pick this up within ~30s')
    },
    onError: (e: any) => setToast(e.response?.data?.detail ?? 'Save failed'),
  })

  const reset = useMutation({
    mutationFn: (r: EffectiveAlertRule) => resetAlertRule(r.module_type, r.trigger_key),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alert-rules'] })
      setToast('Reverted to the shipped default')
    },
    onError: (e: any) => setToast(e.response?.data?.detail ?? 'Reset failed'),
  })

  // Group by module so the table reads as "this module's behaviour", which is
  // how someone tuning noise actually thinks about it.
  const byModule = useMemo(() => {
    const m = new Map<string, EffectiveAlertRule[]>()
    for (const r of rules ?? []) {
      if (!m.has(r.module_type)) m.set(r.module_type, [])
      m.get(r.module_type)!.push(r)
    }
    return [...m.entries()].sort((a, b) => a[0].localeCompare(b[0]))
  }, [rules])

  const overriddenCount = (rules ?? []).filter((r) => r.is_overridden).length

  return (
    <Box>
      <PageHeader pageKey="alert-rules"
        action={
          <Tooltip title="Open Alert Rules in its own window">
            <IconButton size="small" onClick={() => openInNewWindow('/alert-rules')}>
              <OpenInNewIcon fontSize="small" />
            </IconButton>
          </Tooltip>
        }
      />

      {!canManage && (
        <MuiAlert severity="info" sx={{ mb: 2 }}>
          You can view these rules but not change them — `alert_rule:manage` is required.
        </MuiAlert>
      )}

      <MuiAlert severity="info" sx={{ mb: 2 }}>
        Changes reach the detection workers within about 30 seconds — they read these
        through a short-lived cache, so there is no redeploy and no restart.
        {overriddenCount > 0 && ` ${overriddenCount} rule${overriddenCount === 1 ? '' : 's'} currently differ from the shipped defaults.`}
      </MuiAlert>

      {isLoading ? (
        <Skeleton variant="rounded" height={360} />
      ) : (
        <Stack spacing={2}>
          {byModule.map(([moduleType, moduleRules]) => (
            <GlassCard key={moduleType} sx={{ p: 0, overflow: 'hidden' }}>
              <Box sx={{ px: 2, py: 1.5 }}>
                <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>
                  {MODULE_LABELS[moduleType] ?? moduleType}
                </Typography>
              </Box>
              <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
                <Table size="small">
                  <TableHead>
                    <TableRow>
                      <TableCell>Trigger</TableCell>
                      <TableCell>Alert severity</TableCell>
                      <TableCell>Opens incident</TableCell>
                      <TableCell>Incident severity</TableCell>
                      <TableCell>Enabled</TableCell>
                      <TableCell align="right">Default</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {moduleRules.map((r) => (
                      <TableRow key={`${r.module_type}/${r.trigger_key}`} hover>
                        <TableCell>
                          <Stack direction="row" spacing={1} sx={{ alignItems: 'center' }}>
                            <Typography variant="body2">{prettyTrigger(r.trigger_key)}</Typography>
                            {r.is_overridden && (
                              <Tooltip title="Changed from the shipped default">
                                <Chip size="small" label="custom" color="secondary" variant="outlined" />
                              </Tooltip>
                            )}
                          </Stack>
                        </TableCell>
                        <TableCell>
                          <SeveritySelect
                            value={r.severity}
                            options={severities}
                            disabled={!canManage || save.isPending}
                            onChange={(v) => save.mutate({ ...r, severity: v })}
                          />
                        </TableCell>
                        <TableCell>
                          <Switch
                            size="small"
                            checked={r.create_incident}
                            disabled={!canManage || save.isPending}
                            onChange={(e) =>
                              save.mutate({
                                ...r,
                                create_incident: e.target.checked,
                                // An incident needs a severity; seed it from the
                                // alert's so enabling the toggle can't produce an
                                // incomplete rule the API would reject.
                                incident_severity: e.target.checked
                                  ? (r.incident_severity ?? r.severity)
                                  : null,
                              })
                            }
                          />
                        </TableCell>
                        <TableCell>
                          {r.create_incident ? (
                            <SeveritySelect
                              value={r.incident_severity ?? r.severity}
                              options={severities}
                              disabled={!canManage || save.isPending}
                              onChange={(v) => save.mutate({ ...r, incident_severity: v })}
                            />
                          ) : (
                            <Typography variant="caption" color="text.disabled">—</Typography>
                          )}
                        </TableCell>
                        <TableCell>
                          <Switch
                            size="small"
                            checked={r.is_enabled}
                            disabled={!canManage || save.isPending}
                            onChange={(e) => save.mutate({ ...r, is_enabled: e.target.checked })}
                          />
                        </TableCell>
                        <TableCell align="right">
                          {r.is_overridden ? (
                            <Tooltip
                              title={`Revert to default: ${r.default_severity}${
                                r.default_create_incident ? ' + incident' : ', no incident'
                              }`}
                            >
                              <span>
                                <IconButton
                                  size="small"
                                  disabled={!canManage || reset.isPending}
                                  onClick={() => reset.mutate(r)}
                                >
                                  <RestartAltIcon fontSize="small" />
                                </IconButton>
                              </span>
                            </Tooltip>
                          ) : (
                            <Typography variant="caption" color="text.disabled">default</Typography>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </TableContainer>
            </GlassCard>
          ))}
        </Stack>
      )}

      <Snackbar
        open={!!toast}
        autoHideDuration={4000}
        onClose={() => setToast(null)}
        message={toast ?? ''}
      />
    </Box>
  )
}

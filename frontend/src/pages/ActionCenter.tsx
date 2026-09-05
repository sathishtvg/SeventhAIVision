/**
 * Action Center — role-aware "what needs my attention right now?" feed.
 * Control-room / supervisor roles see the operational items needing a human
 * (guards to contact, unacked alerts, overdue checkpoints, pending approvals,
 * offline cameras); a security guard sees their own pending duties. The
 * backend self-scopes by role, so this page just renders whatever it returns.
 */
import { Box, Typography, Stack, Button, IconButton, Skeleton, Tooltip } from '@mui/material'
import PhoneInTalkIcon from '@mui/icons-material/PhoneInTalk'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import RouteIcon from '@mui/icons-material/Route'
import FactCheckIcon from '@mui/icons-material/FactCheck'
import VideocamOffIcon from '@mui/icons-material/VideocamOff'
import WrongLocationIcon from '@mui/icons-material/WrongLocation'
import LoginIcon from '@mui/icons-material/Login'
import ReportProblemIcon from '@mui/icons-material/ReportProblem'
import DescriptionIcon from '@mui/icons-material/Description'
import TaskAltIcon from '@mui/icons-material/TaskAlt'
import LaunchIcon from '@mui/icons-material/Launch'
import ShieldIcon from '@mui/icons-material/Shield'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import FullscreenIcon from '@mui/icons-material/Fullscreen'
import { useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { getActionCenter, type ActionItem } from '@/api/actionCenter'
import { AlertResponseDialog, type AlertSummary } from '@/components/common/AlertResponseDialog'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { fadeUpSx, useCountUp } from '@/lib/motion'
import { openInNewWindow } from '@/lib/popoutWindow'
import { useKioskToggle } from '@/hooks/useKioskToggle'

const SEV_COLOR: Record<ActionItem['severity'], string> = {
  critical: '#FF4560',
  high: '#FF9800',
  medium: '#FFC107',
  low: '#6C63FF',
}

const CATEGORY_ICON: Record<ActionItem['category'], React.ReactNode> = {
  contact_guard: <PhoneInTalkIcon />,
  ack_alert: <WarningAmberIcon />,
  overdue_checkpoint: <RouteIcon />,
  pending_approval: <FactCheckIcon />,
  camera_offline: <VideocamOffIcon />,
  geofence_flag: <WrongLocationIcon />,
  check_in: <LoginIcon />,
  patrol_due: <RouteIcon />,
  respond_incident: <ReportProblemIcon />,
  doc_expiry: <DescriptionIcon />,
}

function hexToRgb(hex: string) {
  const m = hex.replace('#', '').match(/.{2}/g)
  return m ? m.map((v) => parseInt(v, 16)).join(',') : '108,99,255'
}

function KpiCard({ label, value, color }: { label: string; value: number | undefined; color: string }) {
  const rgb = hexToRgb(color)
  const v = useCountUp(value)
  return (
    <GlassCard variant="glow" sx={{ p: 2.5, flex: '1 1 160px', minWidth: 0, borderColor: `rgba(${rgb},0.18)` }}>
      <Typography sx={{ color: `rgba(${rgb},0.85)`, textTransform: 'uppercase', letterSpacing: '0.1em', fontSize: '0.62rem', fontWeight: 700, mb: 0.75 }}>
        {label}
      </Typography>
      {value === undefined ? (
        <Skeleton width={50} height={40} sx={{ bgcolor: `rgba(${rgb},0.08)` }} />
      ) : (
        <Typography sx={{ fontWeight: 800, fontSize: '2rem', lineHeight: 1, color, fontFamily: '"Fira Code", monospace' }}>
          {v}
        </Typography>
      )}
    </GlassCard>
  )
}

function ActionRow({ item, onNavigate, onRespond }: {
  item: ActionItem
  onNavigate: (route: string) => void
  onRespond: (alert: AlertSummary) => void
}) {
  const color = SEV_COLOR[item.severity]
  const rgb = hexToRgb(color)
  const webRoute = item.action_route && item.action_route.startsWith('/') ? item.action_route : null
  return (
    <Box sx={{
      display: 'flex', alignItems: 'center', gap: 1.5,
      py: 1.25, px: 1.5, borderRadius: '10px',
      backgroundColor: 'rgba(255,255,255,0.04)',
      borderLeft: `3px solid ${color}`,
    }}>
      <Box sx={{
        width: 38, height: 38, borderRadius: '10px', flexShrink: 0,
        background: `rgba(${rgb},0.12)`, border: `1px solid rgba(${rgb},0.28)`,
        display: 'flex', alignItems: 'center', justifyContent: 'center', color,
        '& svg': { fontSize: 20 },
      }}>
        {CATEGORY_ICON[item.category]}
      </Box>
      <Box sx={{ flex: 1, minWidth: 0 }}>
        <Typography variant="body2" noWrap sx={{ fontWeight: 700 }}>{item.title}</Typography>
        <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
          {item.subtitle}
        </Typography>
      </Box>
      <Stack direction="row" spacing={1} sx={{ flexShrink: 0 }}>
        {item.phone && (
          <Button
            component="a" href={`tel:${item.phone}`} size="small" variant="contained" color="error"
            startIcon={<PhoneInTalkIcon />} sx={{ whiteSpace: 'nowrap' }}
          >
            Call
          </Button>
        )}
        {/* Respond here rather than navigating: this is the page an operator
            works a queue from, and bouncing to /alerts loses their place. The
            "Open" button stays as the escape hatch to the full list. */}
        {item.alert && (
          <Button
            size="small" variant="contained" startIcon={<ShieldIcon />}
            sx={{ whiteSpace: 'nowrap' }} onClick={() => onRespond(item.alert as AlertSummary)}
          >
            Respond
          </Button>
        )}
        {webRoute && (
          <Button size="small" variant="outlined" endIcon={<LaunchIcon />} onClick={() => onNavigate(webRoute)}>
            Open
          </Button>
        )}
      </Stack>
    </Box>
  )
}

export default function ActionCenter() {
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [responding, setResponding] = useState<AlertSummary | null>(null)
  const { kiosk, toggleKiosk } = useKioskToggle()
  const { data, isLoading } = useQuery({
    queryKey: ['action-center'],
    queryFn: getActionCenter,
    refetchInterval: 30_000, // fallback; WS invalidation is the primary refresh
  })

  const items = data?.items ?? []

  return (
    <Box>
      <PageHeader pageKey="action-center"
        action={
          <Stack direction="row" spacing={1}>
            <Tooltip title="Send Action Center to another monitor — opens its own window already in full screen (Esc to leave full screen there)">
              <IconButton size="small" onClick={() => openInNewWindow('/action-center', { fullscreen: true })}>
                <OpenInNewIcon fontSize="small" />
              </IconButton>
            </Tooltip>
            {/* Enter-only — AppShell's focus-mode strip owns Back and Exit once
                full screen, so the two don't stack in the same corner. */}
            {!kiosk && (
              <Tooltip title="Full screen for continuous monitoring">
                <Button
                  size="small"
                  variant="outlined"
                  startIcon={<FullscreenIcon />}
                  onClick={toggleKiosk}
                >
                  Full Screen
                </Button>
              </Tooltip>
            )}
          </Stack>
        }
      />

      <Box sx={{ display: 'flex', gap: 2, mb: 2.5, flexWrap: 'wrap' }}>
        {[
          { label: 'Critical', value: data?.summary.critical, color: '#FF4560' },
          { label: 'High', value: data?.summary.high, color: '#FF9800' },
          { label: 'Total Actions', value: data?.summary.total, color: '#6C63FF' },
        ].map((k, i) => (
          <Box key={k.label} sx={{ flex: '1 1 160px', minWidth: 0, ...fadeUpSx(i) }}>
            <KpiCard label={k.label} value={k.value} color={k.color} />
          </Box>
        ))}
      </Box>

      {isLoading ? (
        <GlassCard sx={{ p: 2 }}><Skeleton height={72} /></GlassCard>
      ) : items.length === 0 ? (
        <GlassCard sx={{ p: 5, textAlign: 'center' }}>
          <TaskAltIcon sx={{ fontSize: 44, color: '#00E396', mb: 1 }} />
          <Typography variant="h6" sx={{ fontWeight: 700 }}>All clear</Typography>
          <Typography variant="body2" color="text.secondary">
            Nothing needs your attention right now.
          </Typography>
        </GlassCard>
      ) : (
        <GlassCard sx={{ p: 2 }}>
          <Stack spacing={1}>
            {items.map((item, i) => (
              <Box key={item.id} sx={fadeUpSx(i, { stepMs: 35 })}>
                <ActionRow item={item} onNavigate={navigate} onRespond={setResponding} />
              </Box>
            ))}
          </Stack>
        </GlassCard>
      )}

      {responding && (
        <AlertResponseDialog
          alert={responding}
          onClose={() => setResponding(null)}
          // Acting on the alert removes it from this feed, so refetch rather
          // than leaving a row the operator has already dealt with.
          onResolved={() => {
            setResponding(null)
            qc.invalidateQueries({ queryKey: ['action-center'] })
          }}
        />
      )}
    </Box>
  )
}

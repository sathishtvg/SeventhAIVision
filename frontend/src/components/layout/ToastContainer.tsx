import { Box, IconButton, Typography } from '@mui/material'
import CloseIcon from '@mui/icons-material/Close'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import InfoIcon from '@mui/icons-material/Info'
import WarningIcon from '@mui/icons-material/Warning'
import ErrorIcon from '@mui/icons-material/Error'
import { useNotificationStore } from '@/store/notifications'
import type { AlertSeverity } from '@/types/api'

const SEVERITY_META: Record<AlertSeverity, { color: string; border: string; bg: string; icon: React.ReactNode }> = {
  info:     { color: '#7ED4FA', border: 'rgba(56,189,248,0.25)',  bg: 'rgba(56,189,248,0.07)',  icon: <InfoIcon sx={{ fontSize: 16 }} /> },
  low:      { color: '#4DD87E', border: 'rgba(34,197,94,0.25)',   bg: 'rgba(34,197,94,0.07)',   icon: <CheckCircleIcon sx={{ fontSize: 16 }} /> },
  medium:   { color: '#FBC14A', border: 'rgba(245,158,11,0.28)',  bg: 'rgba(245,158,11,0.07)',  icon: <WarningIcon sx={{ fontSize: 16 }} /> },
  high:     { color: '#FF7088', border: 'rgba(255,69,96,0.3)',    bg: 'rgba(255,69,96,0.08)',   icon: <ErrorIcon sx={{ fontSize: 16 }} /> },
  critical: { color: '#FF4560', border: 'rgba(255,69,96,0.5)',    bg: 'rgba(255,69,96,0.12)',   icon: <ErrorIcon sx={{ fontSize: 16 }} /> },
}

export function ToastContainer() {
  const toasts  = useNotificationStore((s) => s.toasts)
  const dismiss = useNotificationStore((s) => s.dismiss)

  if (toasts.length === 0) return null

  return (
    <Box
      sx={{
        position: 'fixed',
        bottom: 24,
        right: 24,
        zIndex: 2000,
        display: 'flex',
        flexDirection: 'column',
        gap: 1,
        maxWidth: 380,
        width: 'calc(100vw - 48px)',
      }}
    >
      {toasts.map((toast) => {
        const meta = SEVERITY_META[toast.severity] ?? SEVERITY_META.info
        return (
          <Box
            key={toast.id}
            className="fade-in"
            sx={{
              display: 'flex',
              alignItems: 'flex-start',
              gap: 1.25,
              p: 1.5,
              borderRadius: '12px',
              backgroundColor: 'rgba(5,8,22,0.94)',
              backdropFilter: 'blur(20px) saturate(180%)',
              WebkitBackdropFilter: 'blur(20px) saturate(180%)',
              border: `1px solid ${meta.border}`,
              boxShadow: `0 8px 32px rgba(0,0,0,0.6), 0 0 0 1px ${meta.border}`,
              background: `linear-gradient(135deg, rgba(5,8,22,0.96) 0%, ${meta.bg} 100%)`,
              transition: 'box-shadow 0.2s',
            }}
          >
            <Box sx={{ color: meta.color, mt: 0.1, flexShrink: 0 }}>
              {meta.icon}
            </Box>

            <Box sx={{ flex: 1, minWidth: 0 }}>
              <Typography
                sx={{
                  fontSize: '0.82rem',
                  fontWeight: 600,
                  color: 'text.primary',
                  lineHeight: 1.4,
                  wordBreak: 'break-word',
                }}
              >
                {toast.title}
              </Typography>
            </Box>

            <IconButton
              size="small"
              onClick={() => dismiss(toast.id)}
              sx={{
                flexShrink: 0,
                mt: -0.25,
                color: 'text.disabled',
                '&:hover': { color: 'text.secondary', background: 'rgba(255,255,255,0.08)' },
              }}
            >
              <CloseIcon sx={{ fontSize: 15 }} />
            </IconButton>
          </Box>
        )
      })}
    </Box>
  )
}

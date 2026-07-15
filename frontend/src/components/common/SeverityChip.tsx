import { Box, Typography } from '@mui/material'
import type { AlertSeverity } from '@/types/api'

const SEVERITY_CONFIG: Record<AlertSeverity, { label: string; bg: string; color: string; border: string; glow: string }> = {
  info:     { label: 'Info',     bg: 'rgba(56,189,248,0.12)',  color: '#7ED4FA', border: 'rgba(56,189,248,0.3)',  glow: 'none' },
  low:      { label: 'Low',      bg: 'rgba(34,197,94,0.1)',    color: '#4DD87E', border: 'rgba(34,197,94,0.25)',  glow: 'none' },
  medium:   { label: 'Medium',   bg: 'rgba(245,158,11,0.1)',   color: '#FBC14A', border: 'rgba(245,158,11,0.25)', glow: 'none' },
  high:     { label: 'High',     bg: 'rgba(255,69,96,0.12)',   color: '#FF7088', border: 'rgba(255,69,96,0.28)',  glow: 'none' },
  critical: { label: 'Critical', bg: 'rgba(255,69,96,0.18)',   color: '#FF4560', border: 'rgba(255,69,96,0.5)',   glow: '0 0 12px rgba(255,69,96,0.5)' },
}

interface Props {
  severity: AlertSeverity
  size?: 'small' | 'medium'
}

export function SeverityChip({ severity, size = 'small' }: Props) {
  const cfg = SEVERITY_CONFIG[severity]
  const isCritical = severity === 'critical'
  const height = size === 'small' ? 22 : 28
  const px = size === 'small' ? 1 : 1.5
  const fontSize = size === 'small' ? '0.65rem' : '0.74rem'

  return (
    <Box
      sx={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: 0.6,
        height,
        px,
        borderRadius: '6px',
        backgroundColor: cfg.bg,
        border: `1px solid ${cfg.border}`,
        backdropFilter: 'blur(8px)',
        boxShadow: cfg.glow,
        flexShrink: 0,
        ...(isCritical && {
          animation: 'glow-pulse-critical 1.6s ease-in-out infinite',
          '@keyframes glow-pulse-critical': {
            '0%,100%': { boxShadow: '0 0 8px rgba(255,69,96,0.3)', borderColor: 'rgba(255,69,96,0.4)' },
            '50%':     { boxShadow: '0 0 16px rgba(255,69,96,0.7)', borderColor: 'rgba(255,69,96,0.75)' },
          },
        }),
      }}
    >
      {isCritical && (
        <Box
          sx={{
            width: 5,
            height: 5,
            borderRadius: '50%',
            backgroundColor: cfg.color,
            flexShrink: 0,
            animation: 'pulse-dot 1.6s ease-in-out infinite',
            '@keyframes pulse-dot': {
              '0%,100%': { opacity: 1 },
              '50%': { opacity: 0.3 },
            },
          }}
        />
      )}
      <Typography
        sx={{
          fontSize,
          fontWeight: 700,
          textTransform: 'uppercase',
          letterSpacing: '0.07em',
          color: cfg.color,
          lineHeight: 1,
          fontFamily: '"Inter", sans-serif',
        }}
      >
        {cfg.label}
      </Typography>
    </Box>
  )
}

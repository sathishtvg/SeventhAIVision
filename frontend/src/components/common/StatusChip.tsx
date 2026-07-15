import { Box, Typography } from '@mui/material'

const STATUS_CONFIG: Record<string, { bg: string; color: string; border: string; dot?: boolean }> = {
  open:          { bg: 'rgba(255,69,96,0.12)',   color: '#FF7088', border: 'rgba(255,69,96,0.28)',   dot: true },
  acknowledged:  { bg: 'rgba(245,158,11,0.1)',   color: '#FBC14A', border: 'rgba(245,158,11,0.25)'  },
  investigating: { bg: 'rgba(56,189,248,0.1)',   color: '#7ED4FA', border: 'rgba(56,189,248,0.25)'  },
  resolved:      { bg: 'rgba(34,197,94,0.1)',    color: '#4DD87E', border: 'rgba(34,197,94,0.25)'   },
  closed:        { bg: 'rgba(100,116,139,0.08)', color: '#94A3B8', border: 'rgba(100,116,139,0.2)'  },
  dismissed:     { bg: 'rgba(100,116,139,0.08)', color: '#64748B', border: 'rgba(100,116,139,0.18)' },
  online:        { bg: 'rgba(34,197,94,0.1)',    color: '#4DD87E', border: 'rgba(34,197,94,0.25)',   dot: true },
  degraded:      { bg: 'rgba(245,158,11,0.1)',   color: '#FBC14A', border: 'rgba(245,158,11,0.25)',  dot: true },
  offline:       { bg: 'rgba(255,69,96,0.12)',   color: '#FF7088', border: 'rgba(255,69,96,0.28)'   },
  recording:     { bg: 'rgba(255,69,96,0.15)',   color: '#FF4560', border: 'rgba(255,69,96,0.4)',    dot: true },
  completed:     { bg: 'rgba(34,197,94,0.1)',    color: '#4DD87E', border: 'rgba(34,197,94,0.25)'   },
  failed:        { bg: 'rgba(255,69,96,0.1)',    color: '#FF7088', border: 'rgba(255,69,96,0.22)'   },
}

const DEFAULT_CFG = { bg: 'rgba(100,116,139,0.08)', color: '#94A3B8', border: 'rgba(100,116,139,0.18)' }

interface Props {
  status: string
  size?: 'small' | 'medium'
}

export function StatusChip({ status, size = 'small' }: Props) {
  const cfg = STATUS_CONFIG[status.toLowerCase()] ?? DEFAULT_CFG
  const height = size === 'small' ? 22 : 28
  const px = size === 'small' ? 1 : 1.5
  const fontSize = size === 'small' ? '0.65rem' : '0.74rem'
  const label = status.charAt(0).toUpperCase() + status.slice(1)

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
        flexShrink: 0,
      }}
    >
      {cfg.dot && (
        <Box
          sx={{
            width: 5,
            height: 5,
            borderRadius: '50%',
            backgroundColor: cfg.color,
            flexShrink: 0,
            animation: status === 'recording' ? 'pulse-dot-rec 1s ease-in-out infinite' : 'none',
            '@keyframes pulse-dot-rec': {
              '0%,100%': { opacity: 1 },
              '50%': { opacity: 0.25 },
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
        {label}
      </Typography>
    </Box>
  )
}

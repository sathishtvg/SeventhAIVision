import { Card } from '@mui/material'
import type { CardProps } from '@mui/material'

type GlassVariant = 'default' | 'glow' | 'elevated'

interface GlassCardProps extends Omit<CardProps, 'variant'> {
  variant?: GlassVariant
}

const VARIANT_STYLES: Record<GlassVariant, object> = {
  default: {
    backgroundColor: 'rgba(255,255,255,0.10)',
    backdropFilter: 'blur(16px) saturate(180%)',
    WebkitBackdropFilter: 'blur(16px) saturate(180%)',
    border: '1px solid rgba(255,255,255,0.18)',
    boxShadow: '0 4px 24px rgba(0,0,0,0.45), inset 0 1px 0 rgba(255,255,255,0.10)',
    transition: 'border-color 0.22s, box-shadow 0.22s, transform 0.22s',
    '&:hover': {
      borderColor: 'rgba(108,99,255,0.40)',
      boxShadow: '0 8px 40px rgba(0,0,0,0.55), 0 0 0 1px rgba(108,99,255,0.20), inset 0 1px 0 rgba(255,255,255,0.14)',
    },
  },
  glow: {
    backgroundColor: 'rgba(108,99,255,0.10)',
    backdropFilter: 'blur(20px) saturate(200%)',
    WebkitBackdropFilter: 'blur(20px) saturate(200%)',
    border: '1px solid rgba(108,99,255,0.30)',
    boxShadow: '0 4px 24px rgba(0,0,0,0.45), 0 0 0 1px rgba(108,99,255,0.12), inset 0 1px 0 rgba(255,255,255,0.12)',
    transition: 'border-color 0.22s, box-shadow 0.22s, transform 0.22s',
    '&:hover': {
      borderColor: 'rgba(108,99,255,0.55)',
      boxShadow: '0 8px 40px rgba(108,99,255,0.20), 0 0 0 1px rgba(108,99,255,0.28), inset 0 1px 0 rgba(108,99,255,0.18)',
    },
  },
  elevated: {
    backgroundColor: 'rgba(255,255,255,0.15)',
    backdropFilter: 'blur(24px) saturate(200%)',
    WebkitBackdropFilter: 'blur(24px) saturate(200%)',
    border: '1px solid rgba(255,255,255,0.22)',
    boxShadow: '0 12px 48px rgba(0,0,0,0.55), inset 0 1px 0 rgba(255,255,255,0.16)',
    transition: 'border-color 0.22s, box-shadow 0.22s, transform 0.22s',
    '&:hover': {
      borderColor: 'rgba(255,255,255,0.30)',
      boxShadow: '0 20px 60px rgba(0,0,0,0.65), inset 0 1px 0 rgba(255,255,255,0.22)',
      transform: 'translateY(-2px)',
    },
  },
}

export function GlassCard({ sx, variant = 'default', ...props }: GlassCardProps) {
  return (
    <Card
      sx={{
        backgroundImage: 'none',
        borderRadius: '16px',
        ...VARIANT_STYLES[variant],
        ...sx,
      }}
      {...props}
    />
  )
}

export default GlassCard

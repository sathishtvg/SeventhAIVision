export const colors = {
  // OLED dark root background
  background: '#020617',

  // Glass surface tokens
  glassBg:          'rgba(255,255,255,0.10)',
  glassBgDeep:      'rgba(255,255,255,0.06)',
  glassBgSubtle:    'rgba(255,255,255,0.04)',
  glassBorder:      'rgba(255,255,255,0.18)',
  glassBorderLight: 'rgba(255,255,255,0.10)',

  // Backward-compat tokens mapped to glass values
  surface:    'rgba(255,255,255,0.06)',
  card:       'rgba(255,255,255,0.10)',
  cardBorder: 'rgba(255,255,255,0.18)',

  // Brand
  primary:        '#6C63FF',
  primaryGlow:    'rgba(108,99,255,0.30)',
  primaryMuted:   'rgba(108,99,255,0.15)',
  secondary:      '#00D9C0',
  secondaryGlow:  'rgba(0,217,192,0.25)',
  secondaryMuted: 'rgba(0,217,192,0.15)',
  cta:            '#22C55E',

  // Semantic
  error:        '#FF4560',
  errorMuted:   'rgba(255,69,96,0.18)',
  warning:      '#FF9800',
  warningMuted: 'rgba(255,152,0,0.18)',
  success:      '#22C55E',
  successMuted: 'rgba(34,197,94,0.18)',
  info:         '#38BDF8',
  infoMuted:    'rgba(56,189,248,0.18)',

  // Text
  text:          '#F8FAFC',
  textSecondary: 'rgba(248,250,252,0.60)',
  textDisabled:  'rgba(248,250,252,0.30)',

  // Divider
  divider: 'rgba(255,255,255,0.10)',
}

export const severity: Record<string, string> = {
  info:     colors.info,
  low:      colors.success,
  medium:   colors.warning,
  high:     '#FF6B35',
  critical: colors.error,
}

export const statusColor: Record<string, string> = {
  open:          colors.error,
  acknowledged:  colors.warning,
  resolved:      colors.success,
  dismissed:     colors.textDisabled,
  investigating: colors.warning,
  closed:        colors.textDisabled,
  online:        colors.success,
  degraded:      colors.warning,
  offline:       colors.error,
}

export const spacing = {
  xs:  4,
  sm:  8,
  md:  16,
  lg:  24,
  xl:  32,
  xxl: 48,
}

export const radius = {
  xs:   4,
  sm:   8,
  md:   12,
  lg:   16,
  xl:   24,
  full: 9999,
}

export const fontSize = {
  xs:   11,
  sm:   13,
  md:   15,
  lg:   17,
  xl:   20,
  xxl:  24,
  xxxl: 32,
}

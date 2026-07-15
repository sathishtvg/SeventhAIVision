import { useState, useEffect } from 'react'
import { Link as RouterLink, useLocation, useNavigate } from 'react-router-dom'
import type { Location } from 'react-router-dom'
import {
  Box, Button, CircularProgress, Collapse,
  TextField, Typography, Alert, InputAdornment, IconButton, Chip, Link,
} from '@mui/material'
import ShieldIcon from '@mui/icons-material/Shield'
import VisibilityIcon from '@mui/icons-material/Visibility'
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff'
import BusinessIcon from '@mui/icons-material/Business'
import EmailIcon from '@mui/icons-material/Email'
import LockIcon from '@mui/icons-material/Lock'
import DomainIcon from '@mui/icons-material/Domain'
import { useAuthStore } from '@/store/auth'
import { resolveSubdomain, type TenantInfo } from '@/api/auth'
import { getSubdomain } from '@/hooks/useSubdomain'

type ResolveState = 'idle' | 'loading' | 'done' | 'error'

// Detected once at module load — stable for the lifetime of the page.
const DETECTED_SUBDOMAIN = getSubdomain()

export default function Login() {
  const navigate = useNavigate()
  const location = useLocation()
  const loginFn = useAuthStore((s) => s.login)
  const accessToken = useAuthStore((s) => s.accessToken)

  // Where RequireAuth sent the user here from (e.g. a Live Wall pop-out
  // window opened straight to /live?layout=X) — falls back to Dashboard
  // for a plain, un-redirected visit to /login.
  const from = (location.state as { from?: Location } | null)?.from
  const redirectTarget = from ? `${from.pathname}${from.search}${from.hash}` : '/'

  useEffect(() => {
    if (accessToken) navigate(redirectTarget, { replace: true })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [accessToken, navigate])

  // ── Subdomain resolution state ─────────────────────────────────────────────
  const [resolveState, setResolveState] = useState<ResolveState>(
    DETECTED_SUBDOMAIN ? 'loading' : 'idle'
  )
  const [tenantInfo, setTenantInfo] = useState<TenantInfo | null>(null)

  useEffect(() => {
    if (!DETECTED_SUBDOMAIN) return
    let cancelled = false
    resolveSubdomain(DETECTED_SUBDOMAIN)
      .then((info) => {
        if (cancelled) return
        setTenantInfo(info)
        setTenantSlug(info.slug)
        setResolveState('done')
      })
      .catch(() => {
        if (cancelled) return
        setResolveState('error')
      })
    return () => { cancelled = true }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // ── Form state ─────────────────────────────────────────────────────────────
  const [tenantSlug, setTenantSlug] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      await loginFn(tenantSlug.trim(), email.trim(), password)
      navigate(redirectTarget)
    } catch (err: any) {
      const code = err?.response?.data?.detail?.code
      if (code === '2fa_setup_required') {
        setError(
          'Two-factor authentication is required. Contact your administrator to complete 2FA setup.'
        )
      } else {
        setError('Invalid credentials. Check your organisation slug, email and password.')
      }
    } finally {
      setLoading(false)
    }
  }

  // ── Computed branding values ───────────────────────────────────────────────
  const accentColor   = tenantInfo?.branding?.primary_color  ?? '#6C63FF'
  const accentDark    = tenantInfo?.branding?.primary_dark   ?? '#5048D4'
  const accentGlow    = `${accentColor}80`
  const brandLogoUrl  = tenantInfo?.branding?.logo_url       ?? null
  const isSubdomain   = DETECTED_SUBDOMAIN !== null
  const tenantResolved = resolveState === 'done' && tenantInfo !== null

  return (
    <Box
      sx={{
        minHeight: '100vh',
        width: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        position: 'relative',
        overflow: 'hidden',
        background: 'radial-gradient(ellipse 120% 80% at 15% 10%, #0d0a2e 0%, #020617 55%) fixed, radial-gradient(ellipse 80% 60% at 85% 90%, #040d1a 0%, #020617 60%) fixed',
      }}
    >
      {/* Animated background orbs */}
      <Box sx={{ position: 'absolute', inset: 0, overflow: 'hidden', zIndex: 0 }}>
        <Box sx={{
          position: 'absolute',
          top: '-15%',
          left: '-10%',
          width: '60vw',
          height: '60vw',
          borderRadius: '50%',
          background: `radial-gradient(circle, ${accentColor}2e 0%, transparent 70%)`,
          animation: 'float-orb-a 12s ease-in-out infinite',
          '@keyframes float-orb-a': {
            '0%,100%': { transform: 'translate(0, 0) scale(1)' },
            '33%': { transform: 'translate(5vw, 4vh) scale(1.06)' },
            '66%': { transform: 'translate(-3vw, 7vh) scale(0.95)' },
          },
        }} />
        <Box sx={{
          position: 'absolute',
          bottom: '-10%',
          right: '-5%',
          width: '50vw',
          height: '50vw',
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(0,217,192,0.12) 0%, transparent 70%)',
          animation: 'float-orb-b 15s ease-in-out infinite',
          '@keyframes float-orb-b': {
            '0%,100%': { transform: 'translate(0, 0) scale(1)' },
            '40%': { transform: 'translate(-4vw, -5vh) scale(1.08)' },
            '70%': { transform: 'translate(3vw, -2vh) scale(0.94)' },
          },
        }} />
        <Box sx={{
          position: 'absolute',
          top: '40%',
          right: '15%',
          width: '20vw',
          height: '20vw',
          borderRadius: '50%',
          background: `radial-gradient(circle, ${accentColor}1a 0%, transparent 70%)`,
          animation: 'float-orb-c 8s ease-in-out infinite',
          '@keyframes float-orb-c': {
            '0%,100%': { transform: 'translate(0, 0)' },
            '50%': { transform: 'translate(-2vw, 5vh)' },
          },
        }} />
        <Box sx={{
          position: 'absolute',
          inset: 0,
          backgroundImage: `
            linear-gradient(${accentColor}0a 1px, transparent 1px),
            linear-gradient(90deg, ${accentColor}0a 1px, transparent 1px)
          `,
          backgroundSize: '80px 80px',
          maskImage: 'radial-gradient(ellipse 70% 70% at 50% 50%, black 0%, transparent 100%)',
        }} />
      </Box>

      {/* Login card */}
      <Box
        sx={{
          position: 'relative',
          zIndex: 1,
          width: '100%',
          maxWidth: 440,
          mx: 2,
          animation: 'fade-up-login 0.45s cubic-bezier(0.0, 0.0, 0.2, 1)',
          '@keyframes fade-up-login': {
            from: { opacity: 0, transform: 'translateY(20px)' },
            to:   { opacity: 1, transform: 'translateY(0)' },
          },
        }}
      >
        <Box
          sx={{
            backgroundColor: 'rgba(255,255,255,0.12)',
            backdropFilter: 'blur(24px) saturate(200%)',
            WebkitBackdropFilter: 'blur(24px) saturate(200%)',
            border: '1px solid rgba(255,255,255,0.20)',
            borderRadius: '20px',
            boxShadow: `0 24px 64px rgba(0,0,0,0.6), 0 0 0 1px ${accentColor}1f, inset 0 1px 0 rgba(255,255,255,0.14)`,
            overflow: 'hidden',
          }}
        >
          {/* Top accent line */}
          <Box sx={{
            height: 3,
            background: `linear-gradient(90deg, ${accentColor} 0%, #00D9C0 50%, ${accentColor} 100%)`,
            backgroundSize: '200% 100%',
            animation: 'shimmer-bar 3s linear infinite',
            '@keyframes shimmer-bar': {
              '0%': { backgroundPosition: '200% center' },
              '100%': { backgroundPosition: '-200% center' },
            },
          }} />

          <Box sx={{ p: 4 }}>
            {/* Subdomain resolution loading state */}
            {resolveState === 'loading' && (
              <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, mb: 3, p: 2, borderRadius: '10px', background: 'rgba(255,255,255,0.05)' }}>
                <CircularProgress size={16} sx={{ color: accentColor, flexShrink: 0 }} />
                <Typography variant="caption" sx={{ color: 'text.secondary', fontSize: '0.78rem' }}>
                  Resolving your workspace&hellip;
                </Typography>
              </Box>
            )}

            {/* Subdomain resolve error — show banner but keep slug field */}
            {resolveState === 'error' && (
              <Alert
                severity="warning"
                sx={{ mb: 2.5, fontSize: '0.78rem' }}
                icon={<DomainIcon fontSize="small" />}
              >
                Workspace&nbsp;<strong>{DETECTED_SUBDOMAIN}</strong>&nbsp;not found.
                Enter your organisation slug manually.
              </Alert>
            )}

            {/* Subdomain resolved — show tenant chip */}
            {tenantResolved && (
              <Collapse in>
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2.5 }}>
                  <Chip
                    icon={<DomainIcon sx={{ fontSize: '14px !important' }} />}
                    label={DETECTED_SUBDOMAIN}
                    size="small"
                    sx={{
                      fontSize: '0.72rem',
                      height: 24,
                      background: `${accentColor}22`,
                      border: `1px solid ${accentColor}44`,
                      color: accentColor,
                      '& .MuiChip-icon': { color: accentColor },
                    }}
                  />
                  <Typography variant="caption" sx={{ color: 'text.disabled', fontSize: '0.72rem' }}>
                    workspace detected
                  </Typography>
                </Box>
              </Collapse>
            )}

            {/* Brand header */}
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: tenantResolved ? 3 : 4 }}>
              {/* Logo — use tenant logo_url when provided, else ShieldIcon */}
              <Box
                sx={{
                  width: 48,
                  height: 48,
                  borderRadius: '14px',
                  background: brandLogoUrl
                    ? 'rgba(255,255,255,0.08)'
                    : `linear-gradient(135deg, ${accentColor} 0%, #00D9C0 100%)`,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  flexShrink: 0,
                  overflow: 'hidden',
                  boxShadow: brandLogoUrl
                    ? `0 6px 20px ${accentGlow}66`
                    : `0 6px 20px ${accentGlow}8c, inset 0 1px 0 rgba(255,255,255,0.3)`,
                }}
              >
                {brandLogoUrl ? (
                  <Box
                    component="img"
                    src={brandLogoUrl}
                    alt="Logo"
                    sx={{ width: '100%', height: '100%', objectFit: 'contain', p: 0.75 }}
                  />
                ) : (
                  <ShieldIcon sx={{ fontSize: 26, color: '#fff' }} />
                )}
              </Box>

              <Box>
                <Typography
                  variant="h5"
                  sx={{
                    fontWeight: 800,
                    lineHeight: 1.1,
                    background: tenantResolved
                      ? `linear-gradient(135deg, #F1F5F9 30%, ${accentColor} 100%)`
                      : 'linear-gradient(135deg, #F1F5F9 30%, #8B85FF 100%)',
                    WebkitBackgroundClip: 'text',
                    WebkitTextFillColor: 'transparent',
                    backgroundClip: 'text',
                    letterSpacing: '-0.02em',
                  }}
                >
                  {tenantInfo?.name ?? 'Seventh AI Vision'}
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.72rem' }}>
                  {tenantResolved
                    ? 'Enterprise Security Operations Platform'
                    : 'Enterprise Security Operations Platform'}
                </Typography>
              </Box>
            </Box>

            <Typography
              variant="body2"
              sx={{ color: 'text.secondary', mb: 3, fontSize: '0.82rem' }}
            >
              {tenantResolved
                ? `Sign in to ${tenantInfo!.name}'s security dashboard.`
                : "Sign in to your organisation's security dashboard."}
            </Typography>

            {error && (
              <Alert severity="error" sx={{ mb: 2.5, fontSize: '0.82rem' }}>
                {error}
              </Alert>
            )}

            <Box
              component="form"
              onSubmit={handleSubmit}
              sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}
            >
              {/* Organisation slug — hidden when tenant is auto-resolved from subdomain */}
              <Collapse in={!tenantResolved}>
                <TextField
                  label="Organisation slug"
                  value={tenantSlug}
                  onChange={(e) => setTenantSlug(e.target.value)}
                  required={!tenantResolved}
                  fullWidth
                  autoFocus={!tenantResolved}
                  autoComplete="organization"
                  placeholder="my-company"
                  size="small"
                  slotProps={{
                    input: {
                      startAdornment: (
                        <InputAdornment position="start">
                          <BusinessIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
                        </InputAdornment>
                      ),
                    },
                  }}
                />
              </Collapse>

              <TextField
                label="Email"
                type="email"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
                fullWidth
                autoFocus={tenantResolved}
                autoComplete="email"
                size="small"
                slotProps={{
                  input: {
                    startAdornment: (
                      <InputAdornment position="start">
                        <EmailIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
                      </InputAdornment>
                    ),
                  },
                }}
              />
              <TextField
                label="Password"
                type={showPassword ? 'text' : 'password'}
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
                fullWidth
                autoComplete="current-password"
                size="small"
                slotProps={{
                  input: {
                    startAdornment: (
                      <InputAdornment position="start">
                        <LockIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
                      </InputAdornment>
                    ),
                    endAdornment: (
                      <InputAdornment position="end">
                        <IconButton
                          size="small"
                          onClick={() => setShowPassword((v) => !v)}
                          tabIndex={-1}
                          edge="end"
                        >
                          {showPassword
                            ? <VisibilityOffIcon sx={{ fontSize: 16 }} />
                            : <VisibilityIcon sx={{ fontSize: 16 }} />}
                        </IconButton>
                      </InputAdornment>
                    ),
                  },
                }}
              />

              <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: -1 }}>
                <Link
                  component={RouterLink}
                  to="/forgot-password"
                  sx={{
                    color: 'text.secondary', fontSize: '0.76rem', textDecoration: 'none',
                    '&:hover': { color: accentColor, textDecoration: 'underline' },
                  }}
                >
                  Forgot password?
                </Link>
              </Box>

              <Button
                type="submit"
                variant="contained"
                fullWidth
                disabled={loading || resolveState === 'loading'}
                sx={{
                  mt: 0.5,
                  py: 1.35,
                  fontSize: '0.9rem',
                  fontWeight: 700,
                  borderRadius: '10px',
                  background: `linear-gradient(135deg, ${accentColor} 0%, ${accentDark} 100%)`,
                  boxShadow: `0 4px 18px ${accentGlow}`,
                  letterSpacing: '0.01em',
                  transition: 'all 0.22s',
                  '&:hover': {
                    background: `linear-gradient(135deg, ${accentColor}cc 0%, ${accentColor} 100%)`,
                    boxShadow: `0 6px 28px ${accentColor}b3`,
                    transform: 'translateY(-1px)',
                  },
                  '&:active': { transform: 'translateY(0)' },
                  '&.Mui-disabled': {
                    background: `${accentColor}38`,
                    boxShadow: 'none',
                  },
                }}
              >
                {loading ? <CircularProgress size={20} color="inherit" /> : 'Sign in'}
              </Button>
            </Box>

            {/* Footer */}
            <Box sx={{ mt: 3, display: 'flex', flexDirection: 'column', alignItems: 'center', gap: 0.5 }}>
              {/* "Powered by" footer shown when accessing via tenant subdomain */}
              {isSubdomain && (
                <Box sx={{ display: 'flex', alignItems: 'center', gap: 0.75 }}>
                  <ShieldIcon sx={{ fontSize: 10, color: 'text.disabled', opacity: 0.6 }} />
                  <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.66rem', letterSpacing: '0.02em' }}>
                    Powered by Seventh AI Vision
                  </Typography>
                </Box>
              )}
              <Typography variant="caption" color="text.disabled" sx={{ fontSize: '0.66rem', letterSpacing: '0.02em' }}>
                Protected by enterprise-grade encryption · v1.0.0
              </Typography>
            </Box>
          </Box>
        </Box>
      </Box>
    </Box>
  )
}

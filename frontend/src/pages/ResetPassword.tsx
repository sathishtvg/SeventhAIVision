import { useState } from 'react'
import { Link as RouterLink, useNavigate, useSearchParams } from 'react-router-dom'
import {
  Box, Button, CircularProgress, TextField, Typography, Alert, InputAdornment, IconButton, Link,
} from '@mui/material'
import ShieldIcon from '@mui/icons-material/Shield'
import LockIcon from '@mui/icons-material/Lock'
import VisibilityIcon from '@mui/icons-material/Visibility'
import VisibilityOffIcon from '@mui/icons-material/VisibilityOff'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import { resetPassword } from '@/api/auth'

const accentColor = '#6C63FF'
const accentDark = '#5048D4'
const accentGlow = `${accentColor}80`

export default function ResetPassword() {
  const navigate = useNavigate()
  const [searchParams] = useSearchParams()
  const token = searchParams.get('token') ?? ''

  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showPassword, setShowPassword] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [done, setDone] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError(null)

    if (newPassword.length < 8) {
      setError('Password must be at least 8 characters.')
      return
    }
    if (newPassword !== confirmPassword) {
      setError('Passwords do not match.')
      return
    }

    setLoading(true)
    try {
      await resetPassword(token, newPassword)
      setDone(true)
      setTimeout(() => navigate('/login', { replace: true }), 2500)
    } catch (err: any) {
      const detail = err?.response?.data?.detail
      setError(typeof detail === 'string' ? detail : 'This reset link is invalid or has expired.')
    } finally {
      setLoading(false)
    }
  }

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
      <Box sx={{ position: 'absolute', inset: 0, overflow: 'hidden', zIndex: 0 }}>
        <Box sx={{
          position: 'absolute', top: '-15%', left: '-10%', width: '60vw', height: '60vw',
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
          position: 'absolute', bottom: '-10%', right: '-5%', width: '50vw', height: '50vw',
          borderRadius: '50%',
          background: 'radial-gradient(circle, rgba(0,217,192,0.12) 0%, transparent 70%)',
          animation: 'float-orb-b 15s ease-in-out infinite',
          '@keyframes float-orb-b': {
            '0%,100%': { transform: 'translate(0, 0) scale(1)' },
            '40%': { transform: 'translate(-4vw, -5vh) scale(1.08)' },
            '70%': { transform: 'translate(3vw, -2vh) scale(0.94)' },
          },
        }} />
      </Box>

      <Box
        sx={{
          position: 'relative', zIndex: 1, width: '100%', maxWidth: 440, mx: 2,
          animation: 'fade-up-login 0.45s cubic-bezier(0.0, 0.0, 0.2, 1)',
          '@keyframes fade-up-login': {
            from: { opacity: 0, transform: 'translateY(20px)' },
            to: { opacity: 1, transform: 'translateY(0)' },
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
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 3 }}>
              <Box
                sx={{
                  width: 48, height: 48, borderRadius: '14px',
                  background: `linear-gradient(135deg, ${accentColor} 0%, #00D9C0 100%)`,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                  boxShadow: `0 6px 20px ${accentGlow}8c, inset 0 1px 0 rgba(255,255,255,0.3)`,
                }}
              >
                <ShieldIcon sx={{ fontSize: 26, color: '#fff' }} />
              </Box>
              <Box>
                <Typography
                  variant="h5"
                  sx={{
                    fontWeight: 800, lineHeight: 1.1,
                    background: 'linear-gradient(135deg, #F1F5F9 30%, #8B85FF 100%)',
                    WebkitBackgroundClip: 'text', WebkitTextFillColor: 'transparent',
                    backgroundClip: 'text', letterSpacing: '-0.02em',
                  }}
                >
                  Reset Password
                </Typography>
                <Typography variant="caption" color="text.secondary" sx={{ fontSize: '0.72rem' }}>
                  Seventh AI Vision
                </Typography>
              </Box>
            </Box>

            {!token ? (
              <Alert severity="error" sx={{ fontSize: '0.82rem' }}>
                This reset link is missing its token. Please request a new one from the{' '}
                <Link component={RouterLink} to="/forgot-password" sx={{ color: accentColor }}>
                  forgot password
                </Link>{' '}
                page.
              </Alert>
            ) : done ? (
              <Alert severity="success" sx={{ fontSize: '0.82rem' }}>
                Password reset successfully. Redirecting you to sign in&hellip;
              </Alert>
            ) : (
              <>
                <Typography variant="body2" sx={{ color: 'text.secondary', mb: 3, fontSize: '0.82rem' }}>
                  Choose a new password for your account.
                </Typography>

                {error && (
                  <Alert severity="error" sx={{ mb: 2.5, fontSize: '0.82rem' }}>
                    {error}
                  </Alert>
                )}

                <Box component="form" onSubmit={handleSubmit} sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                  <TextField
                    label="New password"
                    type={showPassword ? 'text' : 'password'}
                    value={newPassword}
                    onChange={(e) => setNewPassword(e.target.value)}
                    required
                    fullWidth
                    autoFocus
                    autoComplete="new-password"
                    size="small"
                    helperText="At least 8 characters"
                    slotProps={{
                      input: {
                        startAdornment: (
                          <InputAdornment position="start">
                            <LockIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
                          </InputAdornment>
                        ),
                        endAdornment: (
                          <InputAdornment position="end">
                            <IconButton size="small" onClick={() => setShowPassword((v) => !v)} tabIndex={-1} edge="end">
                              {showPassword
                                ? <VisibilityOffIcon sx={{ fontSize: 16 }} />
                                : <VisibilityIcon sx={{ fontSize: 16 }} />}
                            </IconButton>
                          </InputAdornment>
                        ),
                      },
                    }}
                  />
                  <TextField
                    label="Confirm new password"
                    type={showPassword ? 'text' : 'password'}
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    required
                    fullWidth
                    autoComplete="new-password"
                    size="small"
                    slotProps={{
                      input: {
                        startAdornment: (
                          <InputAdornment position="start">
                            <LockIcon sx={{ fontSize: 16, color: 'text.disabled' }} />
                          </InputAdornment>
                        ),
                      },
                    }}
                  />

                  <Button
                    type="submit"
                    variant="contained"
                    fullWidth
                    disabled={loading}
                    sx={{
                      mt: 0.5, py: 1.35, fontSize: '0.9rem', fontWeight: 700, borderRadius: '10px',
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
                      '&.Mui-disabled': { background: `${accentColor}38`, boxShadow: 'none' },
                    }}
                  >
                    {loading ? <CircularProgress size={20} color="inherit" /> : 'Reset password'}
                  </Button>

                  <Link
                    component={RouterLink}
                    to="/login"
                    sx={{
                      display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 0.75,
                      color: 'text.secondary', fontSize: '0.78rem', textDecoration: 'none', mt: 0.5,
                      '&:hover': { color: accentColor, textDecoration: 'underline' },
                    }}
                  >
                    <ArrowBackIcon sx={{ fontSize: 14 }} />
                    Back to sign in
                  </Link>
                </Box>
              </>
            )}
          </Box>
        </Box>
      </Box>
    </Box>
  )
}

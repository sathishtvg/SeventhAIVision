import { useEffect, useState } from 'react'
import {
  AppBar, Badge, Box, Button, Chip, CircularProgress, Dialog, DialogActions, DialogContent,
  DialogTitle, IconButton, List, ListItem, ListItemSecondaryAction, ListItemText,
  MenuItem, Select, Toolbar, Tooltip, Typography, useTheme,
} from '@mui/material'
import NotificationsIcon from '@mui/icons-material/Notifications'
import DarkModeIcon from '@mui/icons-material/DarkMode'
import LightModeIcon from '@mui/icons-material/LightMode'
import AccountCircleIcon from '@mui/icons-material/AccountCircle'
import DevicesIcon from '@mui/icons-material/Devices'
import LogoutIcon from '@mui/icons-material/Logout'
import LanguageIcon from '@mui/icons-material/Language'
import VolumeUpIcon from '@mui/icons-material/VolumeUp'
import VolumeOffIcon from '@mui/icons-material/VolumeOff'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { PRODUCT_NAME } from '@/lib/brand'
import { getMySessions, revokeMySession, revokeAllMySessions } from '@/api/sessions'
import { useAuthStore } from '@/store/auth'
import { useNotificationStore } from '@/store/notifications'
import { useAlertSoundStore } from '@/store/alertSound'
import { useWebSocket } from '@/hooks/useWebSocket'
import { useColorMode } from '@/context/ColorMode'
import { DRAWER_WIDTH } from './Sidebar'
import { useTranslation } from 'react-i18next'
import i18n, { LOCALE_LABELS, SUPPORTED_LOCALES, type SupportedLocale } from '@/i18n'

const ROLE_LABELS: Record<number, string> = {
  1: 'Super Admin', 2: 'Admin', 3: 'Supervisor',
  4: 'Operator', 5: 'Security Guard', 6: 'Viewer', 7: 'Client', 8: 'Manager',
}

function ProfileDialog({ open, onClose }: { open: boolean; onClose: () => void }) {
  const queryClient = useQueryClient()
  const logout = useAuthStore((s) => s.logout)
  const user   = useAuthStore((s) => s.user)

  const { data: sessions, isLoading } = useQuery({
    queryKey: ['my-sessions'],
    queryFn: getMySessions,
    enabled: open,
  })

  const { mutate: revokeOne, isPending: revokingOne } = useMutation({
    mutationFn: revokeMySession,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['my-sessions'] }),
  })

  const { mutate: revokeAll, isPending: revokingAll } = useMutation({
    mutationFn: revokeAllMySessions,
    onSuccess: () => { logout() },
  })

  const roleName = user ? (ROLE_LABELS[user.roleId] ?? `Role ${user.roleId}`) : '—'

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth
      slotProps={{ paper: { sx: { background: 'rgba(15,15,35,0.95)', backdropFilter: 'blur(24px)', border: '1px solid rgba(255,255,255,0.08)' } } }}>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1, pb: 1 }}>
        <AccountCircleIcon sx={{ color: '#6C63FF' }} />
        My Account
        <Chip label={roleName} size="small" sx={{ ml: 'auto', background: 'rgba(108,99,255,0.15)', color: '#8B85FF', border: '1px solid rgba(108,99,255,0.3)', fontSize: '0.7rem' }} />
      </DialogTitle>

      <DialogContent dividers sx={{ borderColor: 'rgba(255,255,255,0.06)' }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
          <DevicesIcon sx={{ fontSize: 16, color: 'text.secondary' }} />
          <Typography variant="subtitle2" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: '0.08em', fontSize: '0.68rem' }}>
            Active Sessions
          </Typography>
        </Box>

        {isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
            <CircularProgress size={24} sx={{ color: '#6C63FF' }} />
          </Box>
        ) : !sessions?.length ? (
          <Typography color="text.secondary" variant="body2" sx={{ py: 2, textAlign: 'center' }}>
            No active sessions found.
          </Typography>
        ) : (
          <List dense disablePadding>
            {sessions.map((s) => (
              <ListItem key={s.id} divider sx={{ borderColor: 'rgba(255,255,255,0.04)', py: 1 }}>
                <ListItemText
                  primary={s.device_name ?? 'Unknown device'}
                  secondary={[
                    s.last_ip ? `IP: ${s.last_ip}` : null,
                    s.last_seen_at ? `Last seen: ${new Date(s.last_seen_at).toLocaleString()}` : null,
                  ].filter(Boolean).join('  ·  ')}
                  slotProps={{
                    primary: { variant: 'body2', sx: { fontWeight: 600 } },
                    secondary: { variant: 'caption', color: 'text.secondary' },
                  }}
                />
                <ListItemSecondaryAction>
                  <Tooltip title="Revoke this session">
                    <IconButton size="small" disabled={revokingOne} onClick={() => revokeOne(s.id)}
                      sx={{ color: 'error.main', '&:hover': { background: 'rgba(255,69,96,0.08)' } }}>
                      <LogoutIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </ListItemSecondaryAction>
              </ListItem>
            ))}
          </List>
        )}
      </DialogContent>

      <DialogActions sx={{ px: 2.5, py: 1.5, gap: 1 }}>
        <Button onClick={onClose} sx={{ color: 'text.secondary' }}>Close</Button>
        <Button color="error" variant="outlined" size="small"
          disabled={revokingAll || !sessions?.length}
          onClick={() => revokeAll()}
          sx={{ borderColor: 'rgba(255,69,96,0.4)', '&:hover': { borderColor: '#FF4560' } }}>
          Sign out everywhere
        </Button>
        <Button variant="contained" startIcon={<LogoutIcon />} size="small"
          onClick={() => { logout(); onClose() }}
          sx={{ background: 'linear-gradient(135deg, #6C63FF, #4F46E5)', '&:hover': { background: 'linear-gradient(135deg, #7C73FF, #5F56F5)' } }}>
          Sign out
        </Button>
      </DialogActions>
    </Dialog>
  )
}

interface Props { title: string }

function LiveClock() {
  const [time, setTime] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(id)
  }, [])
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        gap: 0.5,
        px: 1.25,
        py: 0.5,
        borderRadius: '8px',
        background: 'rgba(255,255,255,0.03)',
        border: '1px solid rgba(255,255,255,0.06)',
        mr: 1,
      }}
    >
      <Typography
        sx={{
          color: 'text.secondary',
          fontFamily: '"Fira Code", "JetBrains Mono", monospace',
          fontSize: '0.7rem',
          letterSpacing: '0.04em',
          userSelect: 'none',
          lineHeight: 1,
        }}
      >
        {time.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false })}
      </Typography>
    </Box>
  )
}

export function TopBar({ title }: Props) {
  const unreadCount = useNotificationStore((s) => s.unreadCount)
  const markRead    = useNotificationStore((s) => s.markRead)
  const soundEnabled = useAlertSoundStore((s) => s.soundEnabled)
  const toggleSound  = useAlertSoundStore((s) => s.toggleSound)
  const { status }  = useWebSocket()
  const { mode, toggle } = useColorMode()
  const theme = useTheme()
  const isDark = mode === 'dark'
  const [profileOpen, setProfileOpen] = useState(false)

  const { i18n: i18nInstance } = useTranslation()
  const currentLocale = (i18nInstance.language?.slice(0, 2) ?? 'en') as SupportedLocale

  const handleLocaleChange = (locale: SupportedLocale) => {
    i18n.changeLanguage(locale)
    localStorage.setItem('locale', locale)
    // Persist to backend (fire-and-forget)
    import('@/api/client').then(({ apiClient }) => {
      apiClient.put('/api/v1/i18n/me/locale', { locale }).catch(() => {/* ignore if logged out */})
    })
  }

  const wsColor = status === 'open' ? '#22C55E' : status === 'connecting' ? '#F59E0B' : '#FF4560'
  const wsLabel = status === 'open' ? 'Live'     : status === 'connecting' ? 'Connecting'  : 'Offline'
  const wsRgb   = status === 'open' ? '34,197,94' : status === 'connecting' ? '245,158,11' : '255,69,96'

  return (
    <>
    <AppBar
      position="fixed"
      elevation={0}
      sx={{
        width: `calc(100% - ${DRAWER_WIDTH}px)`,
        ml: `${DRAWER_WIDTH}px`,
        '&::after': {
          content: '""',
          position: 'absolute',
          bottom: 0,
          left: 0,
          right: 0,
          height: '1px',
          background:
            'linear-gradient(90deg, transparent 0%, rgba(108,99,255,0.6) 30%, rgba(0,217,192,0.5) 70%, transparent 100%)',
        },
      }}
    >
      <Toolbar sx={{ gap: 0.5, minHeight: '56px !important' }}>
        {/* Vertical accent bar */}
        <Box
          sx={{
            width: 2.5,
            height: 20,
            borderRadius: 2,
            background: 'linear-gradient(180deg, #6C63FF 0%, #00D9C0 100%)',
            boxShadow: '0 0 8px rgba(108,99,255,0.8)',
            flexShrink: 0,
            mr: 1.25,
          }}
        />

        {/* The product name, then where you are inside it. This bar is the one
            element on every screen, so it is what guarantees "Seventh AI
            Vision" is always visible; the subscriber's own label and logo stay
            in the sidebar rather than competing for the same spot. Both are
            solid text:
            these used to be painted as a gradient clipped to the glyphs, which
            fades every title into the accent colour toward its end and reads as
            washed out on both themes — worse the longer the word. The accent
            now lives entirely in the bar to the left, where it decorates
            without costing legibility. */}
        <Box sx={{ flexGrow: 1, minWidth: 0, display: 'flex', alignItems: 'baseline', gap: 1 }}>
          <Typography
            variant="h6"
            noWrap
            sx={{
              fontWeight: 800,
              fontSize: '0.95rem',
              letterSpacing: '-0.02em',
              color: 'text.primary',
              flexShrink: 0,
            }}
          >
            {PRODUCT_NAME}
          </Typography>
          <Typography
            variant="body2"
            noWrap
            sx={{ fontSize: '0.8rem', color: 'text.secondary', minWidth: 0 }}
          >
            · {title}
          </Typography>
        </Box>

        {/* Live clock */}
        <LiveClock />

        {/* WS status pill */}
        <Tooltip title={`WebSocket: ${wsLabel}`} arrow>
          <Box
            sx={{
              display: 'flex',
              alignItems: 'center',
              gap: 0.65,
              mr: 0.75,
              px: 1.25,
              py: 0.55,
              borderRadius: '20px',
              background: `rgba(${wsRgb},0.08)`,
              border: `1px solid rgba(${wsRgb},0.22)`,
              cursor: 'default',
              transition: 'all 0.3s ease',
            }}
          >
            <Box sx={{ position: 'relative', width: 7, height: 7, flexShrink: 0 }}>
              {status !== 'closed' && (
                <Box
                  sx={{
                    position: 'absolute',
                    inset: 0,
                    borderRadius: '50%',
                    backgroundColor: wsColor,
                    opacity: 0.4,
                    animation: 'pulse-ring 1.8s ease-out infinite',
                    transform: 'scale(1)',
                  }}
                />
              )}
              <Box
                sx={{
                  position: 'absolute',
                  inset: 0,
                  borderRadius: '50%',
                  backgroundColor: wsColor,
                  boxShadow: `0 0 6px ${wsColor}`,
                }}
              />
            </Box>
            <Typography
              sx={{
                color: wsColor,
                fontWeight: 700,
                fontSize: '0.69rem',
                lineHeight: 1,
                letterSpacing: '0.03em',
              }}
            >
              {wsLabel}
            </Typography>
          </Box>
        </Tooltip>

        {/* Locale switcher */}
        <Tooltip title="Change language" arrow>
          <Box sx={{ display: 'flex', alignItems: 'center', mr: 0.5 }}>
            <LanguageIcon sx={{ fontSize: 16, color: 'text.secondary', mr: 0.5 }} />
            <Select
              value={currentLocale}
              onChange={(e) => handleLocaleChange(e.target.value as SupportedLocale)}
              size="small"
              variant="standard"
              disableUnderline
              sx={{
                fontSize: '0.72rem',
                color: 'text.secondary',
                '& .MuiSelect-select': { py: 0, pr: '20px !important' },
                '& .MuiSelect-icon': { fontSize: 16, color: 'text.secondary' },
                '&:hover .MuiSelect-select': { color: '#6C63FF' },
              }}
            >
              {SUPPORTED_LOCALES.map((loc) => (
                <MenuItem key={loc} value={loc} sx={{ fontSize: '0.8rem' }}>
                  {LOCALE_LABELS[loc]}
                </MenuItem>
              ))}
            </Select>
          </Box>
        </Tooltip>

        {/* Alert sound toggle */}
        <Tooltip title={soundEnabled ? 'Mute alert sounds' : 'Alert sounds muted — click to enable'} arrow>
          <IconButton
            onClick={toggleSound}
            size="small"
            sx={{
              color: soundEnabled ? 'text.secondary' : '#FF4560',
              transition: 'color 0.2s, background 0.2s',
              '&:hover': { color: '#6C63FF', background: 'rgba(108,99,255,0.1)' },
            }}
          >
            {soundEnabled ? <VolumeUpIcon sx={{ fontSize: 18 }} /> : <VolumeOffIcon sx={{ fontSize: 18 }} />}
          </IconButton>
        </Tooltip>

        {/* Dark/light toggle */}
        <Tooltip title={isDark ? 'Light mode' : 'Dark mode'} arrow>
          <IconButton
            onClick={toggle}
            size="small"
            sx={{
              color: 'text.secondary',
              transition: 'color 0.2s, background 0.2s',
              '&:hover': { color: '#6C63FF', background: 'rgba(108,99,255,0.1)' },
            }}
          >
            {isDark ? <LightModeIcon sx={{ fontSize: 18 }} /> : <DarkModeIcon sx={{ fontSize: 18 }} />}
          </IconButton>
        </Tooltip>

        {/* Account / profile */}
        <Tooltip title="My Account & Sessions" arrow>
          <IconButton
            onClick={() => setProfileOpen(true)}
            size="small"
            sx={{
              color: 'text.secondary',
              transition: 'color 0.2s, background 0.2s',
              '&:hover': { color: '#00D9C0', background: 'rgba(0,217,192,0.1)' },
            }}
          >
            <AccountCircleIcon sx={{ fontSize: 18 }} />
          </IconButton>
        </Tooltip>

        {/* Notification bell */}
        <Tooltip title={unreadCount ? `${unreadCount} unread alerts` : 'Notifications'} arrow>
          <IconButton
            onClick={markRead}
            size="small"
            sx={{
              color: unreadCount ? '#6C63FF' : 'text.secondary',
              transition: 'color 0.2s, background 0.2s, transform 0.15s',
              '&:hover': {
                color: unreadCount ? '#8B85FF' : theme.palette.text.primary,
                background: 'rgba(108,99,255,0.1)',
                transform: 'scale(1.05)',
              },
            }}
          >
            <Badge
              badgeContent={unreadCount}
              color="error"
              sx={{
                '& .MuiBadge-badge': {
                  boxShadow: '0 0 10px rgba(255,69,96,0.65)',
                  fontWeight: 800,
                  fontSize: '0.58rem',
                  minWidth: 15,
                  height: 15,
                  padding: '0 3px',
                },
              }}
            >
              <NotificationsIcon sx={{ fontSize: 18 }} />
            </Badge>
          </IconButton>
        </Tooltip>
      </Toolbar>
    </AppBar>

    <ProfileDialog open={profileOpen} onClose={() => setProfileOpen(false)} />
    </>
  )
}

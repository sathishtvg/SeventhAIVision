import { Box, Button, Toolbar, Tooltip, Typography } from '@mui/material'
import { Outlet, useLocation, useNavigate } from 'react-router-dom'
import ArrowBackIcon from '@mui/icons-material/ArrowBack'
import FullscreenExitIcon from '@mui/icons-material/FullscreenExit'
import { Sidebar } from './Sidebar'
import { VisitorEntryDialog } from '@/components/common/VisitorEntryDialog'
import { TopBar } from './TopBar'
import { useRealtimeEvents } from '@/hooks/useRealtimeEvents'
import { ToastContainer } from './ToastContainer'
import { useFocusModeStore } from '@/store/focusMode'

const PAGE_TITLES: Record<string, string> = {
  '/':                   'Dashboard',
  '/alerts':             'Alerts',
  '/incidents':          'Incidents',
  '/cameras':            'Cameras',
  '/live':               'Live Wall',
  '/sites':              'Sites',
  '/recordings':         'Recordings',
  '/detections':         'Detections',
  '/watchlists':         'Watchlists',
  '/zones':              'Restricted Zones',
  '/evidence':           'Evidence',
  '/audit':              'Audit Logs',
  '/analytics':          'Analytics',
  '/export':             'Data Export',
  '/settings':           'Settings',
  '/users':              'User Management',
  '/notifications':      'Notifications',
  '/tenants':            'Tenant Management',
  '/guard-ops':          'Guard Operations',
  '/reports':            'Reports',
  '/heatmap':            'Heatmap',
  '/client':             'Client Portal',
  '/api-keys':           'API Keys',
  '/ip-allowlist':       'IP Allowlist',
  '/scheduled-reports':  'Scheduled Reports',
  '/alert-dedup':        'Alert Deduplication',
}

export function AppShell() {
  useRealtimeEvents()
  const location = useLocation()
  const navigate = useNavigate()
  const title = PAGE_TITLES[location.pathname] ?? 'Seventh AI Vision'
  const isFocusMode = useFocusModeStore((s) => s.isFocusMode)
  const setFocusMode = useFocusModeStore((s) => s.setFocusMode)

  /* Entering full screen does TWO things — hides the app chrome and puts the
     window itself into fullscreen/kiosk. Undoing only the first left the
     sidebar back but the window still fullscreen; in Electron kiosk that also
     means no window frame and no taskbar, which is what "can't get back"
     actually looked like. Exit mirrors enter exactly. */
  const leaveFullScreen = async () => {
    setFocusMode(false)
    if (window.electronAPI?.setKiosk) {
      try { await window.electronAPI.setKiosk(false) } catch { /* window already normal */ }
      return
    }
    if (document.fullscreenElement) {
      try { await document.exitFullscreen() } catch { /* already exiting */ }
    }
  }

  /* Back leaves full screen AND navigates away. A popped-out monitor window
     was opened straight at its route, so it has no in-app history to go back
     through — send it to the Dashboard rather than a dead history.back(). */
  const goBack = async () => {
    await leaveFullScreen()
    if (window.history.length > 1) navigate(-1)
    else navigate('/')
  }

  return (
    <Box sx={{ display: 'flex', height: '100vh', overflow: 'hidden' }}>
      {/* Mounted outside the focus-mode branches on purpose: a vehicle arriving
          at the gate must interrupt the operator even when they are watching
          the wall full-screen, which is exactly when they'd otherwise miss it. */}
      <VisitorEntryDialog />
      {!isFocusMode && <Sidebar />}
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          display: 'flex',
          flexDirection: 'column',
          minWidth: 0,
          overflow: 'hidden',
          position: 'relative',
        }}
      >
        {!isFocusMode && (
          <>
            <TopBar title={title} />
            <Toolbar sx={{ flexShrink: 0 }} />
          </>
        )}
        {isFocusMode && (
          /* Focus-mode command strip. Deliberately IN FLOW rather than
             position:fixed — a floating control landed on top of whatever the
             page already draws top-right (every full-screen page puts its own
             action row there), which read as two overlapping exit buttons. As
             a flex row above <Outlet/> it pushes the page down instead, so it
             can never collide, and it is the single place Back and Exit live
             for every full-screen page. Pages hide their own Full Screen
             button while focus mode is on so there is exactly one of each. */
          <Box
            sx={{
              flexShrink: 0,
              display: 'flex',
              alignItems: 'center',
              gap: 1,
              px: 1.5,
              py: 0.75,
              borderBottom: '1px solid rgba(255,255,255,0.10)',
              bgcolor: 'rgba(8,8,24,0.92)',
              backdropFilter: 'blur(12px)',
            }}
          >
            <Tooltip title="Leave full screen and go back">
              <Button
                size="small"
                variant="outlined"
                startIcon={<ArrowBackIcon />}
                onClick={goBack}
                sx={{ flexShrink: 0 }}
              >
                Back
              </Button>
            </Tooltip>
            <Typography
              variant="caption"
              sx={{
                flex: 1, minWidth: 0, textAlign: 'center',
                color: 'rgba(255,255,255,0.65)',
                letterSpacing: '0.08em', textTransform: 'uppercase',
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}
            >
              {title} · Full Screen
            </Typography>
            <Tooltip title="Exit full screen (Esc)">
              <Button
                size="small"
                variant="contained"
                startIcon={<FullscreenExitIcon />}
                onClick={leaveFullScreen}
                sx={{ flexShrink: 0 }}
              >
                Exit Full Screen
              </Button>
            </Tooltip>
          </Box>
        )}
        <Box
          key={location.pathname}
          className="page-enter"
          sx={{
            flexGrow: 1,
            overflowY: 'auto',
            overflowX: 'hidden',
            p: isFocusMode ? 0 : 3,
            /* Custom scrollbar for main content */
            '&::-webkit-scrollbar': { width: '5px' },
            '&::-webkit-scrollbar-track': {
              background: 'rgba(255,255,255,0.02)',
              borderRadius: '4px',
            },
            '&::-webkit-scrollbar-thumb': {
              background: 'linear-gradient(180deg, rgba(108,99,255,0.5) 0%, rgba(0,217,192,0.3) 100%)',
              borderRadius: '4px',
            },
            '&::-webkit-scrollbar-thumb:hover': {
              background: 'linear-gradient(180deg, rgba(108,99,255,0.85) 0%, rgba(0,217,192,0.65) 100%)',
            },
            scrollbarWidth: 'thin',
            scrollbarColor: 'rgba(108,99,255,0.45) rgba(255,255,255,0.02)',
          }}
        >
          <Outlet />
        </Box>
      </Box>
      <ToastContainer />
    </Box>
  )
}

import { Box, IconButton, Toolbar, Tooltip } from '@mui/material'
import { Outlet, useLocation } from 'react-router-dom'
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
  const title = PAGE_TITLES[location.pathname] ?? 'Seventh AI Vision'
  const isFocusMode = useFocusModeStore((s) => s.isFocusMode)
  const setFocusMode = useFocusModeStore((s) => s.setFocusMode)

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
          <Tooltip title="Exit full screen">
            <IconButton
              /* Entering full screen does TWO things — hides the app chrome and
                 puts the window itself into fullscreen/kiosk. This button used
                 to undo only the first, so the sidebar came back while the
                 window stayed fullscreen. In Electron kiosk that also means no
                 window frame and no taskbar, which is what "can't get back"
                 actually looked like. Exit now mirrors enter exactly. */
              onClick={async () => {
                setFocusMode(false)
                if (window.electronAPI?.setKiosk) {
                  try { await window.electronAPI.setKiosk(false) } catch { /* window already normal */ }
                  return
                }
                if (document.fullscreenElement) {
                  try { await document.exitFullscreen() } catch { /* already exiting */ }
                }
              }}
              sx={{
                position: 'fixed', top: 12, right: 12, zIndex: 1300,
                bgcolor: 'rgba(0,0,0,0.55)', color: '#fff',
                '&:hover': { bgcolor: 'rgba(0,0,0,0.75)' },
              }}
            >
              <FullscreenExitIcon />
            </IconButton>
          </Tooltip>
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

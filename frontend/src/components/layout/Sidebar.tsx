import { useNavigate, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Box, Drawer, List, ListItemButton, ListItemIcon, ListItemText,
  Typography, Tooltip, IconButton, useTheme,
} from '@mui/material'
import DashboardIcon from '@mui/icons-material/Dashboard'
import AccessTimeIcon from '@mui/icons-material/AccessTime'
import NotificationsIcon from '@mui/icons-material/Notifications'
import ReportProblemIcon from '@mui/icons-material/ReportProblem'
import WarningAmberIcon from '@mui/icons-material/WarningAmber'
import EventBusyIcon from '@mui/icons-material/EventBusy'
import PaymentsIcon from '@mui/icons-material/Payments'
import ReceiptLongIcon from '@mui/icons-material/ReceiptLong'
import VideocamIcon from '@mui/icons-material/Videocam'
import SearchIcon from '@mui/icons-material/Search'
import DirectionsCarIcon from '@mui/icons-material/DirectionsCar'
import LocationOnIcon from '@mui/icons-material/LocationOn'
import PhotoLibraryIcon from '@mui/icons-material/PhotoLibrary'
import HistoryIcon from '@mui/icons-material/History'
import SettingsIcon from '@mui/icons-material/Settings'
import PeopleIcon from '@mui/icons-material/People'
import ShieldIcon from '@mui/icons-material/Shield'
import BarChartIcon from '@mui/icons-material/BarChart'
import NotificationsActiveIcon from '@mui/icons-material/NotificationsActive'
import BusinessIcon from '@mui/icons-material/Business'
import DownloadIcon from '@mui/icons-material/Download'
import LogoutIcon from '@mui/icons-material/Logout'
import ApartmentIcon from '@mui/icons-material/Apartment'
import LiveTvIcon from '@mui/icons-material/LiveTv'
import VideoFileIcon from '@mui/icons-material/VideoFile'
import SecurityIcon from '@mui/icons-material/Security'
import AssessmentIcon from '@mui/icons-material/Assessment'
import MapIcon from '@mui/icons-material/Map'
import VpnKeyIcon from '@mui/icons-material/VpnKey'
import LanIcon from '@mui/icons-material/Lan'
import FilterAltIcon from '@mui/icons-material/FilterAlt'
import DeveloperModeIcon from '@mui/icons-material/DeveloperMode'
import HomeWorkIcon from '@mui/icons-material/HomeWork'
import MonitorIcon from '@mui/icons-material/Monitor'
import SensorsIcon from '@mui/icons-material/Sensors'
import EngineeringIcon from '@mui/icons-material/Engineering'
import LocalParkingIcon from '@mui/icons-material/LocalParking'
import CameraAltIcon from '@mui/icons-material/CameraAlt'
import RouteIcon from '@mui/icons-material/Route'
import NotificationImportantIcon from '@mui/icons-material/NotificationImportant'
import DoorFrontIcon from '@mui/icons-material/DoorFront'
import LockOpenIcon from '@mui/icons-material/LockOpen'
import SchoolIcon from '@mui/icons-material/School'
import CampaignIcon from '@mui/icons-material/Campaign'
import PersonPinIcon from '@mui/icons-material/PersonPin'
import TaskAltIcon from '@mui/icons-material/TaskAlt'
import { useQuery } from '@tanstack/react-query'
import { getBranding } from '@/api/branding'
import { usePermission } from '@/hooks/usePermission'
import { useAuthStore } from '@/store/auth'

export const DRAWER_WIDTH = 258

const ROLE_LABELS: Record<number, string> = {
  1: 'Super Admin',
  2: 'Administrator',
  3: 'Supervisor',
  4: 'Operator',
  5: 'Security Guard',
  6: 'Viewer',
  7: 'Client',
  8: 'Manager',
}

const NAV_ITEMS = [
  { label: 'Action Center', path: '/action-center', icon: <TaskAltIcon fontSize="small" />,        permission: null },
  { label: 'Command Centre', path: '/command-centre', icon: <MonitorIcon fontSize="small" />,      permission: 'alert:read' },
  { label: 'Dashboard',   path: '/',           icon: <DashboardIcon fontSize="small" />,          permission: null },
  { label: 'Alerts',      path: '/alerts',      icon: <NotificationsIcon fontSize="small" />,      permission: 'alert:read' },
  { label: 'Incidents',   path: '/incidents',   icon: <ReportProblemIcon fontSize="small" />,      permission: 'incident:read' },
  { label: 'Live Wall',   path: '/live',        icon: <LiveTvIcon fontSize="small" />,             permission: 'camera:read' },
  { label: 'Sites',       path: '/sites',       icon: <ApartmentIcon fontSize="small" />,          permission: 'site:manage' },
  { label: 'Cameras',     path: '/cameras',     icon: <VideocamIcon fontSize="small" />,           permission: 'camera:read' },
  { label: 'Recordings',  path: '/recordings',  icon: <VideoFileIcon fontSize="small" />,          permission: 'recording:read' },
  { label: 'Playback',    path: '/playback',    icon: <VideoFileIcon fontSize="small" />,          permission: 'recording:read' },
  { label: 'Detections',  path: '/detections',  icon: <SearchIcon fontSize="small" />,             permission: 'detection:read' },
  { label: 'Watchlists',  path: '/watchlists',  icon: <DirectionsCarIcon fontSize="small" />,      permission: 'watchlist:manage' },
  { label: 'Zones',       path: '/zones',       icon: <LocationOnIcon fontSize="small" />,         permission: 'zone:manage' },
  { label: 'Evidence',    path: '/evidence',    icon: <PhotoLibraryIcon fontSize="small" />,       permission: 'evidence:read' },
  { label: 'Audit Logs',  path: '/audit',       icon: <HistoryIcon fontSize="small" />,            permission: 'audit:read' },
  { label: 'Analytics',   path: '/analytics',   icon: <BarChartIcon fontSize="small" />,           permission: 'alert:read' },
  { label: 'Export',      path: '/export',      icon: <DownloadIcon fontSize="small" />,           permission: 'alert:read' },
  { label: 'Guard Ops',  path: '/guard-ops',   icon: <SecurityIcon fontSize="small" />,           permission: 'shift:read' },
  { label: 'Roster',     path: '/roster',      icon: <SecurityIcon fontSize="small" />,           permission: 'shift:read' },
  { label: 'Attendance', path: '/attendance',  icon: <AccessTimeIcon fontSize="small" />,         permission: 'attendance:read' },
  { label: 'Violations', path: '/violations',  icon: <WarningAmberIcon fontSize="small" />,       permission: 'violation:read' },
  { label: 'Leave',      path: '/leave',       icon: <EventBusyIcon fontSize="small" />,          permission: 'leave:read' },
  { label: 'Payroll',    path: '/payroll',     icon: <PaymentsIcon fontSize="small" />,           permission: 'payroll:read' },
  { label: 'Client Invoicing', path: '/invoicing', icon: <ReceiptLongIcon fontSize="small" />,     permission: 'invoicing:read' },
  { label: 'Post Orders', path: '/post-orders', icon: <SecurityIcon fontSize="small" />,          permission: 'shift:read' },
  { label: 'Smart Facilities', path: '/iot',  icon: <SensorsIcon fontSize="small" />,            permission: 'iot:read' },
  { label: 'GPS Fleet',       path: '/gps',  icon: <DirectionsCarIcon fontSize="small" />,      permission: 'gps:read' },
  { label: 'Contractors',    path: '/contractors', icon: <EngineeringIcon fontSize="small" />,   permission: 'contractor:read' },
  { label: 'Smart Parking', path: '/parking',     icon: <LocalParkingIcon fontSize="small" />,  permission: 'parking:read' },
  { label: 'Body Cameras', path: '/bwc',         icon: <CameraAltIcon fontSize="small" />,      permission: 'bwc:read' },
  { label: 'Tour Compliance', path: '/compliance', icon: <RouteIcon fontSize="small" />,          permission: 'compliance:read' },
  { label: 'Alarm Panels',   path: '/alarms',     icon: <NotificationImportantIcon fontSize="small" />, permission: 'alarm:read' },
  { label: 'Access Control', path: '/access',     icon: <DoorFrontIcon fontSize="small" />,             permission: 'access:read' },
  { label: 'Barriers',       path: '/barriers',   icon: <LockOpenIcon fontSize="small" />,              permission: 'barrier:read' },
  { label: 'Site Map',       path: '/map',        icon: <MapIcon fontSize="small" />,                   permission: 'camera:read' },
  { label: 'Guard Training',    path: '/training',       icon: <SchoolIcon fontSize="small" />,   permission: 'training:read' },
  { label: 'Emergency Alert',   path: '/emergency',      icon: <CampaignIcon fontSize="small" />, permission: 'broadcast:read' },
  { label: 'Visitor Pre-Reg',   path: '/visitor-prereg', icon: <PersonPinIcon fontSize="small" />, permission: 'visitor:read' },
  { label: 'Reports',    path: '/reports',     icon: <AssessmentIcon fontSize="small" />,         permission: 'dob:read' },
  { label: 'Heatmap',    path: '/heatmap',     icon: <MapIcon fontSize="small" />,                permission: 'alert:read' },
]

const ADMIN_ITEMS = [
  { label: 'Tenants',       path: '/tenants',       icon: <BusinessIcon fontSize="small" />,          permission: 'tenant:manage' },
  { label: 'Notifications', path: '/notifications', icon: <NotificationsActiveIcon fontSize="small" />, permission: 'notification:manage' },
  { label: 'Settings',      path: '/settings',      icon: <SettingsIcon fontSize="small" />,           permission: 'settings:read' },
  { label: 'Users',         path: '/users',         icon: <PeopleIcon fontSize="small" />,             permission: 'user:read' },
  { label: 'Roles',         path: '/roles',         icon: <PeopleIcon fontSize="small" />,             permission: 'role:manage' },
  { label: 'API Keys',      path: '/api-keys',      icon: <VpnKeyIcon fontSize="small" />,             permission: 'apikey:manage' },
  { label: 'IP Allowlist',  path: '/ip-allowlist',  icon: <LanIcon fontSize="small" />,                permission: 'iplist:manage' },
  { label: 'Sched. Reports', path: '/scheduled-reports', icon: <AssessmentIcon fontSize="small" />,   permission: 'report:schedule' },
  { label: 'Alert Dedup',   path: '/alert-dedup',       icon: <FilterAltIcon fontSize="small" />,      permission: 'alert:dedup:manage' },
  { label: 'Developer',     path: '/developer',         icon: <DeveloperModeIcon fontSize="small" />,  permission: 'apikey:manage' },
  { label: 'Client Portal', path: '/client',            icon: <HomeWorkIcon fontSize="small" />,       permission: 'portal:view' },
]

interface NavLinkProps {
  label: string
  path: string
  icon: React.ReactNode
  permission: string | null
}

/** English nav label → i18n key, e.g. "Audit Logs" → "nav.audit_logs". */
function navKey(label: string): string {
  return 'nav.' + label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/(^_|_$)/g, '')
}

function NavLink({ label, path, icon, permission }: NavLinkProps) {
  const allowed = usePermission(permission ?? 'camera:read')
  const location = useLocation()
  const navigate = useNavigate()
  const { t } = useTranslation()
  const { palette } = useTheme()
  const isDark = palette.mode === 'dark'
  const isAllowed = permission === null || allowed
  if (!isAllowed) return null
  // Translated label, falling back to the English string for any untranslated
  // nav item (Gap 93).
  const displayLabel = t(navKey(label), { defaultValue: label })

  const selected =
    location.pathname === path ||
    (path !== '/' && location.pathname.startsWith(path))

  const hoverBg = isDark ? 'rgba(255,255,255,0.045)' : 'rgba(0,0,0,0.035)'

  return (
    <ListItemButton
      selected={selected}
      onClick={() => navigate(path)}
      className={selected ? 'nav-item-enter' : undefined}
      sx={{
        mx: 1.5,
        mb: 0.25,
        borderRadius: '10px',
        position: 'relative',
        overflow: 'hidden',
        py: 0.9,
        transition: 'all 0.18s ease',
        ...(selected
          ? {
              background:
                'linear-gradient(90deg, rgba(108,99,255,0.2) 0%, rgba(108,99,255,0.04) 100%)',
              '&::before': {
                content: '""',
                position: 'absolute',
                left: 0,
                top: '18%',
                bottom: '18%',
                width: '3px',
                borderRadius: '0 3px 3px 0',
                background: 'linear-gradient(180deg, #6C63FF 0%, #00D9C0 100%)',
                boxShadow: '0 0 10px rgba(108,99,255,0.9)',
              },
              '&:hover': {
                background:
                  'linear-gradient(90deg, rgba(108,99,255,0.26) 0%, rgba(108,99,255,0.08) 100%)',
              },
            }
          : {
              '&:hover': {
                background: hoverBg,
                transform: 'translateX(2px)',
              },
            }),
      }}
    >
      <ListItemIcon
        sx={{
          minWidth: 34,
          color: selected ? '#6C63FF' : 'text.secondary',
          transition: 'color 0.18s, filter 0.18s',
          filter: selected
            ? 'drop-shadow(0 0 5px rgba(108,99,255,0.85))'
            : 'none',
        }}
      >
        {icon}
      </ListItemIcon>

      <ListItemText
        primary={displayLabel}
        slotProps={{
          primary: {
            sx: {
              fontSize: '0.845rem',
              fontWeight: selected ? 700 : 400,
              color: selected ? 'text.primary' : 'text.secondary',
              transition: 'color 0.18s, font-weight 0.18s',
              letterSpacing: selected ? '0.01em' : 0,
            },
          },
        }}
      />

      {selected && (
        <Box
          sx={{
            width: 5,
            height: 5,
            borderRadius: '50%',
            flexShrink: 0,
            background: 'linear-gradient(135deg, #6C63FF 0%, #00D9C0 100%)',
            boxShadow: '0 0 7px rgba(108,99,255,0.9)',
          }}
        />
      )}
    </ListItemButton>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <Typography
      variant="caption"
      sx={{
        px: 2.5,
        pt: 1.75,
        pb: 0.5,
        display: 'block',
        fontSize: '0.635rem',
        fontWeight: 700,
        letterSpacing: '0.12em',
        textTransform: 'uppercase',
        color: 'text.disabled',
        userSelect: 'none',
      }}
    >
      {children}
    </Typography>
  )
}

export function Sidebar() {
  const user   = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const { palette } = useTheme()
  const isDark = palette.mode === 'dark'

  const { data: tenantBrand } = useQuery({
    queryKey: ['branding'],
    queryFn: getBranding,
    staleTime: 5 * 60 * 1000,
  })

  const brandLogoUrl   = tenantBrand?.branding?.logo_url     ?? null
  const brandName      = tenantBrand?.branding?.company_name || tenantBrand?.name || '7th AI Vision'
  const accentColor    = tenantBrand?.branding?.primary_color ?? '#6C63FF'

  const initials = 'U'
  const roleLabel = user ? (ROLE_LABELS[user.roleId] ?? 'User') : 'User'

  const divider = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.07)'
  const logoTitleGradient = isDark
    ? `linear-gradient(90deg, #ffffff 30%, ${accentColor}d9 100%)`
    : `linear-gradient(90deg, #1a1a4e 30%, ${accentColor} 100%)`

  return (
    <Drawer
      variant="permanent"
      sx={{
        width: DRAWER_WIDTH,
        flexShrink: 0,
        '& .MuiDrawer-paper': { width: DRAWER_WIDTH, boxSizing: 'border-box', overflow: 'hidden' },
      }}
    >
      {/* ── Logo ─────────────────────────────────────────────────────────── */}
      <Box
        sx={{
          px: 2.5,
          py: 2.25,
          display: 'flex',
          alignItems: 'center',
          gap: 1.5,
          background: `linear-gradient(180deg, ${accentColor}1a 0%, transparent 100%)`,
          borderBottom: `1px solid ${divider}`,
          flexShrink: 0,
        }}
      >
        <Box
          sx={{
            width: 38,
            height: 38,
            borderRadius: '11px',
            background: brandLogoUrl
              ? 'rgba(255,255,255,0.06)'
              : `linear-gradient(135deg, ${accentColor} 0%, #00D9C0 100%)`,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            overflow: 'hidden',
            flexShrink: 0,
            boxShadow: brandLogoUrl
              ? `0 4px 14px ${accentColor}44`
              : `0 4px 14px ${accentColor}80, inset 0 1px 0 rgba(255,255,255,0.25)`,
          }}
        >
          {brandLogoUrl ? (
            <Box
              component="img"
              src={brandLogoUrl}
              alt="Logo"
              sx={{ width: '100%', height: '100%', objectFit: 'contain', p: 0.5 }}
            />
          ) : (
            <ShieldIcon sx={{ fontSize: 20, color: '#fff' }} />
          )}
        </Box>

        <Box sx={{ minWidth: 0 }}>
          <Typography
            variant="subtitle1"
            noWrap
            sx={{
              fontWeight: 800,
              fontSize: '0.95rem',
              lineHeight: 1.25,
              background: logoTitleGradient,
              WebkitBackgroundClip: 'text',
              WebkitTextFillColor: 'transparent',
              backgroundClip: 'text',
            }}
          >
            {brandName}
          </Typography>
          <Typography
            variant="caption"
            noWrap
            sx={{ color: 'text.secondary', fontSize: '0.63rem', lineHeight: 1 }}
          >
            Security Platform
          </Typography>
        </Box>
      </Box>

      {/* ── Navigation ───────────────────────────────────────────────────── */}
      <Box
        sx={{
          flex: 1,
          overflowY: 'auto',
          overflowX: 'hidden',
          py: 0.5,
          /* Thin glass scrollbar — barely visible, glows violet on hover */
          '&::-webkit-scrollbar': { width: '3px' },
          '&::-webkit-scrollbar-track': { background: 'transparent' },
          '&::-webkit-scrollbar-thumb': {
            background: 'rgba(108,99,255,0.25)',
            borderRadius: '4px',
            transition: 'background 0.2s',
          },
          '&:hover::-webkit-scrollbar-thumb': {
            background: 'linear-gradient(180deg, rgba(108,99,255,0.7) 0%, rgba(0,217,192,0.5) 100%)',
          },
          scrollbarWidth: 'thin',
          scrollbarColor: 'rgba(108,99,255,0.25) transparent',
        }}
      >
        <SectionLabel>Monitoring</SectionLabel>
        <List dense disablePadding>
          {NAV_ITEMS.map((item) => (
            <NavLink key={item.path} {...item} />
          ))}
        </List>

        <SectionLabel>Administration</SectionLabel>
        <List dense disablePadding>
          {ADMIN_ITEMS.map((item) => (
            <NavLink key={item.path} {...item} />
          ))}
        </List>
      </Box>

      {/* ── User footer ──────────────────────────────────────────────────── */}
      <Box
        sx={{
          px: 1.5,
          py: 1.5,
          borderTop: `1px solid ${divider}`,
          background:
            'linear-gradient(0deg, rgba(108,99,255,0.07) 0%, transparent 100%)',
          flexShrink: 0,
        }}
      >
        <Box
          sx={{
            display: 'flex',
            alignItems: 'center',
            gap: 1.25,
            px: 1,
            py: 0.75,
            borderRadius: '10px',
            background: isDark ? 'rgba(255,255,255,0.03)' : 'rgba(0,0,0,0.03)',
            border: `1px solid ${divider}`,
          }}
        >
          {/* Avatar */}
          <Box
            sx={{
              width: 32,
              height: 32,
              borderRadius: '9px',
              background: 'linear-gradient(135deg, #6C63FF 0%, #00D9C0 100%)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              fontSize: '0.8rem',
              fontWeight: 700,
              color: '#fff',
              flexShrink: 0,
              boxShadow: '0 2px 8px rgba(108,99,255,0.45)',
              letterSpacing: 0,
            }}
          >
            {initials}
          </Box>

          {/* Info */}
          <Box sx={{ flex: 1, minWidth: 0 }}>
            <Typography
              variant="caption"
              noWrap
              sx={{
                display: 'block',
                fontWeight: 600,
                fontSize: '0.78rem',
                color: 'text.primary',
                lineHeight: 1.35,
              }}
            >
              {roleLabel}
            </Typography>
            <Typography
              variant="caption"
              noWrap
              sx={{ display: 'block', color: 'text.secondary', fontSize: '0.64rem' }}
            >
              {user?.id ? 'Authenticated' : 'Guest'}
            </Typography>
          </Box>

          {/* Logout */}
          <Tooltip title="Sign out" placement="top">
            <IconButton
              size="small"
              onClick={logout}
              sx={{
                color: 'text.secondary',
                flexShrink: 0,
                transition: 'color 0.18s, background 0.18s',
                '&:hover': {
                  color: '#FF4560',
                  background: 'rgba(255,69,96,0.12)',
                },
              }}
            >
              <LogoutIcon sx={{ fontSize: 16 }} />
            </IconButton>
          </Tooltip>
        </Box>
      </Box>
    </Drawer>
  )
}

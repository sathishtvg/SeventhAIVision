import { useEffect, useMemo, useState } from 'react'
import { useNavigate, useLocation } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import {
  Box, Collapse, Drawer, List, ListItemButton, ListItemIcon, ListItemText,
  Typography, Tooltip, IconButton, useTheme,
} from '@mui/material'
import ChevronLeftIcon from '@mui/icons-material/ChevronLeft'
import ChevronRightIcon from '@mui/icons-material/ChevronRight'
import ExpandMoreIcon from '@mui/icons-material/ExpandMore'
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
import RuleIcon from '@mui/icons-material/Rule'
import CableIcon from '@mui/icons-material/Cable'
import { useQuery } from '@tanstack/react-query'
import { getBranding } from '@/api/branding'
import { PRODUCT_NAME } from '@/lib/brand'
import { usePermission, getPermissionsForRole } from '@/hooks/usePermission'
import { useAuthStore } from '@/store/auth'

export const DRAWER_WIDTH = 258
/** Width when collapsed to an icon rail. Wide enough for a 38px icon plus the
 *  same horizontal rhythm as the expanded state, so the logo and nav icons
 *  don't visibly shift sideways when toggling. */
export const RAIL_WIDTH = 72

/** Rail on/off, and which section headings are open. Persisted so an
 *  operator's chosen shape survives a reload — a control room leaves this app
 *  open for a whole shift and re-folding the same groups every morning is
 *  exactly the kind of small tax that makes software feel cheap. */
const RAIL_KEY = 'sidebar-rail'
const OPEN_KEY = 'sidebar-open-sections'
/** Superseded by OPEN_KEY. Stored the inverse (which sections were shut), so
 *  reading it as an open-set would flip every saved preference. Cleared on
 *  load rather than migrated: one group's fold state is not worth carrying a
 *  translation shim for. */
const LEGACY_COLLAPSED_KEY = 'sidebar-collapsed-sections'

/** Which sections are open. Empty by default — everything starts folded and
 *  the group holding the current route opens itself (see the effect below),
 *  so you land on a short menu showing where you actually are instead of all
 *  53 items at once.
 *
 *  Storing the open set rather than the collapsed one is what makes that
 *  default work: an unknown section is closed, so a group added in a later
 *  release doesn't force itself open on everyone. */
function loadOpenSections(): Set<string> {
  try {
    localStorage.removeItem(LEGACY_COLLAPSED_KEY)
    const raw = localStorage.getItem(OPEN_KEY)
    return new Set<string>(raw ? JSON.parse(raw) : [])
  } catch {
    return new Set()
  }
}

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

/**
 * Navigation, grouped by the JOB SOMEONE IS DOING rather than by feature area.
 *
 * This was previously two groups — "Monitoring" holding 40 entries and
 * "Administration" holding 13. At that length a sidebar stops being a menu and
 * becomes a list you read top to bottom every time, which is exactly the
 * complaint: everything together, so you hunt.
 *
 * The grouping answers "what am I here to do?":
 *   Monitoring        — what is happening right now (the control-room loop)
 *   Investigate       — what happened earlier (look something up after the fact)
 *   Guard Operations  — the workforce: who is on, who is late, who gets paid
 *   Sites & Devices   — the physical estate and the hardware on it
 *   People & Vehicles — who and what is expected or watched for
 *   Reports & Billing — what leaves the system: exports, invoices, audit
 *   Configuration     — set-up, done rarely, by an admin
 *
 * No item was added, removed, or re-pathed — only regrouped and reordered, so
 * every route and permission behaves exactly as before.
 *
 * Ordering inside each group is by how often it is opened, not alphabetically:
 * the thing you reach for hourly sits above the thing you touch monthly.
 */
const NAV_SECTIONS: {
  title: string
  items: { label: string; path: string; icon: React.ReactNode; permission: string | null }[]
}[] = [
  {
    title: 'Monitoring',
    items: [
      { label: 'Action Center', path: '/action-center', icon: <TaskAltIcon fontSize="small" />,     permission: null },
      { label: 'Command Centre', path: '/command-centre', icon: <MonitorIcon fontSize="small" />,   permission: 'alert:read' },
      { label: 'Dashboard',      path: '/',            icon: <DashboardIcon fontSize="small" />,    permission: null },
      { label: 'Alerts',         path: '/alerts',      icon: <NotificationsIcon fontSize="small" />, permission: 'alert:read' },
      { label: 'Incidents',      path: '/incidents',   icon: <ReportProblemIcon fontSize="small" />, permission: 'incident:read' },
      { label: 'Live Wall',      path: '/live',        icon: <LiveTvIcon fontSize="small" />,       permission: 'camera:read' },
      { label: 'Site Map',       path: '/map',         icon: <MapIcon fontSize="small" />,          permission: 'camera:read' },
      { label: 'Emergency Alert', path: '/emergency',  icon: <CampaignIcon fontSize="small" />,     permission: 'broadcast:read' },
    ],
  },
  {
    title: 'Investigate',
    items: [
      { label: 'Detections',  path: '/detections', icon: <SearchIcon fontSize="small" />,        permission: 'detection:read' },
      { label: 'Evidence',    path: '/evidence',   icon: <PhotoLibraryIcon fontSize="small" />,  permission: 'evidence:read' },
      { label: 'Recordings',  path: '/recordings', icon: <VideoFileIcon fontSize="small" />,     permission: 'recording:read' },
      { label: 'Playback',    path: '/playback',   icon: <VideoFileIcon fontSize="small" />,     permission: 'recording:read' },
      { label: 'Analytics',   path: '/analytics',  icon: <BarChartIcon fontSize="small" />,      permission: 'alert:read' },
      { label: 'Heatmap',     path: '/heatmap',    icon: <MapIcon fontSize="small" />,           permission: 'alert:read' },
    ],
  },
  {
    title: 'Guard Operations',
    items: [
      { label: 'Attendance',      path: '/attendance',  icon: <AccessTimeIcon fontSize="small" />,   permission: 'attendance:read' },
      { label: 'Roster',          path: '/roster',      icon: <SecurityIcon fontSize="small" />,     permission: 'shift:read' },
      { label: 'Guard Ops',       path: '/guard-ops',   icon: <SecurityIcon fontSize="small" />,     permission: 'shift:read' },
      { label: 'Tour Compliance', path: '/compliance',  icon: <RouteIcon fontSize="small" />,        permission: 'compliance:read' },
      { label: 'Violations',      path: '/violations',  icon: <WarningAmberIcon fontSize="small" />, permission: 'violation:read' },
      { label: 'Leave',           path: '/leave',       icon: <EventBusyIcon fontSize="small" />,    permission: 'leave:read' },
      { label: 'Payroll',         path: '/payroll',     icon: <PaymentsIcon fontSize="small" />,     permission: 'payroll:read' },
      { label: 'Guard Training',  path: '/training',    icon: <SchoolIcon fontSize="small" />,       permission: 'training:read' },
      { label: 'SOP',              path: '/post-orders', icon: <SecurityIcon fontSize="small" />,     permission: 'shift:read' },
    ],
  },
  {
    title: 'Sites & Devices',
    items: [
      { label: 'Sites',            path: '/sites',    icon: <ApartmentIcon fontSize="small" />,   permission: 'site:manage' },
      { label: 'Cameras',          path: '/cameras',  icon: <VideocamIcon fontSize="small" />,    permission: 'camera:read' },
      { label: 'Zones',            path: '/zones',    icon: <LocationOnIcon fontSize="small" />,  permission: 'zone:manage' },
      { label: 'Barriers',         path: '/barriers', icon: <LockOpenIcon fontSize="small" />,    permission: 'barrier:read' },
      { label: 'Access Control',   path: '/access',   icon: <DoorFrontIcon fontSize="small" />,   permission: 'access:read' },
      { label: 'Alarm Panels',     path: '/alarms',   icon: <NotificationImportantIcon fontSize="small" />, permission: 'alarm:read' },
      { label: 'Smart Parking',    path: '/parking',  icon: <LocalParkingIcon fontSize="small" />, permission: 'parking:read' },
      { label: 'Smart Facilities', path: '/iot',      icon: <SensorsIcon fontSize="small" />,     permission: 'iot:read' },
      { label: 'Body Cameras',     path: '/bwc',      icon: <CameraAltIcon fontSize="small" />,   permission: 'bwc:read' },
      { label: 'GPS Fleet',        path: '/gps',      icon: <DirectionsCarIcon fontSize="small" />, permission: 'gps:read' },
    ],
  },
  {
    title: 'People & Vehicles',
    items: [
      { label: 'Visitor Pre-Reg', path: '/visitor-prereg', icon: <PersonPinIcon fontSize="small" />,     permission: 'visitor:read' },
      { label: 'Watchlists',      path: '/watchlists',     icon: <DirectionsCarIcon fontSize="small" />, permission: 'watchlist:manage' },
      { label: 'Contractors',     path: '/contractors',    icon: <EngineeringIcon fontSize="small" />,   permission: 'contractor:read' },
    ],
  },
  {
    title: 'Reports & Billing',
    items: [
      { label: 'Reports',          path: '/reports',           icon: <AssessmentIcon fontSize="small" />, permission: 'dob:read' },
      { label: 'Sched. Reports',   path: '/scheduled-reports', icon: <AssessmentIcon fontSize="small" />, permission: 'report:schedule' },
      { label: 'Export',           path: '/export',            icon: <DownloadIcon fontSize="small" />,   permission: 'alert:read' },
      { label: 'Client Invoicing', path: '/invoicing',         icon: <ReceiptLongIcon fontSize="small" />, permission: 'invoicing:read' },
      { label: 'Client Portal',    path: '/client',            icon: <HomeWorkIcon fontSize="small" />,   permission: 'portal:view' },
      { label: 'Audit Logs',       path: '/audit',             icon: <HistoryIcon fontSize="small" />,    permission: 'audit:read' },
    ],
  },
  {
    title: 'Configuration',
    items: [
      { label: 'Settings',         path: '/settings',         icon: <SettingsIcon fontSize="small" />,     permission: 'settings:read' },
      { label: 'Alert Rules',      path: '/alert-rules',      icon: <RuleIcon fontSize="small" />,         permission: 'alert_rule:read' },
      { label: 'Device Protocols', path: '/device-protocols', icon: <CableIcon fontSize="small" />,        permission: 'device_config:read' },
      { label: 'Notifications',    path: '/notifications',    icon: <NotificationsActiveIcon fontSize="small" />, permission: 'notification:manage' },
      { label: 'Users',            path: '/users',            icon: <PeopleIcon fontSize="small" />,       permission: 'user:read' },
      { label: 'Roles',            path: '/roles',            icon: <PeopleIcon fontSize="small" />,       permission: 'role:manage' },
      // Alert Dedup, API Keys, IP Allowlist and Developer were dropped from
      // the sidebar: setup-once plumbing that earned a permanent slot in a
      // menu an operator reads every day. Their routes are untouched and are
      // now reached from Settings → Advanced, so nothing became unreachable.
      { label: 'Tenants',          path: '/tenants',          icon: <BusinessIcon fontSize="small" />,     permission: 'tenant:manage' },
    ],
  },
]

interface NavLinkProps {
  label: string
  path: string
  icon: React.ReactNode
  permission: string | null
  /** Icon-only rail mode: the label moves into a tooltip. */
  rail?: boolean
}

/** English nav label → i18n key, e.g. "Audit Logs" → "nav.audit_logs". */
function navKey(label: string): string {
  return 'nav.' + label.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/(^_|_$)/g, '')
}

function NavLink({ label, path, icon, permission, rail = false }: NavLinkProps) {
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

  const button = (
    <ListItemButton
      selected={selected}
      onClick={() => navigate(path)}
      className={selected ? 'nav-item-enter' : undefined}
      sx={{
        mx: rail ? 1 : 1.5,
        mb: 0.25,
        borderRadius: '10px',
        position: 'relative',
        overflow: 'hidden',
        py: 0.9,
        ...(rail && { justifyContent: 'center', px: 0 }),
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
        sx={(theme) => ({
          minWidth: rail ? 0 : 34,
          // Icons sat on text.secondary, the same value as the label beside
          // them, and read as noticeably fainter: a glyph is thin strokes and
          // a few outlines where a word is solid letterforms, so matching the
          // label's alpha under-serves the icon. They get their own step,
          // brighter than the label but still clearly below the selected
          // state. Selected follows the theme's primary rather than a fixed
          // #6C63FF, so a tenant's brand colour actually reaches it.
          color: selected
            ? theme.palette.primary.main
            : theme.palette.mode === 'dark'
              ? 'rgba(248,250,252,0.82)'
              : 'rgba(15,23,42,0.72)',
          transition: 'color 0.18s, filter 0.18s',
          filter: selected
            ? `drop-shadow(0 0 5px ${theme.palette.primary.main}d9)`
            : 'none',
        })}
      >
        {icon}
      </ListItemIcon>

      {!rail && (
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
      )}

      {selected && !rail && (
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

  // In rail mode the label is the only thing identifying the item, so it has
  // to survive somewhere — a tooltip is that somewhere.
  return rail
    ? <Tooltip title={displayLabel} placement="right" arrow>{button}</Tooltip>
    : button
}

/** Section heading, now a collapse toggle.
 *
 *  A real button rather than a styled div: it is keyboard-reachable, announces
 *  its expanded state to a screen reader, and gets focus styling for free —
 *  none of which a clickable Typography would have. */
function SectionHeader({
  title, open, count, onToggle,
}: { title: string; open: boolean; count: number; onToggle: () => void }) {
  return (
    <ListItemButton
      onClick={onToggle}
      aria-expanded={open}
      sx={{
        px: 2.5,
        pt: 1.75,
        pb: 0.5,
        borderRadius: 0,
        '&:hover': { background: 'transparent', '& .section-title': { color: 'text.secondary' } },
      }}
    >
      <Typography
        className="section-title"
        variant="caption"
        sx={{
          flex: 1,
          fontSize: '0.635rem',
          fontWeight: 700,
          letterSpacing: '0.12em',
          textTransform: 'uppercase',
          color: 'text.disabled',
          userSelect: 'none',
          transition: 'color 0.18s',
        }}
      >
        {title}
      </Typography>
      {/* Item count while shut, so a collapsed group still says how much is
          behind it instead of reading as an empty heading. */}
      {!open && (
        <Typography
          variant="caption"
          sx={{ fontSize: '0.6rem', color: 'text.disabled', mr: 0.75, opacity: 0.7 }}
        >
          {count}
        </Typography>
      )}
      <ExpandMoreIcon
        sx={{
          fontSize: 16,
          color: 'text.disabled',
          transition: 'transform 0.2s ease',
          transform: open ? 'rotate(0deg)' : 'rotate(-90deg)',
        }}
      />
    </ListItemButton>
  )
}

export function Sidebar() {
  const user   = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const { palette } = useTheme()
  const isDark = palette.mode === 'dark'
  const location = useLocation()

  const [rail, setRail] = useState(() => localStorage.getItem(RAIL_KEY) === '1')
  const [openSections, setOpenSections] = useState<Set<string>>(loadOpenSections)

  const toggleRail = () => {
    setRail((prev) => {
      localStorage.setItem(RAIL_KEY, prev ? '0' : '1')
      return !prev
    })
  }

  const toggleSection = (title: string) => {
    setOpenSections((prev) => {
      const next = new Set(prev)
      if (next.has(title)) next.delete(title)
      else next.add(title)
      localStorage.setItem(OPEN_KEY, JSON.stringify([...next]))
      return next
    })
  }

  // Drop any section this role cannot see a single item in, so a limited role
  // (a guard, a client) gets a short menu rather than a page of empty headings.
  // NavLink still gates each item itself — this only decides whether the
  // heading is worth drawing.
  // Must resolve permissions the SAME way usePermission does: the
  // backend-fetched set first, the hardcoded matrix only as a fallback.
  // Reading the matrix alone silently hides any nav item whose permission
  // code isn't mirrored there, and hides everything for a custom role
  // (Gap 91 — custom role ids aren't in the built-in matrix at all).
  const fetchedPermissions = useAuthStore((s) => s.permissions)
  const visibleSections = useMemo(() => {
    const granted = new Set(
      fetchedPermissions ??
      (user?.roleId != null ? getPermissionsForRole(user.roleId) : []),
    )
    return NAV_SECTIONS
      .map((section) => ({
        ...section,
        items: section.items.filter((i) => i.permission === null || granted.has(i.permission)),
      }))
      .filter((section) => section.items.length > 0)
  }, [fetchedPermissions, user?.roleId])

  // The group holding the current route opens itself. This is what makes an
  // all-folded default usable: you always see the section you are working in,
  // and navigating into a folded group (from a KPI card, a deep link, a
  // pop-out window) never leaves you on a page whose nav entry is hidden.
  useEffect(() => {
    const owning = visibleSections.find((s) =>
      s.items.some((i) =>
        location.pathname === i.path ||
        (i.path !== '/' && location.pathname.startsWith(i.path)),
      ),
    )
    if (!owning) return
    setOpenSections((prev) => {
      if (prev.has(owning.title)) return prev   // already open — no re-render
      const next = new Set(prev)
      next.add(owning.title)
      localStorage.setItem(OPEN_KEY, JSON.stringify([...next]))
      return next
    })
  }, [location.pathname, visibleSections])

  const { data: tenantBrand } = useQuery({
    queryKey: ['branding'],
    queryFn: getBranding,
    staleTime: 5 * 60 * 1000,
  })

  const brandLogoUrl   = tenantBrand?.branding?.logo_url     ?? null
  const brandName      = tenantBrand?.branding?.company_name || tenantBrand?.name || PRODUCT_NAME
  const accentColor    = tenantBrand?.branding?.primary_color ?? '#6C63FF'

  const initials = 'U'
  const roleLabel = user ? (ROLE_LABELS[user.roleId] ?? 'User') : 'User'

  const divider = isDark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.07)'

  return (
    <Drawer
      variant="permanent"
      sx={{
        width: rail ? RAIL_WIDTH : DRAWER_WIDTH,
        flexShrink: 0,
        transition: 'width 0.2s ease',
      }}
      // Width goes on the paper's own slot rather than through a
      // `& .MuiDrawer-paper` descendant selector in the root's sx. Both work;
      // this form keeps the width on the paper's own class, so the rendered
      // width is inspectable without reasoning about selector specificity
      // against the theme's MuiDrawer.paper overrides.
      slotProps={{
        paper: {
          sx: {
            width: rail ? RAIL_WIDTH : DRAWER_WIDTH,
            boxSizing: 'border-box',
            overflow: 'hidden',
            transition: 'width 0.2s ease',
          },
        },
      }}
    >
      {/* ── Logo ─────────────────────────────────────────────────────────── */}
      <Box
        sx={{
          px: rail ? 1 : 2.5,
          py: 2.25,
          display: 'flex',
          alignItems: 'center',
          justifyContent: rail ? 'center' : 'flex-start',
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

        {!rail && (
          <>
            <Box sx={{ minWidth: 0, flex: 1 }}>
              <Typography
                variant="subtitle1"
                noWrap
                sx={{
                  fontWeight: 800,
                  fontSize: '0.95rem',
                  lineHeight: 1.25,
                  color: 'text.primary',
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
            <Tooltip title="Collapse menu" placement="right">
              <IconButton
                size="small"
                onClick={toggleRail}
                aria-label="Collapse menu"
                sx={{ color: 'text.disabled', '&:hover': { color: 'text.primary' } }}
              >
                <ChevronLeftIcon fontSize="small" />
              </IconButton>
            </Tooltip>
          </>
        )}
      </Box>

      {/* In rail mode the expand control gets its own row: putting it beside
          the logo would leave neither enough width at 72px. */}
      {rail && (
        <Tooltip title="Expand menu" placement="right">
          <IconButton
            size="small"
            onClick={toggleRail}
            aria-label="Expand menu"
            sx={{
              alignSelf: 'center',
              mt: 1,
              color: 'text.disabled',
              '&:hover': { color: 'text.primary' },
            }}
          >
            <ChevronRightIcon fontSize="small" />
          </IconButton>
        </Tooltip>
      )}

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
        {/* A section whose every item is permission-denied would otherwise
            render as a bare heading with nothing under it — worse than the
            flat list it replaced. Visibility is resolved with the pure
            getPermissionsForRole rather than the usePermission hook, because
            a hook cannot be called per-item inside a map. */}
        {visibleSections.map((section, idx) => {
          // The rail has no room for headings, so it shows every item as a
          // flat icon run with a hairline between groups — collapsing there
          // would hide items behind a control that isn't visible.
          if (rail) {
            return (
              <Box
                key={section.title}
                sx={{
                  pt: idx === 0 ? 0.5 : 1,
                  mt: idx === 0 ? 0 : 0.5,
                  borderTop: idx === 0 ? 'none' : `1px solid ${divider}`,
                }}
              >
                <List dense disablePadding>
                  {section.items.map((item) => (
                    <NavLink key={item.path} {...item} rail />
                  ))}
                </List>
              </Box>
            )
          }
          const open = openSections.has(section.title)
          return (
            <Box key={section.title}>
              <SectionHeader
                title={section.title}
                open={open}
                count={section.items.length}
                onToggle={() => toggleSection(section.title)}
              />
              <Collapse in={open} timeout={180} unmountOnExit>
                <List dense disablePadding>
                  {section.items.map((item) => (
                    <NavLink key={item.path} {...item} />
                  ))}
                </List>
              </Collapse>
            </Box>
          )
        })}
      </Box>

      {/* ── User footer ──────────────────────────────────────────────────── */}
      <Box
        sx={{
          px: rail ? 0.75 : 1.5,
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
            flexDirection: rail ? 'column' : 'row',
            alignItems: 'center',
            gap: rail ? 0.75 : 1.25,
            px: rail ? 0.5 : 1,
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

          {/* Info — the role is what the avatar's initial can't convey, so in
              rail mode it moves onto the avatar's own tooltip rather than
              disappearing. */}
          {!rail && (
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
          )}

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

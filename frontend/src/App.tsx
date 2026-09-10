import { Navigate, Route, Routes, useLocation } from 'react-router-dom'
import { useEffect } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useAuthStore } from '@/store/auth'
import { useColorMode } from '@/context/ColorMode'
import { getBranding } from '@/api/branding'
import { setTenantTimeZone, installTenantTimeZoneDefault } from '@/lib/tenantTime'
import { AppShell } from '@/components/layout/AppShell'
import Login from '@/pages/Login'
import ForgotPassword from '@/pages/ForgotPassword'
import ResetPassword from '@/pages/ResetPassword'
import Dashboard from '@/pages/Dashboard'
import Alerts from '@/pages/Alerts'
import Incidents from '@/pages/Incidents'
import Cameras from '@/pages/Cameras'
import Detections from '@/pages/Detections'
import Watchlists from '@/pages/Watchlists'
import Zones from '@/pages/Zones'
import Evidence from '@/pages/Evidence'
import Audit from '@/pages/Audit'
import Analytics from '@/pages/Analytics'
import Export from '@/pages/Export'
import Notifications from '@/pages/Notifications'
import Settings from '@/pages/Settings'
import Tenants from '@/pages/Tenants'
import SupportSessions from '@/pages/SupportSessions'
import PlatformDashboard from '@/pages/PlatformDashboard'
import TenantUsage from '@/pages/TenantUsage'
import ErrorCentre from '@/pages/ErrorCentre'
import PlatformBilling from '@/pages/PlatformBilling'
import PlatformInvoices from '@/pages/PlatformInvoices'
import PlatformAnalytics from '@/pages/PlatformAnalytics'
import Users from '@/pages/Users'
import Roles from '@/pages/Roles'
import { SitesPage } from '@/pages/Sites'
import { DutyAssignmentsPage } from '@/pages/DutyAssignments'
import { ShiftsPage } from '@/pages/Shifts'
import { LiveWallPage } from '@/pages/LiveWall'
import { RecordingsPage } from '@/pages/Recordings'
import { PlaybackPage } from '@/pages/Playback'
import { RosterPage } from '@/pages/Roster'
import { AttendancePage } from '@/pages/Attendance'
import { ViolationsPage } from '@/pages/Violations'
import { BarriersPage } from '@/pages/Barriers'
import { MapViewPage } from '@/pages/MapView'
import { LeavePage } from '@/pages/Leave'
import { PayrollPage } from '@/pages/Payroll'
import { InvoicingPage } from '@/pages/Invoicing'
import { PostOrdersPage } from '@/pages/PostOrders'
import { KeyRegisterPage } from '@/pages/KeyRegister'
import { LostFoundPage } from '@/pages/LostFound'
import { DefectLogPage } from '@/pages/DefectLog'
import { EquipmentRegisterPage } from '@/pages/EquipmentRegister'
import { HandoversPage } from '@/pages/Handovers'
import { ManDownPage } from '@/pages/ManDown'
import GuardOps from '@/pages/GuardOps'
import VmsOnsite from '@/pages/VmsOnsite'
import Reports from '@/pages/Reports'
import Heatmap from '@/pages/Heatmap'
import ClientPortal from '@/pages/ClientPortal'
import ApiKeys from '@/pages/ApiKeys'
import IpAllowlist from '@/pages/IpAllowlist'
import ScheduledReports from '@/pages/ScheduledReports'
import AlertDedup from '@/pages/AlertDedup'
import Developer from '@/pages/Developer'
import CommandCentre from '@/pages/CommandCentre'
import ActionCenter from '@/pages/ActionCenter'
import AlertRules from '@/pages/AlertRules'
import DeviceProtocols from '@/pages/DeviceProtocols'
import IoTPage from '@/pages/IoT'
import GPSPage from '@/pages/GPS'
import ContractorsPage from '@/pages/Contractors'
import ParkingPage from '@/pages/Parking'
import BWCPage from '@/pages/BWC'
import CompliancePage from '@/pages/Compliance'
import AlarmsPage from '@/pages/Alarms'
import AccessControlPage from '@/pages/AccessControl'
import TrainingPage from '@/pages/Training'
import EmergencyBroadcastPage from '@/pages/EmergencyBroadcast'
import VisitorPreRegPage from '@/pages/VisitorPreReg'

// At module load, before anything renders: from here on a Date formats in the
// tenant's timezone rather than the machine's. Inert until branding arrives
// and setTenantTimeZone runs below, so nothing changes for a tenant that has
// no timezone configured.
installTenantTimeZoneDefault()

/** Applies the tenant's own settings — brand colour and timezone — once
 *  branding has loaded. Both are properties of the subscriber, not of the
 *  machine the app happens to be running on. */
function TenantSettingsSync() {
  const { setBrandColor } = useColorMode()
  const { data } = useQuery({ queryKey: ['branding'], queryFn: getBranding, staleTime: 5 * 60 * 1000 })
  useEffect(() => {
    const color = data?.branding?.primary_color
    if (color && /^#[0-9A-Fa-f]{6}$/.test(color)) {
      setBrandColor(color)
    }
  }, [data, setBrandColor])
  useEffect(() => {
    // Makes every Date in the app render in the site's clock rather than the
    // operator's machine — see lib/tenantTime.ts. Set here because this is
    // already where the tenant's settings land, and it must apply before any
    // page formats a timestamp.
    setTenantTimeZone(data?.timezone)
  }, [data])
  return null
}

function RequireAuth({ children }: { children: React.ReactNode }) {
  const accessToken = useAuthStore((s) => s.accessToken)
  const location = useLocation()
  // Carry the originally-requested path (e.g. a Live Wall pop-out window
  // opened straight to /live?layout=X) through the redirect, so Login can
  // send the user back to it instead of always landing on Dashboard —
  // otherwise every cold-boot deep link (any new window/tab, not just a
  // fresh browser session) loses its destination on the way through /login.
  return accessToken ? <>{children}</> : <Navigate to="/login" replace state={{ from: location }} />
}

// Client users (role 7) get a dedicated standalone portal instead of the
// internal operational shell (Gap 89). ClientPortal renders no <Outlet/>, so
// the nested operational routes below never mount for a client.
function ShellOrPortal() {
  const roleId = useAuthStore((s) => s.user?.roleId)
  return roleId === 7 ? <ClientPortal /> : <AppShell />
}

export default function App() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <TenantSettingsSync />
            <ShellOrPortal />
          </RequireAuth>
        }
      >
        <Route index element={<Dashboard />} />
        <Route path="command-centre" element={<CommandCentre />} />
        <Route path="action-center" element={<ActionCenter />} />
        <Route path="alert-rules" element={<AlertRules />} />
        <Route path="device-protocols" element={<DeviceProtocols />} />
        <Route path="iot" element={<IoTPage />} />
        <Route path="gps" element={<GPSPage />} />
        <Route path="contractors" element={<ContractorsPage />} />
        <Route path="parking" element={<ParkingPage />} />
        <Route path="bwc" element={<BWCPage />} />
        <Route path="compliance" element={<CompliancePage />} />
        <Route path="alarms" element={<AlarmsPage />} />
        <Route path="access" element={<AccessControlPage />} />
        <Route path="training" element={<TrainingPage />} />
        <Route path="emergency" element={<EmergencyBroadcastPage />} />
        <Route path="visitor-prereg" element={<VisitorPreRegPage />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="incidents" element={<Incidents />} />
        <Route path="cameras" element={<Cameras />} />
        <Route path="live" element={<LiveWallPage />} />
        <Route path="sites" element={<SitesPage />} />
        <Route path="recordings" element={<RecordingsPage />} />
        <Route path="playback" element={<PlaybackPage />} />
        <Route path="shifts" element={<ShiftsPage />} />
        <Route path="roster" element={<RosterPage />} />
        <Route path="duty-assignments" element={<DutyAssignmentsPage />} />
        <Route path="attendance" element={<AttendancePage />} />
        <Route path="violations" element={<ViolationsPage />} />
        <Route path="barriers" element={<BarriersPage />} />
        <Route path="map" element={<MapViewPage />} />
        <Route path="leave" element={<LeavePage />} />
        <Route path="payroll" element={<PayrollPage />} />
        <Route path="invoicing" element={<InvoicingPage />} />
        <Route path="post-orders" element={<PostOrdersPage />} />
        <Route path="key-register" element={<KeyRegisterPage />} />
        <Route path="lost-found" element={<LostFoundPage />} />
        <Route path="defects" element={<DefectLogPage />} />
        <Route path="equipment" element={<EquipmentRegisterPage />} />
        <Route path="handovers" element={<HandoversPage />} />
        <Route path="man-down" element={<ManDownPage />} />
        <Route path="detections" element={<Detections />} />
        <Route path="watchlists" element={<Watchlists />} />
        <Route path="zones" element={<Zones />} />
        <Route path="evidence" element={<Evidence />} />
        <Route path="audit" element={<Audit />} />
        <Route path="analytics" element={<Analytics />} />
        <Route path="export" element={<Export />} />
        <Route path="notifications" element={<Notifications />} />
        <Route path="settings" element={<Settings />} />
        <Route path="tenants" element={<Tenants />} />
        <Route path="support-sessions" element={<SupportSessions />} />
        <Route path="platform" element={<PlatformDashboard />} />
        <Route path="platform/tenants" element={<TenantUsage />} />
        <Route path="platform/tenants/:tenantId" element={<TenantUsage />} />
        <Route path="platform/errors" element={<ErrorCentre />} />
        <Route path="platform/billing" element={<PlatformBilling />} />
        <Route path="platform/invoices" element={<PlatformInvoices />} />
        <Route path="platform/analytics" element={<PlatformAnalytics />} />
        <Route path="users" element={<Users />} />
        <Route path="roles" element={<Roles />} />
        <Route path="guard-ops" element={<GuardOps />} />
        {/* Its own route rather than a Guard Ops tab: a gatehouse runs this
            on a dedicated screen all shift, so it has to be openable in its
            own window (and full screen) independently of Guard Ops. */}
        <Route path="vms-onsite" element={<VmsOnsite />} />
        <Route path="reports" element={<Reports />} />
        <Route path="heatmap" element={<Heatmap />} />
        <Route path="api-keys" element={<ApiKeys />} />
        <Route path="ip-allowlist" element={<IpAllowlist />} />
        <Route path="scheduled-reports" element={<ScheduledReports />} />
        <Route path="alert-dedup" element={<AlertDedup />} />
        <Route path="developer" element={<Developer />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}

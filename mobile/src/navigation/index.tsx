import React, { useEffect } from 'react'
import { Platform, StyleSheet } from 'react-native'
import { createNativeStackNavigator } from '@react-navigation/native-stack'
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs'
import { BlurView } from 'expo-blur'
import { Ionicons } from '@expo/vector-icons'
import { useBiometricStore } from '@/store/biometric'
import { useAppResumeBiometric } from '@/hooks/useAppResumeBiometric'
import { LoginScreen }                 from '@/screens/LoginScreen'
import { DashboardScreen }             from '@/screens/DashboardScreen'
import { AlertsScreen }                from '@/screens/AlertsScreen'
import { AlertDetailScreen }           from '@/screens/AlertDetailScreen'
import { IncidentsScreen }             from '@/screens/IncidentsScreen'
import { IncidentDetailScreen }        from '@/screens/IncidentDetailScreen'
import { CamerasScreen }               from '@/screens/CamerasScreen'
import { CameraLiveScreen }            from '@/screens/CameraLiveScreen'
import { LiveWallScreen }              from '@/screens/LiveWallScreen'
import { ZoneDrawScreen }              from '@/screens/ZoneDrawScreen'
import { SitesScreen }                 from '@/screens/SitesScreen'
import { WatchlistsScreen }            from '@/screens/WatchlistsScreen'
import { EvidenceScreen }              from '@/screens/EvidenceScreen'
import { SettingsScreen }              from '@/screens/SettingsScreen'
import { ShiftScreen }                 from '@/screens/ShiftScreen'
import { PatrolSelectScreen }          from '@/screens/PatrolSelectScreen'
import { PatrolScanScreen }            from '@/screens/PatrolScanScreen'
import { OccurrenceBookScreen }        from '@/screens/OccurrenceBookScreen'
import { MoreMenuScreen }              from '@/screens/MoreMenuScreen'
import { VisitorsScreen }              from '@/screens/VisitorsScreen'
import { DispatchScreen }              from '@/screens/DispatchScreen'
import { DetectionsScreen }            from '@/screens/DetectionsScreen'
import { NotificationsHistoryScreen }  from '@/screens/NotificationsHistoryScreen'
import { ReportsScreen }               from '@/screens/ReportsScreen'
import { AnalyticsScreen }             from '@/screens/AnalyticsScreen'
import { GPSScreen }                   from '@/screens/GPSScreen'
import { AlarmsScreen }                from '@/screens/AlarmsScreen'
import { BWCScreen }                   from '@/screens/BWCScreen'
import { AccessControlScreen }         from '@/screens/AccessControlScreen'
import { ContractorsScreen }           from '@/screens/ContractorsScreen'
import { ParkingScreen }               from '@/screens/ParkingScreen'
import { ComplianceScreen }            from '@/screens/ComplianceScreen'
import { IoTScreen }                   from '@/screens/IoTScreen'
import { EmergencyScreen }             from '@/screens/EmergencyScreen'
import { MyRecordScreen }              from '@/screens/MyRecordScreen'
import { TrainingScreen }              from '@/screens/TrainingScreen'
import { PostOrdersScreen }            from '@/screens/PostOrdersScreen'
import { KeyRegisterScreen }           from '@/screens/KeyRegisterScreen'
import { LostFoundScreen }             from '@/screens/LostFoundScreen'
import { DefectLogScreen }            from '@/screens/DefectLogScreen'
import { MyKitScreen }                from '@/screens/MyKitScreen'
import { HandoverScreen }             from '@/screens/HandoverScreen'
import { MyScheduleScreen }            from '@/screens/MyScheduleScreen'
import { ActionCenterScreen }          from '@/screens/ActionCenterScreen'
import { AttendanceScreen }            from '@/screens/AttendanceScreen'
import { useAuthStore }                from '@/store/auth'
import { colors }                      from '@/theme'

// ── Param list types ─────────────────────────────────────────────────────────

export type RootStackParamList = { Auth: undefined; Main: undefined }

export type AlertsStackParamList = {
  AlertsList:  undefined
  AlertDetail: { alertId: string }
}

export type IncidentsStackParamList = {
  IncidentsList:       undefined
  IncidentDetail:      { incidentId: string }
  IncidentCameraLive:  { cameraId: string; streamId: string; cameraName: string }
}

export type CamerasStackParamList = {
  CamerasList: { siteId?: string } | undefined
  Sites:       undefined
  CameraLive:  { cameraId: string; streamId: string; cameraName: string }
  LiveWall:    undefined
  ZoneDraw:    { cameraId: string; streamId: string; cameraName: string }
}

export type PatrolStackParamList = {
  Shifts:         undefined
  PatrolSelect:   { shiftId: string }
  PatrolScan:     { routeId: string; shiftId: string }
  OccurrenceBook: { shiftId?: string }
}

export type MoreStackParamList = {
  MoreMenu:      undefined
  Visitors:      undefined
  Dispatch:      undefined
  Detections:    undefined
  Notifications: undefined
  Reports:       undefined
  Analytics:     undefined
  Settings:      undefined
  GPS:           undefined
  Alarms:        undefined
  BWC:           undefined
  AccessControl: undefined
  Contractors:   undefined
  Parking:       undefined
  Compliance:    undefined
  IoT:           undefined
  Emergency:     undefined
  MyRecord:      undefined
  Training:      undefined
  PostOrders:    undefined
  KeyRegister:   undefined
  LostFound:     undefined
  DefectLog:     undefined
  MyKit:         undefined
  Handover:      undefined
  MySchedule:    undefined
  ActionCenter:  undefined
  Attendance:    undefined
}

export type MainTabParamList = {
  Dashboard:  undefined
  Alerts:     undefined
  Incidents:  undefined
  Cameras:    undefined
  Patrol:     undefined
  Watchlists: undefined
  Evidence:   undefined
  More:       undefined
}

// ── Navigators ────────────────────────────────────────────────────────────────

const Root           = createNativeStackNavigator<RootStackParamList>()
const AlertsStack    = createNativeStackNavigator<AlertsStackParamList>()
const IncidentsStack = createNativeStackNavigator<IncidentsStackParamList>()
const CamerasStack   = createNativeStackNavigator<CamerasStackParamList>()
const PatrolStack    = createNativeStackNavigator<PatrolStackParamList>()
const MoreStack      = createNativeStackNavigator<MoreStackParamList>()
const Tab            = createBottomTabNavigator<MainTabParamList>()

const screenOptions = {
  headerStyle:         { backgroundColor: '#020617' },
  headerShadowVisible: false,
  headerTintColor:     colors.primary,
  headerTitleStyle:    { fontWeight: '700' as const, color: colors.text, letterSpacing: -0.3 },
  contentStyle:        { backgroundColor: colors.background },
}

function AlertsNavigator() {
  return (
    <AlertsStack.Navigator screenOptions={screenOptions}>
      <AlertsStack.Screen name="AlertsList"  component={AlertsScreen}      options={{ title: 'Alerts' }} />
      <AlertsStack.Screen name="AlertDetail" component={AlertDetailScreen} options={{ title: 'Alert Detail' }} />
    </AlertsStack.Navigator>
  )
}

function IncidentsNavigator() {
  return (
    <IncidentsStack.Navigator screenOptions={screenOptions}>
      <IncidentsStack.Screen name="IncidentsList"      component={IncidentsScreen}      options={{ title: 'Incidents' }} />
      <IncidentsStack.Screen name="IncidentDetail"     component={IncidentDetailScreen} options={{ title: 'Incident Detail' }} />
      <IncidentsStack.Screen name="IncidentCameraLive" component={CameraLiveScreen}     options={{ title: 'Live View', headerBackTitle: 'Back' }} />
    </IncidentsStack.Navigator>
  )
}

function CamerasNavigator() {
  return (
    <CamerasStack.Navigator screenOptions={screenOptions}>
      <CamerasStack.Screen name="CamerasList" component={CamerasScreen}    options={{ title: 'Cameras' }} />
      <CamerasStack.Screen name="Sites"       component={SitesScreen}      options={{ title: 'Sites', headerBackTitle: 'Back' }} />
      <CamerasStack.Screen name="CameraLive"  component={CameraLiveScreen} options={{ title: 'Live View', headerBackTitle: 'Back' }} />
      <CamerasStack.Screen name="LiveWall"    component={LiveWallScreen}   options={{ title: 'Live Wall' }} />
      <CamerasStack.Screen name="ZoneDraw"    component={ZoneDrawScreen}   options={{ title: 'Draw Zone', headerBackTitle: 'Back' }} />
    </CamerasStack.Navigator>
  )
}

function PatrolNavigator() {
  return (
    <PatrolStack.Navigator screenOptions={screenOptions}>
      <PatrolStack.Screen name="Shifts"         component={ShiftScreen}          options={{ title: 'My Shifts' }} />
      <PatrolStack.Screen name="PatrolSelect"   component={PatrolSelectScreen}   options={{ title: 'Select Route' }} />
      <PatrolStack.Screen name="PatrolScan"     component={PatrolScanScreen}     options={{ title: 'Patrol', headerBackTitle: 'Back' }} />
      <PatrolStack.Screen name="OccurrenceBook" component={OccurrenceBookScreen} options={{ title: 'Occurrence Book', headerBackTitle: 'Back' }} />
    </PatrolStack.Navigator>
  )
}

function MoreNavigator() {
  return (
    <MoreStack.Navigator screenOptions={screenOptions}>
      <MoreStack.Screen name="MoreMenu"      component={MoreMenuScreen}             options={{ title: 'More' }} />
      <MoreStack.Screen name="ActionCenter"  component={ActionCenterScreen}         options={{ title: 'Action Center' }} />
      <MoreStack.Screen name="Attendance"    component={AttendanceScreen}           options={{ title: 'Live Attendance' }} />
      <MoreStack.Screen name="MySchedule"    component={MyScheduleScreen}           options={{ title: 'My Schedule' }} />
      <MoreStack.Screen name="Training"      component={TrainingScreen}             options={{ title: 'My Training' }} />
      <MoreStack.Screen name="PostOrders"    component={PostOrdersScreen}           options={{ title: 'SOP' }} />
      <MoreStack.Screen name="KeyRegister"   component={KeyRegisterScreen}          options={{ title: 'Key Register' }} />
      <MoreStack.Screen name="LostFound"     component={LostFoundScreen}            options={{ title: 'Lost & Found' }} />
      <MoreStack.Screen name="DefectLog"     component={DefectLogScreen}            options={{ title: 'Defect Log' }} />
      <MoreStack.Screen name="MyKit"         component={MyKitScreen}                options={{ title: 'My Kit' }} />
      <MoreStack.Screen name="Handover"      component={HandoverScreen}             options={{ title: 'Handovers' }} />
      <MoreStack.Screen name="Visitors"      component={VisitorsScreen}             options={{ title: 'Visitor Management' }} />
      <MoreStack.Screen name="Dispatch"      component={DispatchScreen}             options={{ title: 'Dispatch' }} />
      <MoreStack.Screen name="Detections"    component={DetectionsScreen}           options={{ title: 'AI Detections' }} />
      <MoreStack.Screen name="Notifications" component={NotificationsHistoryScreen} options={{ title: 'Notification Logs' }} />
      <MoreStack.Screen name="Reports"       component={ReportsScreen}              options={{ title: 'Reports' }} />
      <MoreStack.Screen name="Analytics"     component={AnalyticsScreen}            options={{ title: 'Analytics' }} />
      <MoreStack.Screen name="Settings"      component={SettingsScreen}             options={{ title: 'Settings' }} />
      <MoreStack.Screen name="GPS"           component={GPSScreen}                  options={{ title: 'GPS Tracking' }} />
      <MoreStack.Screen name="Alarms"        component={AlarmsScreen}               options={{ title: 'Alarm Panels' }} />
      <MoreStack.Screen name="BWC"           component={BWCScreen}                  options={{ title: 'Body Worn Cameras' }} />
      <MoreStack.Screen name="AccessControl" component={AccessControlScreen}        options={{ title: 'Access Control' }} />
      <MoreStack.Screen name="Contractors"   component={ContractorsScreen}          options={{ title: 'Contractors' }} />
      <MoreStack.Screen name="Parking"       component={ParkingScreen}              options={{ title: 'Parking' }} />
      <MoreStack.Screen name="Compliance"    component={ComplianceScreen}           options={{ title: 'Compliance' }} />
      <MoreStack.Screen name="IoT"           component={IoTScreen}                  options={{ title: 'IoT Devices' }} />
      <MoreStack.Screen name="Emergency"     component={EmergencyScreen}            options={{ title: 'Emergency Broadcast' }} />
      <MoreStack.Screen name="MyRecord"      component={MyRecordScreen}             options={{ title: 'My Record' }} />
    </MoreStack.Navigator>
  )
}

const TAB_ICONS: Record<string, { active: string; inactive: string }> = {
  Dashboard:  { active: 'home',         inactive: 'home-outline' },
  Alerts:     { active: 'alert-circle', inactive: 'alert-circle-outline' },
  Incidents:  { active: 'warning',      inactive: 'warning-outline' },
  Cameras:    { active: 'videocam',     inactive: 'videocam-outline' },
  Patrol:     { active: 'footsteps',    inactive: 'footsteps-outline' },
  Watchlists: { active: 'list',         inactive: 'list-outline' },
  Evidence:   { active: 'images',       inactive: 'images-outline' },
  More:       { active: 'grid',         inactive: 'grid-outline' },
}

function MainTabs() {
  useAppResumeBiometric()
  return (
    <Tab.Navigator
      screenOptions={({ route }) => ({
        tabBarIcon: ({ focused, color, size }) => {
          const icons = TAB_ICONS[route.name] ?? { active: 'ellipse', inactive: 'ellipse-outline' }
          const name = (focused ? icons.active : icons.inactive) as React.ComponentProps<typeof Ionicons>['name']
          return <Ionicons name={name} size={size} color={color} />
        },
        tabBarActiveTintColor:   colors.primary,
        tabBarInactiveTintColor: 'rgba(248,250,252,0.40)',
        tabBarStyle: {
          backgroundColor: Platform.OS === 'ios' ? 'transparent' : 'rgba(2,6,23,0.96)',
          borderTopColor:  'rgba(255,255,255,0.10)',
          borderTopWidth:  1,
          elevation:       0,
        },
        tabBarBackground: Platform.OS === 'ios'
          ? () => <BlurView intensity={25} tint="dark" style={StyleSheet.absoluteFill} />
          : undefined,
        headerShown: false,
      })}
    >
      <Tab.Screen
        name="Dashboard"
        component={DashboardScreen}
        options={{ headerShown: true, ...screenOptions, title: '7th AI Vision' }}
      />
      <Tab.Screen name="Alerts"     component={AlertsNavigator} />
      <Tab.Screen name="Incidents"  component={IncidentsNavigator} />
      <Tab.Screen name="Cameras"    component={CamerasNavigator} />
      <Tab.Screen name="Patrol"     component={PatrolNavigator} />
      <Tab.Screen name="Watchlists" component={WatchlistsScreen}
        options={{ headerShown: true, ...screenOptions, title: 'Watchlists' }} />
      <Tab.Screen name="Evidence"   component={EvidenceScreen}
        options={{ headerShown: true, ...screenOptions, title: 'Evidence' }} />
      <Tab.Screen name="More"       component={MoreNavigator} />
    </Tab.Navigator>
  )
}

export function RootNavigator() {
  const accessToken = useAuthStore((s) => s.accessToken)
  const initBiometric = useBiometricStore((s) => s.init)

  useEffect(() => {
    initBiometric()
  }, [])

  return (
    <Root.Navigator screenOptions={{ headerShown: false }}>
      {accessToken ? (
        <Root.Screen name="Main" component={MainTabs} />
      ) : (
        <Root.Screen name="Auth" component={LoginScreen} />
      )}
    </Root.Navigator>
  )
}

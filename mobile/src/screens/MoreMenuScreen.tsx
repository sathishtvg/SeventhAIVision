import React from 'react'
import {
  Pressable, ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useNavigation } from '@react-navigation/native'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import { Ionicons } from '@expo/vector-icons'
import { useAuthStore } from '@/store/auth'
import { Card } from '@/components/Card'
import { canSee, type Gated } from '@/lib/access'
import { colors, fontSize, radius, spacing } from '@/theme'
import type { MoreStackParamList } from '@/navigation'

type NavProp = NativeStackNavigationProp<MoreStackParamList>

interface MenuItem extends Gated {
  screen: keyof MoreStackParamList
  label: string
  description: string
  icon: React.ComponentProps<typeof Ionicons>['name']
  color: string
}

/*
 * Each row carries the permission its own endpoint demands, and 'ops' where the
 * screen is oversight or configuration rather than field work. See
 * src/lib/access.ts for why both are needed — the question that started this
 * was "why does a guard's phone offer Live Attendance", and the answer is that
 * a guard does hold attendance:read, so only the audience mark can hide it.
 *
 * Rows with neither mark are every user's own work and stay for everyone.
 */

const MENU_ITEMS: MenuItem[] = [
  { screen: 'MyRecord',      label: 'My Record',          description: 'Your violations and leave requests', icon: 'document-text-outline',    color: colors.warning, permission: 'violation:read' },
  { screen: 'ActionCenter',  label: 'Action Center',      description: 'What needs your attention now',      icon: 'checkmark-done-outline',   color: colors.error },
  { screen: 'Attendance',    label: 'Live Attendance',    description: 'Who is on duty and who is missing',  icon: 'people-circle-outline',    color: colors.success, permission: 'attendance:read', audience: 'ops' },
  { screen: 'MySchedule',    label: 'My Schedule',        description: 'Your upcoming and recent shifts',    icon: 'calendar-outline',         color: colors.secondary, permission: 'shift:read' },
  { screen: 'PostOrders',    label: 'SOP',                description: 'Standard operating procedures for your sites', icon: 'reader-outline',       color: colors.error },
  { screen: 'KeyRegister',   label: 'Key Register',       description: 'Issue and receive keys, and see what is out', icon: 'key-outline',              color: colors.warning, permission: 'keyreg:read' },
  { screen: 'LostFound',     label: 'Lost & Found',       description: 'Book in found property and release it to its owner', icon: 'briefcase-outline',        color: colors.info, permission: 'lostfound:read' },
  { screen: 'DefectLog',     label: 'Defect Log',         description: 'Report faults you find on site, with a photo', icon: 'construct-outline',        color: colors.warning, permission: 'defect:read' },
  { screen: 'MyKit',         label: 'My Kit',             description: 'Equipment and uniform signed out to you', icon: 'shirt-outline',            color: colors.secondary, permission: 'equipment:read' },
  { screen: 'Handover',      label: 'Handovers',          description: 'Accept or dispute what the last shift handed over', icon: 'swap-horizontal-outline',  color: colors.primary, permission: 'handover:read' },
  { screen: 'Training',      label: 'My Training',        description: 'SOP courses and quizzes',            icon: 'school-outline',           color: colors.primary, permission: 'training:read' },
  { screen: 'Visitors',      label: 'Visitor Management', description: 'Check in/out and manage visitors',   icon: 'people-outline',           color: colors.info, permission: 'visitor:read' },
  { screen: 'Detections',    label: 'AI Detections',      description: 'LPR, face, and intrusion events',    icon: 'scan-outline',             color: colors.primary, permission: 'detection:read' },
  { screen: 'Analytics',     label: 'Analytics',          description: 'Metrics, trends, and top cameras',   icon: 'bar-chart-outline',        color: colors.secondary, audience: 'ops' },
  { screen: 'Reports',       label: 'Reports',            description: 'Generate and download PDF reports',  icon: 'document-text-outline',    color: colors.warning, audience: 'ops' },
  { screen: 'Notifications', label: 'Notification Logs',  description: 'Email, SMS, and webhook history',    icon: 'notifications-outline',    color: colors.success, permission: 'notification:manage', audience: 'ops' },
  { screen: 'GPS',           label: 'GPS Tracking',       description: 'Live vehicle and fleet positions',   icon: 'navigate-outline',         color: colors.success, permission: 'gps:read', audience: 'ops' },
  { screen: 'Alarms',        label: 'Alarm Panels',       description: 'Arm, disarm, and view alarm events', icon: 'shield-outline',           color: colors.warning, permission: 'alarm:read', audience: 'ops' },
  { screen: 'BWC',           label: 'Body Worn Cameras',  description: 'Camera status and recordings',       icon: 'videocam-outline',         color: colors.info, permission: 'bwc:read', audience: 'ops' },
  { screen: 'AccessControl', label: 'Access Control',     description: 'Doors, credentials, and events',     icon: 'key-outline',              color: colors.primary, permission: 'access:read', audience: 'ops' },
  { screen: 'Contractors',   label: 'Contractors',        description: 'Work permits and deliveries',        icon: 'construct-outline',        color: colors.warning, permission: 'contractor:read', audience: 'ops' },
  { screen: 'Parking',       label: 'Parking',            description: 'Occupancy, sessions, and bays',      icon: 'car-outline',              color: colors.secondary, permission: 'parking:read', audience: 'ops' },
  { screen: 'Compliance',    label: 'Compliance',         description: 'Policies, checks, and audit trail',  icon: 'clipboard-outline',        color: colors.success, permission: 'compliance:read', audience: 'ops' },
  { screen: 'IoT',           label: 'IoT Devices',        description: 'Sensors, readings, and alerts',      icon: 'hardware-chip-outline',    color: colors.info, permission: 'iot:read', audience: 'ops' },
  { screen: 'Emergency',     label: 'Emergency Broadcast', description: 'Send mass alerts to all staff',      icon: 'megaphone-outline',        color: colors.error, permission: 'broadcast:read' },
  { screen: 'Settings',      label: 'Settings',           description: 'AI thresholds and 2FA security',     icon: 'settings-outline',         color: colors.textSecondary, permission: 'settings:read', audience: 'ops' },
]

export function MoreMenuScreen() {
  const nav = useNavigation<NavProp>()
  const logout = useAuthStore((s) => s.logout)
  const user = useAuthStore((s) => s.user)
  const permissions = useAuthStore((s) => s.permissions)
  const items = MENU_ITEMS.filter((i) => canSee(i, permissions, user?.roleId))

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      {/* User badge */}
      {user && (
        <Card style={styles.userCard}>
          <View style={styles.userRow}>
            <View style={styles.avatar}>
              <Ionicons name="person" size={20} color={colors.primary} />
            </View>
            <View style={styles.userInfo}>
              <Text style={styles.userName}>{user.email}</Text>
              <Text style={styles.userRole}>Role ID {user.roleId}</Text>
            </View>
          </View>
        </Card>
      )}

      {/* Navigation items */}
      {items.map((item) => (
        <Pressable key={item.screen} onPress={() => nav.navigate(item.screen as any)}>
          <Card style={styles.menuCard}>
            <View style={[styles.menuIconWrap, { backgroundColor: item.color + '20' }]}>
              <Ionicons name={item.icon} size={22} color={item.color} />
            </View>
            <View style={styles.menuInfo}>
              <Text style={styles.menuLabel}>{item.label}</Text>
              <Text style={styles.menuDesc}>{item.description}</Text>
            </View>
            <Ionicons name="chevron-forward" size={18} color={colors.textDisabled} />
          </Card>
        </Pressable>
      ))}

      {/* Logout */}
      <Pressable style={styles.logoutBtn} onPress={logout}>
        <Ionicons name="log-out-outline" size={18} color={colors.error} />
        <Text style={styles.logoutText}>Sign Out</Text>
      </Pressable>
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root:         { flex: 1, backgroundColor: colors.background },
  content:      { padding: spacing.md, paddingBottom: spacing.xxl },
  userCard:     { marginBottom: spacing.lg },
  userRow:      { flexDirection: 'row', alignItems: 'center', gap: spacing.md },
  avatar:       { width: 44, height: 44, borderRadius: 22, backgroundColor: colors.primaryMuted, alignItems: 'center', justifyContent: 'center' },
  userInfo:     { flex: 1 },
  userName:     { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  userRole:     { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2 },
  menuCard:     { flexDirection: 'row', alignItems: 'center', gap: spacing.md, marginBottom: spacing.sm },
  menuIconWrap: { width: 44, height: 44, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center', flexShrink: 0 },
  menuInfo:     { flex: 1 },
  menuLabel:    { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  menuDesc:     { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2 },
  logoutBtn:    { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm, marginTop: spacing.lg, paddingVertical: spacing.md, borderRadius: radius.md, borderWidth: 1, borderColor: colors.error + '60' },
  logoutText:   { fontSize: fontSize.md, fontWeight: '700', color: colors.error },
})

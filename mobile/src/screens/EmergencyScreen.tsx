import React, { useState, useCallback } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Pressable, RefreshControl,
  ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import {
  getMyBroadcasts, listBroadcasts, sendBroadcast, acknowledgeBroadcast,
  type MyBroadcast, type EmergencyBroadcast,
} from '@/api/emergency'
import { Card } from '@/components/Card'
import { useAuthStore } from '@/store/auth'
import { colors, fontSize, radius, spacing } from '@/theme'

const SEV_COLOR: Record<string, string> = {
  info:     colors.info,
  warning:  colors.warning,
  critical: colors.error,
  drill:    '#6C63FF',
}

const SEVERITIES = ['info', 'warning', 'critical', 'drill'] as const

function SevPill({ severity }: { severity: string }) {
  const color = SEV_COLOR[severity] ?? colors.textDisabled
  return (
    <View style={[styles.pill, { backgroundColor: color + '28', borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{severity.toUpperCase()}</Text>
    </View>
  )
}

function MyAlertRow({ item, onAck }: { item: MyBroadcast; onAck: (id: string) => void }) {
  const unread = !item.acknowledged_at
  return (
    <Card style={[styles.row, unread && styles.rowUnread]}>
      <View style={styles.rowTop}>
        <Ionicons
          name="megaphone-outline"
          size={18}
          color={unread ? colors.error : colors.textSecondary}
          style={styles.icon}
        />
        <View style={styles.rowInfo}>
          <Text style={[styles.name, unread && { color: colors.error }]}>{item.title}</Text>
          {item.sender_name && <Text style={styles.sub}>From: {item.sender_name}</Text>}
        </View>
        <SevPill severity={item.severity} />
      </View>
      <Text style={styles.message} numberOfLines={3}>{item.message}</Text>
      <View style={styles.footer}>
        <Text style={styles.time}>{new Date(item.sent_at).toLocaleString()}</Text>
        {unread ? (
          <Pressable style={styles.ackBtn} onPress={() => onAck(item.id)}>
            <Ionicons name="checkmark-circle-outline" size={14} color={colors.success} />
            <Text style={styles.ackText}>Acknowledge</Text>
          </Pressable>
        ) : (
          <View style={styles.ackedBadge}>
            <Ionicons name="checkmark-circle" size={14} color={colors.success} />
            <Text style={styles.ackedText}>Acknowledged</Text>
          </View>
        )}
      </View>
    </Card>
  )
}

function BroadcastRow({ item }: { item: EmergencyBroadcast }) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.title}</Text>
          {item.sender_name && <Text style={styles.sub}>By: {item.sender_name}</Text>}
        </View>
        <SevPill severity={item.severity} />
      </View>
      <Text style={styles.message} numberOfLines={2}>{item.message}</Text>
      <View style={styles.footer}>
        <Text style={styles.time}>{new Date(item.sent_at).toLocaleString()}</Text>
        <Text style={styles.sub}>
          {item.acknowledged_count}/{item.recipient_count} ack'd
        </Text>
      </View>
    </Card>
  )
}

function SendForm() {
  const qc = useQueryClient()
  const [title, setTitle]       = useState('')
  const [message, setMessage]   = useState('')
  const [severity, setSeverity] = useState<string>('warning')

  const sendMut = useMutation({
    mutationFn: () => sendBroadcast({ title, message, severity, broadcast_type: 'all' }),
    onSuccess: (data) => {
      Alert.alert('Sent', `Broadcast delivered to ${data.recipient_count} recipients.`)
      setTitle('')
      setMessage('')
      setSeverity('warning')
      qc.invalidateQueries({ queryKey: ['broadcasts-all'] })
    },
    onError: () => Alert.alert('Error', 'Failed to send broadcast.'),
  })

  const canSend = title.trim().length > 0 && message.trim().length > 0 && !sendMut.isPending

  return (
    <ScrollView contentContainerStyle={styles.sendForm}>
      <Text style={styles.sectionLabel}>Severity</Text>
      <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.sevRow}>
        {SEVERITIES.map((s) => {
          const color = SEV_COLOR[s]
          const active = severity === s
          return (
            <Pressable
              key={s}
              onPress={() => setSeverity(s)}
              style={[styles.sevChip, active && { backgroundColor: color + '40', borderColor: color }]}
            >
              <Text style={[styles.sevChipText, active && { color }]}>{s.toUpperCase()}</Text>
            </Pressable>
          )
        })}
      </ScrollView>

      <Text style={styles.sectionLabel}>Title</Text>
      <TextInput
        style={styles.input}
        placeholder="Broadcast title…"
        placeholderTextColor={colors.textDisabled}
        value={title}
        onChangeText={setTitle}
        maxLength={200}
      />

      <Text style={styles.sectionLabel}>Message</Text>
      <TextInput
        style={[styles.input, styles.inputMulti]}
        placeholder="Enter the emergency message…"
        placeholderTextColor={colors.textDisabled}
        value={message}
        onChangeText={setMessage}
        multiline
        numberOfLines={5}
        textAlignVertical="top"
        maxLength={2000}
      />

      {severity === 'critical' && (
        <View style={styles.warnBanner}>
          <Ionicons name="warning-outline" size={16} color={colors.error} />
          <Text style={styles.warnText}>
            CRITICAL broadcasts trigger immediate push notifications and email alerts.
          </Text>
        </View>
      )}

      <Pressable
        style={[styles.sendBtn, !canSend && styles.sendBtnDisabled]}
        onPress={() => sendMut.mutate()}
        disabled={!canSend}
      >
        {sendMut.isPending ? (
          <ActivityIndicator color="#fff" size="small" />
        ) : (
          <>
            <Ionicons name="megaphone-outline" size={18} color="#fff" />
            <Text style={styles.sendBtnText}>Send Broadcast</Text>
          </>
        )}
      </Pressable>
    </ScrollView>
  )
}

export function EmergencyScreen() {
  const qc = useQueryClient()
  const user = useAuthStore((s) => s.user)
  const [tab, setTab] = useState<'alerts' | 'all' | 'send'>('alerts')
  const [refreshing, setRefreshing] = useState(false)

  // Super Admin/Admin/Supervisor plus Manager(8) — a raw `roleId <= 3` range
  // check would wrongly exclude Manager despite being senior to Operator/Guard.
  const canSend = user && [1, 2, 3, 8].includes(user.roleId)

  const { data: myAlerts = [], isLoading: loadingMy } = useQuery({
    queryKey: ['broadcasts-my'],
    queryFn: () => getMyBroadcasts(),
    enabled: tab === 'alerts',
  })

  const { data: allBroadcasts = [], isLoading: loadingAll } = useQuery({
    queryKey: ['broadcasts-all'],
    queryFn: () => listBroadcasts({ limit: 50 }),
    enabled: tab === 'all',
  })

  const ackMutation = useMutation({
    mutationFn: acknowledgeBroadcast,
    onSuccess: () => qc.invalidateQueries({ queryKey: ['broadcasts-my'] }),
    onError: () => Alert.alert('Error', 'Failed to acknowledge.'),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['broadcasts'] })
    setRefreshing(false)
  }, [qc])

  const isLoading = tab === 'alerts' ? loadingMy : tab === 'all' ? loadingAll : false

  const unreadCount = myAlerts.filter((b: MyBroadcast) => !b.acknowledged_at).length

  const TABS = [
    { key: 'alerts' as const, label: 'My Alerts', badge: unreadCount > 0 ? unreadCount : undefined },
    { key: 'all'    as const, label: 'All Sent' },
    ...(canSend ? [{ key: 'send' as const, label: 'Send' }] : []),
  ]

  return (
    <View style={styles.root}>
      <View style={styles.tabRow}>
        {TABS.map((t) => (
          <Pressable
            key={t.key}
            style={[styles.tabBtn, tab === t.key && styles.tabActive]}
            onPress={() => setTab(t.key)}
          >
            <Text style={[styles.tabText, tab === t.key && styles.tabTextActive]}>
              {t.label}
              {t.badge != null ? ` (${t.badge})` : ''}
            </Text>
          </Pressable>
        ))}
      </View>

      {tab === 'send' ? (
        <SendForm />
      ) : isLoading ? (
        <View style={styles.center}><ActivityIndicator color={colors.error} /></View>
      ) : tab === 'alerts' ? (
        myAlerts.length === 0 ? (
          <View style={styles.center}>
            <Ionicons name="megaphone-outline" size={48} color={colors.textDisabled} />
            <Text style={styles.emptyText}>No broadcasts received</Text>
          </View>
        ) : (
          <FlatList
            data={myAlerts}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => (
              <MyAlertRow item={item} onAck={(id) => ackMutation.mutate(id)} />
            )}
            ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
            contentContainerStyle={styles.list}
            refreshControl={
              <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.error} />
            }
          />
        )
      ) : (
        allBroadcasts.length === 0 ? (
          <View style={styles.center}>
            <Ionicons name="megaphone-outline" size={48} color={colors.textDisabled} />
            <Text style={styles.emptyText}>No broadcasts sent yet</Text>
          </View>
        ) : (
          <FlatList
            data={allBroadcasts}
            keyExtractor={(item) => item.id}
            renderItem={({ item }) => <BroadcastRow item={item} />}
            ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
            contentContainerStyle={styles.list}
            refreshControl={
              <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.error} />
            }
          />
        )
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root:            { flex: 1, backgroundColor: colors.background },
  center:          { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  emptyText:       { color: colors.textSecondary, fontSize: fontSize.md },
  tabRow:          { flexDirection: 'row', padding: spacing.md, gap: spacing.sm },
  tabBtn:          { flex: 1, paddingVertical: 8, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder, alignItems: 'center' },
  tabActive:       { backgroundColor: colors.error, borderColor: colors.error },
  tabText:         { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  tabTextActive:   { color: '#fff' },
  list:            { padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xl },
  row:             { gap: 6 },
  rowUnread:       { borderColor: colors.error + '60' },
  rowTop:          { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  icon:            { flexShrink: 0 },
  rowInfo:         { flex: 1 },
  name:            { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sub:             { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  message:         { fontSize: fontSize.sm, color: colors.textSecondary, lineHeight: 20 },
  footer:          { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  time:            { fontSize: fontSize.xs, color: colors.textDisabled },
  ackBtn:          { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: spacing.sm, paddingVertical: 4, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.success },
  ackText:         { fontSize: fontSize.xs, color: colors.success, fontWeight: '600' },
  ackedBadge:      { flexDirection: 'row', alignItems: 'center', gap: 4 },
  ackedText:       { fontSize: fontSize.xs, color: colors.success },
  pill:            { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:        { fontSize: 10, fontWeight: '700', letterSpacing: 0.5 },
  sendForm:        { padding: spacing.md, gap: spacing.sm, paddingBottom: spacing.xl },
  sectionLabel:    { fontSize: fontSize.sm, fontWeight: '700', color: colors.textSecondary, textTransform: 'uppercase', letterSpacing: 0.5 },
  sevRow:          { flexDirection: 'row', gap: spacing.xs, paddingVertical: spacing.xs },
  sevChip:         { paddingHorizontal: spacing.md, paddingVertical: 6, borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder },
  sevChipText:     { fontSize: fontSize.sm, color: colors.textSecondary, fontWeight: '600' },
  input:           { backgroundColor: colors.card, borderWidth: 1, borderColor: colors.cardBorder, borderRadius: radius.sm, paddingHorizontal: spacing.md, paddingVertical: 10, color: colors.text, fontSize: fontSize.md },
  inputMulti:      { height: 120, paddingTop: 10 },
  warnBanner:      { flexDirection: 'row', alignItems: 'flex-start', gap: 8, backgroundColor: colors.error + '15', borderRadius: radius.sm, padding: spacing.sm, borderWidth: 1, borderColor: colors.error + '40' },
  warnText:        { flex: 1, fontSize: fontSize.xs, color: colors.error, lineHeight: 18 },
  sendBtn:         { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm, backgroundColor: colors.error, borderRadius: radius.sm, paddingVertical: 14, marginTop: spacing.sm },
  sendBtnDisabled: { opacity: 0.4 },
  sendBtnText:     { fontSize: fontSize.md, fontWeight: '700', color: '#fff' },
})

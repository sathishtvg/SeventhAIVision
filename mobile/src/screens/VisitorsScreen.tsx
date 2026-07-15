import React, { useState, useCallback, useRef } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Modal, Pressable,
  RefreshControl, ScrollView, StyleSheet, Text, TextInput, View,
} from 'react-native'
import { CameraView, useCameraPermissions } from 'expo-camera'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import {
  getVisitors, getUpcomingVisitors, createVisitor,
  checkinVisitor, checkoutVisitor, qrScanCheckin, sendVisitorQR,
  type Visitor, type VisitorCreate,
} from '@/api/visitors'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

// Status values from the DB: pending | arrived | departed | cancelled | expired
const STATUS_COLOR: Record<string, string> = {
  pending:   colors.info,
  arrived:   colors.success,
  departed:  colors.textDisabled,
  cancelled: colors.error,
  expired:   colors.warning,
}

const STATUS_LABEL: Record<string, string> = {
  pending:   'Pre-Registered',
  arrived:   'Inside',
  departed:  'Departed',
  cancelled: 'Cancelled',
  expired:   'Expired',
}

function StatusPill({ status }: { status: string }) {
  const color = STATUS_COLOR[status] ?? colors.textDisabled
  return (
    <View style={[styles.pill, { backgroundColor: color + '28', borderColor: color }]}>
      <Text style={[styles.pillText, { color }]}>{STATUS_LABEL[status] ?? status}</Text>
    </View>
  )
}

function VisitorRow({
  item,
  onCheckin,
  onCheckout,
  onSendQR,
}: {
  item: Visitor
  onCheckin: (v: Visitor) => void
  onCheckout: (v: Visitor) => void
  onSendQR: (v: Visitor) => void
}) {
  return (
    <Card style={styles.row}>
      <View style={styles.rowTop}>
        <View style={styles.avatar}>
          <Ionicons name="person" size={18} color={colors.primary} />
        </View>
        <View style={styles.rowInfo}>
          <Text style={styles.name}>{item.full_name}</Text>
          {item.company && <Text style={styles.sub}>{item.company}</Text>}
          {item.purpose && <Text style={styles.sub}>{item.purpose}</Text>}
        </View>
        <StatusPill status={item.status} />
      </View>
      {item.host_name && (
        <View style={styles.metaRow}>
          <Ionicons name="person-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>Host: {item.host_name}</Text>
        </View>
      )}
      {item.vehicle_plate && (
        <View style={styles.metaRow}>
          <Ionicons name="car-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>{item.vehicle_plate}</Text>
        </View>
      )}
      {item.expected_from && (
        <View style={styles.metaRow}>
          <Ionicons name="time-outline" size={12} color={colors.textSecondary} />
          <Text style={styles.metaText}>
            {new Date(item.expected_from).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}
            {item.expected_until
              ? ` – ${new Date(item.expected_until).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}`
              : ''}
          </Text>
        </View>
      )}
      <View style={styles.actionRow}>
        {item.status === 'pending' && (
          <Pressable style={[styles.actionBtn, { borderColor: colors.success }]} onPress={() => onCheckin(item)}>
            <Ionicons name="enter-outline" size={14} color={colors.success} />
            <Text style={[styles.actionText, { color: colors.success }]}>Check In</Text>
          </Pressable>
        )}
        {item.status === 'arrived' && (
          <Pressable style={[styles.actionBtn, { borderColor: colors.warning }]} onPress={() => onCheckout(item)}>
            <Ionicons name="exit-outline" size={14} color={colors.warning} />
            <Text style={[styles.actionText, { color: colors.warning }]}>Check Out</Text>
          </Pressable>
        )}
        {item.visitor_email && item.status === 'pending' && (
          <Pressable style={[styles.actionBtn, { borderColor: colors.info }]} onPress={() => onSendQR(item)}>
            <Ionicons name="qr-code-outline" size={14} color={colors.info} />
            <Text style={[styles.actionText, { color: colors.info }]}>Send QR</Text>
          </Pressable>
        )}
      </View>
    </Card>
  )
}

// ── List Tab ─────────────────────────────────────────────────────────────────

const LIST_FILTERS = [
  { label: 'Inside', value: 'arrived' },
  { label: 'Pre-reg', value: 'pending' },
  { label: 'Departed', value: 'departed' },
  { label: 'All', value: undefined as string | undefined },
]

function ListTab() {
  const qc = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<string | undefined>('arrived')
  const [refreshing, setRefreshing] = useState(false)
  const [showAdd, setShowAdd] = useState(false)
  const [confirmTarget, setConfirmTarget] = useState<{ visitor: Visitor; action: 'in' | 'out' } | null>(null)

  const emptyForm: VisitorCreate = {
    full_name: '', id_number: '', company: '', host_name: '',
    purpose: '', vehicle_plate: '', visitor_email: '',
    expected_from: '', expected_until: '',
  }
  const [form, setForm] = useState<VisitorCreate>(emptyForm)

  const { data: visitorsPage, isLoading } = useQuery({
    queryKey: ['visitors', statusFilter],
    queryFn: () => getVisitors({ status: statusFilter, limit: 100, offset: 0 }),
  })
  const visitors = visitorsPage?.items ?? []
  const visitorsTotal = visitorsPage?.total ?? 0

  const addMutation = useMutation({
    mutationFn: createVisitor,
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['visitors'] })
      setShowAdd(false)
      setForm(emptyForm)
    },
    onError: () => Alert.alert('Error', 'Failed to register visitor.'),
  })

  const checkinMut = useMutation({
    mutationFn: (v: Visitor) => checkinVisitor(v.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['visitors'] }); setConfirmTarget(null) },
    onError: () => Alert.alert('Error', 'Failed to check in visitor.'),
  })

  const checkoutMut = useMutation({
    mutationFn: (v: Visitor) => checkoutVisitor(v.id),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['visitors'] }); setConfirmTarget(null) },
    onError: () => Alert.alert('Error', 'Failed to check out visitor.'),
  })

  const sendQRMut = useMutation({
    mutationFn: (v: Visitor) => sendVisitorQR(v.id),
    onSuccess: (data) => Alert.alert('Sent', `QR code sent to ${data.email}`),
    onError: () => Alert.alert('Error', 'Failed to send QR email.'),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['visitors'] })
    setRefreshing(false)
  }, [qc])

  const isPending = checkinMut.isPending || checkoutMut.isPending

  return (
    <View style={styles.flex}>
      <View style={styles.chipRow}>
        <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipScroll}>
          {LIST_FILTERS.map((f) => (
            <Pressable
              key={String(f.value)}
              style={[styles.chip, statusFilter === f.value && styles.chipActive]}
              onPress={() => setStatusFilter(f.value)}
            >
              <Text style={[styles.chipText, statusFilter === f.value && styles.chipTextActive]}>{f.label}</Text>
            </Pressable>
          ))}
        </ScrollView>
        <Pressable style={styles.addBtn} onPress={() => setShowAdd(true)}>
          <Ionicons name="person-add-outline" size={18} color={colors.primary} />
        </Pressable>
      </View>

      {!isLoading && visitorsTotal > 0 && (
        <Text style={styles.totalCount}>{visitorsTotal} visitor{visitorsTotal !== 1 ? 's' : ''}</Text>
      )}

      {isLoading ? (
        <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
      ) : visitors.length === 0 ? (
        <View style={styles.center}>
          <Ionicons name="people-outline" size={48} color={colors.textDisabled} />
          <Text style={styles.emptyText}>No visitors</Text>
        </View>
      ) : (
        <FlatList
          data={visitors}
          keyExtractor={(item) => item.id}
          renderItem={({ item }) => (
            <VisitorRow
              item={item}
              onCheckin={(v) => setConfirmTarget({ visitor: v, action: 'in' })}
              onCheckout={(v) => setConfirmTarget({ visitor: v, action: 'out' })}
              onSendQR={(v) => sendQRMut.mutate(v)}
            />
          )}
          ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
        />
      )}

      {/* Check-in/out confirmation */}
      <Modal visible={confirmTarget !== null} transparent animationType="fade">
        <Pressable style={styles.overlay} onPress={() => setConfirmTarget(null)}>
          <View style={styles.modal}>
            <Text style={styles.modalTitle}>
              {confirmTarget?.action === 'out' ? 'Check Out' : 'Check In'}
            </Text>
            <Text style={styles.modalBody}>{confirmTarget?.visitor.full_name}</Text>
            <View style={styles.modalBtns}>
              <Pressable style={styles.cancelBtn} onPress={() => setConfirmTarget(null)}>
                <Text style={styles.cancelBtnText}>Cancel</Text>
              </Pressable>
              <Pressable
                style={[styles.confirmBtn, isPending && { opacity: 0.5 }]}
                disabled={isPending}
                onPress={() => {
                  if (!confirmTarget) return
                  if (confirmTarget.action === 'in') checkinMut.mutate(confirmTarget.visitor)
                  else checkoutMut.mutate(confirmTarget.visitor)
                }}
              >
                {isPending
                  ? <ActivityIndicator size="small" color="#fff" />
                  : <Text style={styles.confirmBtnText}>Confirm</Text>}
              </Pressable>
            </View>
          </View>
        </Pressable>
      </Modal>

      {/* Add visitor modal */}
      <Modal visible={showAdd} transparent animationType="slide">
        <View style={styles.overlay}>
          <View style={[styles.modal, styles.formModal]}>
            <View style={styles.modalHeader}>
              <Text style={styles.modalTitle}>Pre-Register Visitor</Text>
              <Pressable onPress={() => setShowAdd(false)}>
                <Ionicons name="close" size={20} color={colors.textSecondary} />
              </Pressable>
            </View>
            <ScrollView>
              {([
                { key: 'full_name' as const,     label: 'Full Name *',       placeholder: 'e.g. John Smith' },
                { key: 'id_number' as const,     label: 'ID / Passport',     placeholder: 'Optional' },
                { key: 'company' as const,       label: 'Company',           placeholder: 'Optional' },
                { key: 'host_name' as const,     label: 'Host Name',         placeholder: 'Who they are visiting' },
                { key: 'purpose' as const,       label: 'Purpose',           placeholder: 'e.g. Meeting, Delivery' },
                { key: 'vehicle_plate' as const, label: 'Vehicle Plate',     placeholder: 'Optional' },
                { key: 'visitor_email' as const, label: 'Email (for QR)',    placeholder: 'visitor@email.com' },
                { key: 'expected_from' as const, label: 'Expected Arrival',  placeholder: 'e.g. 2026-07-01T09:00:00' },
                { key: 'expected_until' as const,label: 'Expected Departure',placeholder: 'e.g. 2026-07-01T17:00:00' },
              ] as const).map((field) => (
                <View key={field.key} style={styles.fieldGroup}>
                  <Text style={styles.fieldLabel}>{field.label}</Text>
                  <TextInput
                    style={styles.textInput}
                    value={(form[field.key] as string) ?? ''}
                    onChangeText={(v) => setForm((p) => ({ ...p, [field.key]: v }))}
                    placeholder={field.placeholder}
                    placeholderTextColor={colors.textDisabled}
                    keyboardType={field.key === 'visitor_email' ? 'email-address' : 'default'}
                    autoCapitalize={field.key === 'visitor_email' ? 'none' : 'words'}
                  />
                </View>
              ))}
            </ScrollView>
            <Pressable
              style={[styles.confirmBtn, { marginTop: spacing.md }, addMutation.isPending && { opacity: 0.5 }]}
              onPress={() => {
                if (!form.full_name?.trim()) { Alert.alert('Required', 'Visitor name is required.'); return }
                addMutation.mutate(form)
              }}
              disabled={addMutation.isPending}
            >
              {addMutation.isPending
                ? <ActivityIndicator size="small" color="#fff" />
                : <Text style={styles.confirmBtnText}>Register & Generate QR</Text>}
            </Pressable>
          </View>
        </View>
      </Modal>
    </View>
  )
}

// ── Upcoming Tab ─────────────────────────────────────────────────────────────

function UpcomingTab() {
  const qc = useQueryClient()
  const [refreshing, setRefreshing] = useState(false)

  const { data: upcoming = [], isLoading } = useQuery({
    queryKey: ['visitors-upcoming'],
    queryFn: () => getUpcomingVisitors(24),
  })

  const checkinMut = useMutation({
    mutationFn: (v: Visitor) => checkinVisitor(v.id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['visitors'] }),
    onError: () => Alert.alert('Error', 'Failed to check in visitor.'),
  })

  const onRefresh = useCallback(async () => {
    setRefreshing(true)
    await qc.invalidateQueries({ queryKey: ['visitors-upcoming'] })
    setRefreshing(false)
  }, [qc])

  if (isLoading) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>

  if (upcoming.length === 0) {
    return (
      <View style={styles.center}>
        <Ionicons name="calendar-outline" size={48} color={colors.textDisabled} />
        <Text style={styles.emptyText}>No upcoming visitors in 24 h</Text>
      </View>
    )
  }

  return (
    <FlatList
      data={upcoming}
      keyExtractor={(item) => item.id}
      renderItem={({ item }) => (
        <Card style={styles.row}>
          <View style={styles.rowTop}>
            <View style={styles.avatar}>
              <Ionicons name="person" size={18} color={colors.primary} />
            </View>
            <View style={styles.rowInfo}>
              <Text style={styles.name}>{item.full_name}</Text>
              {item.company && <Text style={styles.sub}>{item.company}</Text>}
              {item.host_name && <Text style={styles.sub}>Host: {item.host_name}</Text>}
            </View>
            <StatusPill status={item.status} />
          </View>
          {item.expected_from && (
            <View style={styles.metaRow}>
              <Ionicons name="time-outline" size={12} color={colors.info} />
              <Text style={[styles.metaText, { color: colors.info }]}>
                {new Date(item.expected_from).toLocaleString([], { dateStyle: 'short', timeStyle: 'short' })}
              </Text>
            </View>
          )}
          {item.status === 'pending' && (
            <View style={styles.actionRow}>
              <Pressable
                style={[styles.actionBtn, { borderColor: colors.success }]}
                onPress={() => checkinMut.mutate(item)}
                disabled={checkinMut.isPending}
              >
                <Ionicons name="enter-outline" size={14} color={colors.success} />
                <Text style={[styles.actionText, { color: colors.success }]}>Check In</Text>
              </Pressable>
            </View>
          )}
        </Card>
      )}
      ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
      contentContainerStyle={styles.list}
      refreshControl={<RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />}
    />
  )
}

// ── QR Scan Tab ───────────────────────────────────────────────────────────────

function QRScanTab() {
  const qc = useQueryClient()
  const [permission, requestPermission] = useCameraPermissions()
  const [scanning, setScanning] = useState(false)
  const [lastResult, setLastResult] = useState<{ event_type: string; visitor_name: string } | null>(null)
  const cooldown = useRef(false)

  const scanMut = useMutation({
    mutationFn: (qr_token: string) => qrScanCheckin({ qr_token }),
    onSuccess: (data) => {
      setLastResult({ event_type: data.event_type, visitor_name: data.visitor.full_name })
      setScanning(false)
      qc.invalidateQueries({ queryKey: ['visitors'] })
      qc.invalidateQueries({ queryKey: ['visitors-upcoming'] })
      setTimeout(() => { cooldown.current = false; setLastResult(null) }, 4000)
    },
    onError: () => {
      Alert.alert('Scan Failed', 'QR code not recognised or visitor inactive.')
      cooldown.current = false
    },
  })

  if (!permission) return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>

  if (!permission.granted) {
    return (
      <View style={styles.center}>
        <Ionicons name="camera-outline" size={48} color={colors.textDisabled} />
        <Text style={styles.emptyText}>Camera permission required</Text>
        <Pressable style={[styles.confirmBtn, { paddingHorizontal: spacing.xl }]} onPress={requestPermission}>
          <Text style={styles.confirmBtnText}>Grant Permission</Text>
        </Pressable>
      </View>
    )
  }

  return (
    <View style={styles.flex}>
      <View style={styles.cameraWrap}>
        {scanning ? (
          <CameraView
            style={styles.camera}
            facing="back"
            barcodeScannerSettings={{ barcodeTypes: ['qr'] }}
            onBarcodeScanned={(result) => {
              if (cooldown.current || scanMut.isPending) return
              cooldown.current = true
              scanMut.mutate(result.data)
            }}
          />
        ) : (
          <View style={[styles.camera, styles.cameraPlaceholder]}>
            <Ionicons name="qr-code-outline" size={80} color={colors.cardBorder} />
            <Text style={styles.sub}>Tap scan to activate camera</Text>
          </View>
        )}
        <View style={styles.scanCorners}>
          {['tl', 'tr', 'bl', 'br'].map((p) => (
            <View key={p} style={[styles.corner, styles[`corner_${p}` as keyof typeof styles] as any]} />
          ))}
        </View>
      </View>

      {lastResult && (
        <View style={[styles.scanResult, { borderColor: lastResult.event_type === 'arrival' ? colors.success : colors.warning }]}>
          <Ionicons
            name={lastResult.event_type === 'arrival' ? 'enter-outline' : 'exit-outline'}
            size={20}
            color={lastResult.event_type === 'arrival' ? colors.success : colors.warning}
          />
          <View style={styles.rowInfo}>
            <Text style={styles.name}>{lastResult.visitor_name}</Text>
            <Text style={[styles.sub, { color: lastResult.event_type === 'arrival' ? colors.success : colors.warning }]}>
              {lastResult.event_type === 'arrival' ? 'Checked In' : 'Checked Out'}
            </Text>
          </View>
          <Ionicons name="checkmark-circle" size={24} color={lastResult.event_type === 'arrival' ? colors.success : colors.warning} />
        </View>
      )}

      {scanMut.isPending && (
        <View style={styles.scanResult}>
          <ActivityIndicator color={colors.primary} />
          <Text style={styles.sub}>Processing…</Text>
        </View>
      )}

      <Pressable
        style={[styles.scanToggle, scanning && { backgroundColor: colors.error }]}
        onPress={() => { setScanning((s) => !s); cooldown.current = false }}
      >
        <Ionicons name={scanning ? 'stop-circle-outline' : 'scan-outline'} size={22} color="#fff" />
        <Text style={styles.scanToggleText}>{scanning ? 'Stop Scanning' : 'Start QR Scan'}</Text>
      </Pressable>
    </View>
  )
}

// ── Root Screen ───────────────────────────────────────────────────────────────

export function VisitorsScreen() {
  const [tab, setTab] = useState<'list' | 'upcoming' | 'scan'>('list')

  return (
    <View style={styles.root}>
      <View style={styles.tabRow}>
        {([
          { key: 'list' as const,     label: 'Visitors', icon: 'people-outline' },
          { key: 'upcoming' as const, label: 'Upcoming', icon: 'calendar-outline' },
          { key: 'scan' as const,     label: 'QR Scan',  icon: 'scan-outline' },
        ]).map((t) => (
          <Pressable
            key={t.key}
            style={[styles.tabBtn, tab === t.key && styles.tabActive]}
            onPress={() => setTab(t.key)}
          >
            <Ionicons name={t.icon as any} size={16} color={tab === t.key ? '#fff' : colors.textSecondary} />
            <Text style={[styles.tabText, tab === t.key && styles.tabTextActive]}>{t.label}</Text>
          </Pressable>
        ))}
      </View>

      {tab === 'list'     && <ListTab />}
      {tab === 'upcoming' && <UpcomingTab />}
      {tab === 'scan'     && <QRScanTab />}
    </View>
  )
}

const styles = StyleSheet.create({
  root:           { flex: 1, backgroundColor: colors.background },
  flex:           { flex: 1 },
  totalCount:     { fontSize: fontSize.xs, color: colors.textSecondary, paddingHorizontal: spacing.md, paddingBottom: spacing.xs },
  center:         { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  emptyText:      { color: colors.textSecondary, fontSize: fontSize.md },
  tabRow:         { flexDirection: 'row', padding: spacing.sm, gap: spacing.xs },
  tabBtn:         { flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: 5, paddingVertical: 8, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder },
  tabActive:      { backgroundColor: colors.primary, borderColor: colors.primary },
  tabText:        { fontSize: fontSize.xs, color: colors.textSecondary, fontWeight: '600' },
  tabTextActive:  { color: '#fff' },
  chipRow:        { flexDirection: 'row', alignItems: 'center', paddingHorizontal: spacing.md, paddingVertical: spacing.xs },
  chipScroll:     { flexDirection: 'row', gap: spacing.xs, alignItems: 'center' },
  chip:           { borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: 5 },
  chipActive:     { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText:       { fontSize: fontSize.sm, color: colors.textSecondary },
  chipTextActive: { color: '#fff', fontWeight: '600' },
  addBtn:         { marginLeft: 'auto', padding: spacing.xs },
  list:           { padding: spacing.md, paddingTop: 0, paddingBottom: spacing.xl },
  row:            { gap: 6 },
  rowTop:         { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  avatar:         { width: 36, height: 36, borderRadius: 18, backgroundColor: colors.primaryMuted, alignItems: 'center', justifyContent: 'center' },
  rowInfo:        { flex: 1 },
  name:           { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  sub:            { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 1 },
  metaRow:        { flexDirection: 'row', alignItems: 'center', gap: 4 },
  metaText:       { fontSize: fontSize.xs, color: colors.textSecondary },
  pill:           { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.full, borderWidth: 1 },
  pillText:       { fontSize: 10, fontWeight: '700', textTransform: 'uppercase', letterSpacing: 0.5 },
  actionRow:      { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs, flexWrap: 'wrap' },
  actionBtn:      { flexDirection: 'row', alignItems: 'center', gap: 4, paddingHorizontal: spacing.sm, paddingVertical: 5, borderRadius: radius.sm, borderWidth: 1 },
  actionText:     { fontSize: fontSize.xs, fontWeight: '600' },
  // Camera
  cameraWrap:     { flex: 1, margin: spacing.md, borderRadius: radius.md, overflow: 'hidden', position: 'relative' },
  camera:         { flex: 1, minHeight: 300 },
  cameraPlaceholder: { backgroundColor: colors.card, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  scanCorners:    { ...StyleSheet.absoluteFillObject, pointerEvents: 'none' },
  corner:         { position: 'absolute', width: 24, height: 24, borderColor: colors.primary, borderWidth: 3 },
  corner_tl:      { top: 12, left: 12, borderRightWidth: 0, borderBottomWidth: 0 },
  corner_tr:      { top: 12, right: 12, borderLeftWidth: 0, borderBottomWidth: 0 },
  corner_bl:      { bottom: 12, left: 12, borderRightWidth: 0, borderTopWidth: 0 },
  corner_br:      { bottom: 12, right: 12, borderLeftWidth: 0, borderTopWidth: 0 },
  scanResult:     { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, margin: spacing.md, marginTop: 0, padding: spacing.md, borderRadius: radius.md, borderWidth: 1, borderColor: colors.cardBorder, backgroundColor: colors.card },
  scanToggle:     { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.sm, margin: spacing.md, marginTop: 0, paddingVertical: 14, borderRadius: radius.sm, backgroundColor: colors.primary },
  scanToggleText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  // Modals
  overlay:        { flex: 1, backgroundColor: 'rgba(0,0,0,0.6)', justifyContent: 'center', padding: spacing.md },
  modal:          { backgroundColor: '#0D1B2E', borderRadius: radius.xl, padding: spacing.lg, borderWidth: 1, borderColor: colors.cardBorder },
  formModal:      { maxHeight: '90%' },
  modalHeader:    { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center', marginBottom: spacing.md },
  modalTitle:     { fontSize: fontSize.lg, fontWeight: '700', color: colors.text },
  modalBody:      { fontSize: fontSize.md, color: colors.textSecondary, marginBottom: spacing.lg },
  modalBtns:      { flexDirection: 'row', gap: spacing.sm },
  cancelBtn:      { flex: 1, paddingVertical: 11, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder, alignItems: 'center' },
  cancelBtnText:  { color: colors.textSecondary, fontWeight: '600' },
  confirmBtn:     { flex: 1, paddingVertical: 11, borderRadius: radius.sm, backgroundColor: colors.primary, alignItems: 'center', justifyContent: 'center' },
  confirmBtnText: { color: '#fff', fontWeight: '700' },
  fieldGroup:     { marginBottom: spacing.sm },
  fieldLabel:     { fontSize: fontSize.xs, color: colors.textSecondary, marginBottom: 4, textTransform: 'uppercase', letterSpacing: 0.3 },
  textInput:      { height: 42, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder, backgroundColor: colors.surface, color: colors.text, paddingHorizontal: spacing.md, fontSize: fontSize.md },
})

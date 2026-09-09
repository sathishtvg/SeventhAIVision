/**
 * Defect log, filed from where the defect is.
 *
 * This is the register where mobile is not a convenience. A guard finds the
 * blown light while standing in the stairwell, and a photo taken there is
 * worth more than a description typed at a desk an hour later — the building
 * manager needs to know WHICH light.
 *
 * So reporting leads straight into the camera, the same way booking in found
 * property does. Everything else on the screen is the chasing list: what is
 * still open at this officer's sites, worst first.
 */
import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Modal, Pressable, ScrollView,
  StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import {
  listDefects, reportDefect, uploadDefectPhoto,
  DEFECT_CATEGORIES, DEFECT_SEVERITIES, type Defect,
} from '@/api/guardhouse'
import { getSites } from '@/api/sites'
import { Card } from '@/components/Card'
import { CheckInPhotoModal } from '@/components/CheckInPhotoModal'
import { colors, fontSize, radius, spacing } from '@/theme'

const SEVERITY_COLOUR: Record<string, string> = {
  safety_hazard: colors.error,
  high: colors.warning,
  medium: colors.secondary,
  low: colors.textSecondary,
}

const SEVERITY_LABEL: Record<string, string> = {
  safety_hazard: 'Hazard',
  high: 'High',
  medium: 'Medium',
  low: 'Low',
}

const STATUS_LABEL: Record<string, string> = {
  open: 'Open',
  reported: 'Reported',
  in_progress: 'In progress',
  resolved: 'Fixed',
  closed: 'Closed',
}

function fmtDate(ts: string) {
  return new Date(ts).toLocaleDateString(undefined, { day: '2-digit', month: 'short' })
}

function apiError(e: unknown, fallback: string) {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

function tidy(value: string) {
  return value.replace(/_/g, ' ')
}

export function DefectLogScreen() {
  const qc = useQueryClient()
  const [activeOnly, setActiveOnly] = useState(true)
  const [reporting, setReporting] = useState(false)
  const [photographing, setPhotographing] = useState<Defect | null>(null)

  const [siteId, setSiteId] = useState('')
  const [description, setDescription] = useState('')
  const [category, setCategory] = useState<string>('other')
  const [location, setLocation] = useState('')
  const [severity, setSeverity] = useState<string>('medium')

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const { data: defects = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['defects', activeOnly],
    queryFn: () => listDefects({ active_only: activeOnly }),
  })

  const invalidate = () => qc.invalidateQueries({ queryKey: ['defects'] })

  const report = useMutation({
    mutationFn: () => reportDefect({
      site_id: siteId,
      description: description.trim(),
      category,
      location: location.trim() || null,
      severity,
    }),
    onSuccess: (created) => {
      invalidate()
      setReporting(false)
      setDescription(''); setLocation(''); setCategory('other'); setSeverity('medium')
      // The guard is standing in front of it right now.
      setPhotographing(created)
    },
    onError: (e) => Alert.alert('Could not report this', apiError(e, 'Try again.')),
  })

  const photo = useMutation({
    mutationFn: (uri: string) => uploadDefectPhoto(photographing!.id, uri),
    onSuccess: () => { invalidate(); setPhotographing(null) },
    onError: (e) => Alert.alert('Could not upload the photo', apiError(e, 'Try again.')),
  })

  const openReport = () => {
    setSiteId(sites[0]?.id ?? '')
    setReporting(true)
  }

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load the defect log</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  const hazards = defects.filter(
    (d) => d.severity === 'safety_hazard'
      && ['open', 'reported', 'in_progress'].includes(d.status),
  ).length

  return (
    <View style={styles.container}>
      {hazards > 0 && (
        <View style={styles.banner}>
          <Ionicons name="warning" size={18} color="#fff" />
          <Text style={styles.bannerText}>
            {hazards} safety {hazards === 1 ? 'hazard' : 'hazards'} still open
          </Text>
        </View>
      )}

      <View style={styles.tabs}>
        {([[true, 'Still open'], [false, 'Everything']] as const).map(([value, label]) => (
          <Pressable
            key={label}
            style={[styles.tab, activeOnly === value && styles.tabActive]}
            onPress={() => setActiveOnly(value)}
          >
            <Text style={[styles.tabText, activeOnly === value && styles.tabTextActive]}>
              {label}
            </Text>
          </Pressable>
        ))}
      </View>

      <FlatList
        data={defects}
        keyExtractor={(d) => d.id}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <Card><Text style={styles.muted}>Nothing outstanding at your sites.</Text></Card>
        }
        renderItem={({ item }) => {
          const days = Math.floor(Number(item.days_open) || 0)
          const active = ['open', 'reported', 'in_progress'].includes(item.status)
          return (
            <Card>
              <View style={styles.row}>
                <View style={styles.flex}>
                  <Text style={styles.title}>{item.description}</Text>
                  <Text style={styles.meta}>
                    {tidy(item.category)} · {item.site_name}
                    {item.location ? ` · ${item.location}` : ''}
                  </Text>
                  <Text style={styles.meta}>
                    Reported {fmtDate(item.reported_at)}
                    {item.reported_by_name ? ` by ${item.reported_by_name}` : ''}
                    {active && days >= 14 ? ` · open ${days} days` : ''}
                  </Text>
                  {!!item.referred_to && (
                    <Text style={styles.meta}>
                      With {item.referred_to}
                      {item.reference_no ? ` (ref ${item.reference_no})` : ''}
                    </Text>
                  )}
                  {!active && !!item.resolution_notes && (
                    <Text style={styles.meta}>{item.resolution_notes}</Text>
                  )}
                </View>
                <View style={styles.badges}>
                  <View style={[styles.badge, { backgroundColor: SEVERITY_COLOUR[item.severity] }]}>
                    <Text style={styles.badgeText}>
                      {SEVERITY_LABEL[item.severity] ?? item.severity}
                    </Text>
                  </View>
                  <Text style={styles.statusText}>
                    {STATUS_LABEL[item.status] ?? item.status}
                  </Text>
                </View>
              </View>

              {active && (
                <Pressable style={styles.secondaryBtn} onPress={() => setPhotographing(item)}>
                  <Ionicons name="camera-outline" size={16} color={colors.primary} />
                  <Text style={styles.secondaryText}>
                    {item.has_photo ? 'Retake photo' : 'Add photo'}
                  </Text>
                </Pressable>
              )}
            </Card>
          )
        }}
      />

      <Pressable style={styles.fab} onPress={openReport}>
        <Ionicons name="add" size={26} color="#fff" />
      </Pressable>

      <Modal visible={reporting} animationType="slide" onRequestClose={() => setReporting(false)}>
        <View style={styles.modal}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Report a defect</Text>
            <Pressable onPress={() => setReporting(false)} hitSlop={12}>
              <Ionicons name="close" size={26} color={colors.text} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.body}>
            <Text style={styles.label}>Site</Text>
            <View style={styles.chipRow}>
              {sites.map((s) => (
                <Pressable
                  key={s.id}
                  style={[styles.chip, siteId === s.id && styles.chipActive]}
                  onPress={() => setSiteId(s.id)}
                >
                  <Text style={[styles.chipText, siteId === s.id && styles.chipTextActive]}>
                    {s.name}
                  </Text>
                </Pressable>
              ))}
            </View>

            <Text style={styles.label}>What is wrong</Text>
            <TextInput
              style={[styles.input, styles.multiline]} value={description}
              onChangeText={setDescription} multiline autoFocus
              placeholder="Specific enough that somebody else can find it"
              placeholderTextColor={colors.textSecondary}
            />

            <Text style={styles.label}>Where</Text>
            <TextInput
              style={styles.input} value={location} onChangeText={setLocation}
              placeholder="Stairwell B, level 4 landing"
              placeholderTextColor={colors.textSecondary}
            />

            <Text style={styles.label}>Category</Text>
            <View style={styles.chipRow}>
              {DEFECT_CATEGORIES.map((c) => (
                <Pressable
                  key={c}
                  style={[styles.chip, category === c && styles.chipActive]}
                  onPress={() => setCategory(c)}
                >
                  <Text style={[styles.chipText, category === c && styles.chipTextActive]}>
                    {tidy(c)}
                  </Text>
                </Pressable>
              ))}
            </View>

            <Text style={styles.label}>Severity</Text>
            <View style={styles.chipRow}>
              {DEFECT_SEVERITIES.map((s) => (
                <Pressable
                  key={s}
                  style={[
                    styles.chip,
                    severity === s && { backgroundColor: SEVERITY_COLOUR[s], borderColor: SEVERITY_COLOUR[s] },
                  ]}
                  onPress={() => setSeverity(s)}
                >
                  <Text style={[styles.chipText, severity === s && styles.chipTextActive]}>
                    {SEVERITY_LABEL[s]}
                  </Text>
                </Pressable>
              ))}
            </View>
          </ScrollView>
          <Pressable
            style={[
              styles.primaryBtn,
              (!description.trim() || !siteId || report.isPending) && styles.disabled,
            ]}
            onPress={() => report.mutate()}
            disabled={!description.trim() || !siteId || report.isPending}
          >
            <Text style={styles.primaryText}>
              {report.isPending ? 'Saving…' : 'Report and photograph'}
            </Text>
          </Pressable>
        </View>
      </Modal>

      <CheckInPhotoModal
        visible={!!photographing}
        title="Photograph the defect"
        facing="back"
        permissionPrompt="Camera permission is needed to photograph defects"
        confirming={photo.isPending}
        onClose={() => setPhotographing(null)}
        onConfirm={(uri) => photo.mutate(uri)}
      />
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  flex: { flex: 1 },
  banner: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    backgroundColor: colors.error, paddingVertical: spacing.xs, paddingHorizontal: spacing.md,
  },
  bannerText: { color: '#fff', fontWeight: '700', fontSize: fontSize.sm },
  tabs: { flexDirection: 'row', gap: spacing.sm, padding: spacing.md, paddingBottom: 0 },
  tab: {
    paddingHorizontal: spacing.md, paddingVertical: spacing.xs,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.divider,
  },
  tabActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  tabText: { color: colors.textSecondary, fontSize: fontSize.sm, fontWeight: '600' },
  tabTextActive: { color: '#fff' },
  list: { padding: spacing.md, gap: spacing.sm, paddingBottom: spacing.xl * 2 },
  row: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: spacing.sm },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs, marginTop: 2 },
  muted: { color: colors.textSecondary, fontSize: fontSize.sm },
  badges: { alignItems: 'flex-end', gap: 4 },
  badge: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.sm },
  badgeText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
  statusText: { color: colors.textSecondary, fontSize: fontSize.xs },
  errorText: { color: colors.text, fontSize: fontSize.md },
  retryBtn: {
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.xs,
  },
  retryText: { color: colors.primary, fontWeight: '600' },
  secondaryBtn: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs,
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.md, paddingVertical: spacing.xs, marginTop: spacing.xs,
  },
  secondaryText: { color: colors.primary, fontWeight: '600', fontSize: fontSize.sm },
  fab: {
    position: 'absolute', right: spacing.lg, bottom: spacing.lg,
    width: 54, height: 54, borderRadius: 27, backgroundColor: colors.primary,
    alignItems: 'center', justifyContent: 'center', elevation: 4,
  },
  modal: { flex: 1, backgroundColor: colors.background, paddingTop: spacing.xl },
  modalHeader: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: spacing.md, gap: spacing.sm,
  },
  modalTitle: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700', flex: 1 },
  body: { padding: spacing.md, paddingBottom: spacing.xl, gap: spacing.xs },
  label: { color: colors.textSecondary, fontSize: fontSize.xs, marginTop: spacing.sm },
  input: {
    backgroundColor: colors.surface, borderRadius: radius.md, color: colors.text,
    paddingHorizontal: spacing.md, paddingVertical: spacing.sm, fontSize: fontSize.md,
  },
  multiline: { minHeight: 72, textAlignVertical: 'top' },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: spacing.xs, marginTop: spacing.xs },
  chip: {
    paddingHorizontal: spacing.md, paddingVertical: spacing.xs,
    borderRadius: radius.md, borderWidth: 1, borderColor: colors.divider,
  },
  chipActive: { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText: { color: colors.textSecondary, fontSize: fontSize.xs },
  chipTextActive: { color: '#fff', fontWeight: '700' },
  primaryBtn: {
    backgroundColor: colors.primary, borderRadius: radius.md,
    paddingVertical: spacing.md, alignItems: 'center', margin: spacing.md,
  },
  primaryText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  disabled: { opacity: 0.5 },
})

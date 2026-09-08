/**
 * Lost & found, worked from the counter.
 *
 * Booking in and releasing both happen with somebody standing there, which is
 * why this is a phone screen rather than a page on a desktop the guard will
 * reach an hour later. The photo is taken at the moment of handover for the
 * same reason: it cannot be reconstructed afterwards, and it is what settles a
 * disputed claim.
 *
 * The claimant's identity is kept deliberately thin — a name and the last few
 * characters of a document. The check that matters is against the card in
 * their hand.
 */
import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Modal, Pressable, ScrollView,
  StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import {
  listLostFound, logLostFoundItem, releaseLostFoundItem, uploadLostFoundPhoto,
  LOST_FOUND_CATEGORIES, type LostFoundItem,
} from '@/api/guardhouse'
import { Card } from '@/components/Card'
import { CheckInPhotoModal } from '@/components/CheckInPhotoModal'
import { colors, fontSize, radius, spacing } from '@/theme'

const STATUS_COLOUR: Record<string, string> = {
  held: colors.warning,
  claimed: colors.success,
  disposed: colors.textSecondary,
  handed_to_police: colors.info,
}

function fmtDate(ts: string) {
  return new Date(ts).toLocaleDateString(undefined, { day: '2-digit', month: 'short' })
}

function apiError(e: unknown, fallback: string) {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

export function LostFoundScreen() {
  const qc = useQueryClient()
  const [tab, setTab] = useState<'held' | ''>('held')
  const [logging, setLogging] = useState(false)
  const [releasing, setReleasing] = useState<LostFoundItem | null>(null)
  const [photographing, setPhotographing] = useState<LostFoundItem | null>(null)

  // Book-in form
  const [description, setDescription] = useState('')
  const [category, setCategory] = useState<string>('other')
  const [foundLocation, setFoundLocation] = useState('')
  const [storage, setStorage] = useState('')

  // Release form
  const [claimName, setClaimName] = useState('')
  const [claimContact, setClaimContact] = useState('')
  const [claimIdLast4, setClaimIdLast4] = useState('')

  // See KeyRegisterScreen: useQuery is `any` here, so the type goes on the local.
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['lost-found', tab],
    queryFn: () => listLostFound(tab ? { status_filter: tab } : {}),
  })
  const items: LostFoundItem[] = data ?? []

  const invalidate = () => qc.invalidateQueries({ queryKey: ['lost-found'] })

  const log = useMutation({
    mutationFn: () => logLostFoundItem({
      description: description.trim(),
      category,
      found_location: foundLocation.trim() || null,
      storage_location: storage.trim() || null,
    }),
    onSuccess: (created) => {
      invalidate()
      setLogging(false)
      setDescription(''); setCategory('other'); setFoundLocation(''); setStorage('')
      // Straight into the camera: the item is still on the counter, and this
      // is the only moment a photo of it can be taken.
      setPhotographing(created)
    },
    onError: (e) => Alert.alert('Could not book this in', apiError(e, 'Try again.')),
  })

  const release = useMutation({
    mutationFn: () => releaseLostFoundItem(releasing!.id, {
      claimed_by_name: claimName.trim(),
      claimed_by_contact: claimContact.trim() || null,
      claimed_id_type: 'NRIC/FIN',
      claimed_id_last4: claimIdLast4.trim() || null,
    }),
    onSuccess: () => {
      invalidate()
      setReleasing(null); setClaimName(''); setClaimContact(''); setClaimIdLast4('')
    },
    onError: (e) => Alert.alert('Could not release', apiError(e, 'Try again.')),
  })

  const photo = useMutation({
    mutationFn: (uri: string) => uploadLostFoundPhoto(photographing!.id, uri),
    onSuccess: () => { invalidate(); setPhotographing(null) },
    onError: (e) => Alert.alert('Could not upload the photo', apiError(e, 'Try again.')),
  })

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load lost &amp; found</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  const overdue = items.filter((i) => i.due_for_disposal).length

  return (
    <View style={styles.container}>
      {overdue > 0 && (
        <View style={styles.banner}>
          <Ionicons name="alert-circle" size={18} color="#fff" />
          <Text style={styles.bannerText}>
            {overdue} item{overdue === 1 ? '' : 's'} held past the retention period
          </Text>
        </View>
      )}

      <View style={styles.tabs}>
        {([['held', 'Held'], ['', 'Everything']] as const).map(([value, label]) => (
          <Pressable
            key={label}
            style={[styles.tab, tab === value && styles.tabActive]}
            onPress={() => setTab(value)}
          >
            <Text style={[styles.tabText, tab === value && styles.tabTextActive]}>{label}</Text>
          </Pressable>
        ))}
      </View>

      <FlatList
        data={items}
        keyExtractor={(i) => i.id}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <Card><Text style={styles.muted}>Nothing in the register yet.</Text></Card>
        }
        renderItem={({ item }) => (
          <Card>
            <View style={styles.row}>
              <View style={styles.flex}>
                <Text style={styles.title}>{item.description}</Text>
                <Text style={styles.meta}>
                  {item.category} · found {fmtDate(item.found_at)}
                  {item.found_location ? ` · ${item.found_location}` : ''}
                </Text>
                {item.status === 'held' && !!item.storage_location && (
                  <Text style={styles.meta}>Stored: {item.storage_location}</Text>
                )}
                {item.status === 'claimed' && (
                  <Text style={styles.meta}>
                    Released to {item.claimed_by_name}
                    {item.claimed_id_last4 ? ` (…${item.claimed_id_last4})` : ''}
                  </Text>
                )}
              </View>
              <View style={[styles.badge, { backgroundColor: STATUS_COLOUR[item.status] }]}>
                <Text style={styles.badgeText}>
                  {item.status === 'handed_to_police' ? 'Police' : item.status}
                </Text>
              </View>
            </View>

            {item.status === 'held' && (
              <View style={styles.actions}>
                <Pressable style={styles.secondaryBtn} onPress={() => setReleasing(item)}>
                  <Ionicons name="hand-left-outline" size={16} color={colors.primary} />
                  <Text style={styles.secondaryText}>Release</Text>
                </Pressable>
                <Pressable style={styles.secondaryBtn} onPress={() => setPhotographing(item)}>
                  <Ionicons name="camera-outline" size={16} color={colors.primary} />
                  <Text style={styles.secondaryText}>
                    {item.has_photo ? 'Retake photo' : 'Photo'}
                  </Text>
                </Pressable>
              </View>
            )}
          </Card>
        )}
      />

      <Pressable style={styles.fab} onPress={() => setLogging(true)}>
        <Ionicons name="add" size={26} color="#fff" />
      </Pressable>

      {/* Book in */}
      <Modal visible={logging} animationType="slide" onRequestClose={() => setLogging(false)}>
        <View style={styles.modal}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle}>Book in found property</Text>
            <Pressable onPress={() => setLogging(false)} hitSlop={12}>
              <Ionicons name="close" size={26} color={colors.text} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.body}>
            <Text style={styles.label}>What is it</Text>
            <TextInput
              style={[styles.input, styles.multiline]} value={description}
              onChangeText={setDescription} multiline autoFocus
              placeholder="Enough detail to tell it apart from a similar item"
              placeholderTextColor={colors.textSecondary}
            />
            <Text style={styles.label}>Category</Text>
            <View style={styles.chipRow}>
              {LOST_FOUND_CATEGORIES.map((c) => (
                <Pressable
                  key={c}
                  style={[styles.chip, category === c && styles.chipActive]}
                  onPress={() => setCategory(c)}
                >
                  <Text style={[styles.chipText, category === c && styles.chipTextActive]}>{c}</Text>
                </Pressable>
              ))}
            </View>
            <Text style={styles.label}>Where it was found</Text>
            <TextInput
              style={styles.input} value={foundLocation} onChangeText={setFoundLocation}
              placeholder="Lobby, car park level 2…"
              placeholderTextColor={colors.textSecondary}
            />
            <Text style={styles.label}>Where you are storing it</Text>
            <TextInput
              style={styles.input} value={storage} onChangeText={setStorage}
              placeholder="Guardhouse drawer 2, safe…"
              placeholderTextColor={colors.textSecondary}
            />
          </ScrollView>
          <Pressable
            style={[styles.primaryBtn, (!description.trim() || log.isPending) && styles.disabled]}
            onPress={() => log.mutate()}
            disabled={!description.trim() || log.isPending}
          >
            <Text style={styles.primaryText}>
              {log.isPending ? 'Saving…' : 'Book in and photograph'}
            </Text>
          </Pressable>
        </View>
      </Modal>

      {/* Release */}
      <Modal visible={!!releasing} animationType="slide" onRequestClose={() => setReleasing(null)}>
        <View style={styles.modal}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle} numberOfLines={2}>Release to claimant</Text>
            <Pressable onPress={() => setReleasing(null)} hitSlop={12}>
              <Ionicons name="close" size={26} color={colors.text} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.body}>
            <Text style={styles.muted}>{releasing?.description}</Text>
            <Text style={styles.label}>Claimant name</Text>
            <TextInput
              style={styles.input} value={claimName} onChangeText={setClaimName} autoFocus
              placeholder="As shown on their document"
              placeholderTextColor={colors.textSecondary}
            />
            <Text style={styles.label}>Contact number</Text>
            <TextInput
              style={styles.input} value={claimContact} onChangeText={setClaimContact}
              keyboardType="phone-pad" placeholderTextColor={colors.textSecondary}
            />
            <Text style={styles.label}>Last 4 of NRIC / FIN</Text>
            <TextInput
              style={styles.input} value={claimIdLast4} maxLength={8}
              onChangeText={setClaimIdLast4} autoCapitalize="characters"
              placeholder="567A" placeholderTextColor={colors.textSecondary}
            />
            <Text style={styles.note}>
              Check the document in their hand. Only these last few characters are stored.
            </Text>
          </ScrollView>
          <Pressable
            style={[styles.primaryBtn, (!claimName.trim() || release.isPending) && styles.disabled]}
            onPress={() => release.mutate()}
            disabled={!claimName.trim() || release.isPending}
          >
            <Text style={styles.primaryText}>
              {release.isPending ? 'Recording…' : 'Release item'}
            </Text>
          </Pressable>
        </View>
      </Modal>

      <CheckInPhotoModal
        visible={!!photographing}
        title="Photograph the item"
        facing="back"
        permissionPrompt="Camera permission is needed to photograph found property"
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
    backgroundColor: colors.warning, paddingVertical: spacing.xs, paddingHorizontal: spacing.md,
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
  note: { color: colors.textSecondary, fontSize: fontSize.xs, marginTop: spacing.sm },
  badge: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.sm },
  badgeText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700', textTransform: 'capitalize' },
  actions: { flexDirection: 'row', gap: spacing.sm, marginTop: spacing.xs },
  errorText: { color: colors.text, fontSize: fontSize.md },
  retryBtn: {
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.xs,
  },
  retryText: { color: colors.primary, fontWeight: '600' },
  secondaryBtn: {
    flex: 1, flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: spacing.xs, borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.md, paddingVertical: spacing.xs,
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

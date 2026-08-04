/**
 * Post orders — the standing instructions for a site, read on site.
 *
 * Acknowledgement is a compliance record, not a UI nicety: it evidences that
 * this guard read THIS version. The version is shown next to the ack state on
 * purpose — an order revised after a guard acknowledged it shows as needing a
 * fresh acknowledgement, because their old one no longer covers the current
 * instructions.
 */
import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Modal, Pressable,
  ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import { acknowledgePostOrder, listPostOrders, type PostOrder } from '@/api/postOrders'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

export function PostOrdersScreen() {
  const qc = useQueryClient()
  const [open, setOpen] = useState<PostOrder | null>(null)

  const { data: orders = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['post-orders'],
    queryFn: () => listPostOrders(),
  })

  const ack = useMutation({
    mutationFn: (id: string) => acknowledgePostOrder(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['post-orders'] })
      setOpen(null)
    },
    onError: () => Alert.alert('Could not acknowledge', 'Check your connection and try again.'),
  })

  if (isLoading) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load post orders</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  // Orders still needing acknowledgement float to the top — that is the only
  // thing on this screen the guard must act on.
  const sorted = [...orders].sort((a, b) => {
    const aNeeds = a.requires_acknowledgment && !a.acknowledged ? 0 : 1
    const bNeeds = b.requires_acknowledgment && !b.acknowledged ? 0 : 1
    return aNeeds - bNeeds
  })
  const pending = sorted.filter((o) => o.requires_acknowledgment && !o.acknowledged).length

  return (
    <View style={styles.container}>
      {pending > 0 && (
        <View style={styles.banner}>
          <Ionicons name="alert-circle" size={18} color="#fff" />
          <Text style={styles.bannerText}>
            {pending} order{pending === 1 ? '' : 's'} need your acknowledgement
          </Text>
        </View>
      )}
      <FlatList
        data={sorted}
        keyExtractor={(o) => o.id}
        contentContainerStyle={styles.list}
        ListEmptyComponent={
          <Card><Text style={styles.muted}>No post orders for your sites.</Text></Card>
        }
        renderItem={({ item }) => {
          const needsAck = item.requires_acknowledgment && !item.acknowledged
          return (
            <Pressable onPress={() => setOpen(item)}>
              <Card>
                <View style={styles.row}>
                  <Text style={styles.title} numberOfLines={2}>{item.title}</Text>
                  {needsAck ? (
                    <View style={[styles.badge, { backgroundColor: colors.warning }]}>
                      <Text style={styles.badgeText}>Action</Text>
                    </View>
                  ) : item.requires_acknowledgment ? (
                    <View style={[styles.badge, { backgroundColor: colors.success }]}>
                      <Text style={styles.badgeText}>Read</Text>
                    </View>
                  ) : null}
                </View>
                <View style={styles.metaRow}>
                  {!!item.site_name && <Text style={styles.meta}>{item.site_name}</Text>}
                  <Text style={styles.meta}>{item.category}</Text>
                  <Text style={styles.meta}>v{item.version}</Text>
                </View>
              </Card>
            </Pressable>
          )
        }}
      />

      <Modal visible={!!open} animationType="slide" onRequestClose={() => setOpen(null)}>
        <View style={styles.modal}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle} numberOfLines={2}>{open?.title}</Text>
            <Pressable onPress={() => setOpen(null)} hitSlop={12}>
              <Ionicons name="close" size={26} color={colors.text} />
            </Pressable>
          </View>
          <View style={styles.metaRow}>
            {!!open?.site_name && <Text style={styles.meta}>{open.site_name}</Text>}
            <Text style={styles.meta}>{open?.category}</Text>
            <Text style={styles.meta}>Version {open?.version}</Text>
          </View>
          <ScrollView contentContainerStyle={styles.body}>
            <Text style={styles.bodyText}>
              {open?.body?.trim() || 'This post order has no written content.'}
            </Text>
          </ScrollView>
          {open?.requires_acknowledgment && !open?.acknowledged && (
            <Pressable
              style={styles.primaryBtn}
              onPress={() => ack.mutate(open.id)}
              disabled={ack.isPending}
            >
              <Text style={styles.primaryText}>
                {ack.isPending ? 'Recording…' : 'I have read and understood'}
              </Text>
            </Pressable>
          )}
          {open?.acknowledged && (
            <View style={styles.ackedRow}>
              <Ionicons name="checkmark-circle" size={18} color={colors.success} />
              <Text style={styles.muted}>Acknowledged (v{open.version})</Text>
            </View>
          )}
        </View>
      </Modal>
    </View>
  )
}

const styles = StyleSheet.create({
  container: { flex: 1, backgroundColor: colors.background },
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', gap: spacing.sm },
  banner: {
    flexDirection: 'row', alignItems: 'center', gap: spacing.xs,
    backgroundColor: colors.warning, paddingVertical: spacing.xs, paddingHorizontal: spacing.md,
  },
  bannerText: { color: '#fff', fontWeight: '700', fontSize: fontSize.sm },
  list: { padding: spacing.md, gap: spacing.sm },
  row: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: spacing.sm },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '600', flex: 1 },
  metaRow: { flexDirection: 'row', gap: spacing.md, marginTop: spacing.xs, flexWrap: 'wrap', paddingHorizontal: spacing.md },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs },
  muted: { color: colors.textSecondary, fontSize: fontSize.sm },
  badge: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.sm },
  badgeText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
  errorText: { color: colors.text, fontSize: fontSize.md },
  retryBtn: {
    borderColor: colors.primary, borderWidth: 1, borderRadius: radius.md,
    paddingHorizontal: spacing.lg, paddingVertical: spacing.xs,
  },
  retryText: { color: colors.primary, fontWeight: '600' },
  modal: { flex: 1, backgroundColor: colors.background, paddingTop: spacing.xl },
  modalHeader: {
    flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between',
    paddingHorizontal: spacing.md, gap: spacing.sm,
  },
  modalTitle: { color: colors.text, fontSize: fontSize.lg, fontWeight: '700', flex: 1 },
  body: { padding: spacing.md, paddingBottom: spacing.xl },
  bodyText: { color: colors.text, fontSize: fontSize.md, lineHeight: 22 },
  primaryBtn: {
    backgroundColor: colors.primary, borderRadius: radius.md,
    paddingVertical: spacing.md, alignItems: 'center', margin: spacing.md,
  },
  primaryText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  ackedRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'center',
    gap: spacing.xs, padding: spacing.md,
  },
})

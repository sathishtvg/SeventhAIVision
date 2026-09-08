/**
 * Key register, worked from the counter.
 *
 * The screen opens on what is out, not on the cabinet, because that is the
 * question a guard is asked ("has the aircon contractor returned the riser
 * key?") and the one they have to answer at handover. The cabinet list is
 * below it for the moment somebody is standing there wanting a key.
 *
 * Issuing takes a name and nothing else mandatory. Anything a phone keyboard
 * makes tedious at 3am is a field that gets left blank or filled with rubbish,
 * so due time, company and purpose are all optional — the name and the fact a
 * key left the cabinet are what matter.
 */
import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, FlatList, Modal, Pressable, ScrollView,
  StyleSheet, Text, TextInput, View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'

import {
  getOutstandingKeys, issueKey, listKeys, returnKey,
  type OutstandingKey, type SiteKey,
} from '@/api/guardhouse'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

function outFor(hours: number) {
  if (hours < 1) return `${Math.max(1, Math.round(hours * 60))} min`
  if (hours < 48) return `${hours.toFixed(1)} h`
  return `${Math.floor(hours / 24)} days`
}

function apiError(e: unknown, fallback: string) {
  const detail = (e as { response?: { data?: { detail?: string } } })?.response?.data?.detail
  return detail || fallback
}

export function KeyRegisterScreen() {
  const qc = useQueryClient()
  const [issuing, setIssuing] = useState<SiteKey | null>(null)
  const [name, setName] = useState('')
  const [company, setCompany] = useState('')
  const [purpose, setPurpose] = useState('')

  const { data: outstanding, isLoading: loadingOut } = useQuery({
    queryKey: ['keys-outstanding'],
    queryFn: () => getOutstandingKeys(),
  })

  const { data: keys = [], isLoading, isError, refetch } = useQuery({
    queryKey: ['keys'],
    queryFn: () => listKeys(),
  })

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['keys'] })
    qc.invalidateQueries({ queryKey: ['keys-outstanding'] })
  }

  const issue = useMutation({
    mutationFn: () => issueKey({
      key_id: issuing!.id,
      issued_to_name: name.trim(),
      issued_to_company: company.trim() || null,
      purpose: purpose.trim() || null,
    }),
    onSuccess: () => {
      invalidate()
      setIssuing(null); setName(''); setCompany(''); setPurpose('')
    },
    onError: (e) => Alert.alert('Could not issue', apiError(e, 'Try again.')),
  })

  const give = useMutation({
    mutationFn: (transactionId: string) => returnKey(transactionId),
    onSuccess: invalidate,
    onError: (e) => Alert.alert('Could not receive', apiError(e, 'Try again.')),
  })

  const confirmReturn = (transactionId: string, label: string) =>
    Alert.alert('Receive key', `Put ${label} back in the cabinet?`, [
      { text: 'Cancel', style: 'cancel' },
      { text: 'Receive', onPress: () => give.mutate(transactionId) },
    ])

  if (isLoading || loadingOut) {
    return <View style={styles.center}><ActivityIndicator color={colors.primary} /></View>
  }
  if (isError) {
    return (
      <View style={styles.center}>
        <Ionicons name="cloud-offline-outline" size={40} color={colors.textSecondary} />
        <Text style={styles.errorText}>Couldn’t load the key register</Text>
        <Pressable style={styles.retryBtn} onPress={() => refetch()}>
          <Text style={styles.retryText}>Retry</Text>
        </Pressable>
      </View>
    )
  }

  const overdue = outstanding?.overdue ?? 0
  const inCabinet = keys.filter((k) => !k.is_out && k.is_active)

  const renderOutstanding = (row: OutstandingKey) => (
    <Card key={row.id}>
      <View style={styles.row}>
        <View style={styles.flex}>
          <Text style={styles.title}>{row.key_code} — {row.label}</Text>
          <Text style={styles.meta}>
            {row.held_by_name}{row.held_by_company ? ` · ${row.held_by_company}` : ''}
          </Text>
          <Text style={styles.meta}>
            {row.site_name} · out {outFor(Number(row.hours_out))}
          </Text>
        </View>
        {row.is_overdue && (
          <View style={[styles.badge, { backgroundColor: colors.error }]}>
            <Text style={styles.badgeText}>Overdue</Text>
          </View>
        )}
      </View>
      <Pressable
        style={styles.secondaryBtn}
        onPress={() => confirmReturn(row.id, `${row.key_code} — ${row.label}`)}
        disabled={give.isPending}
      >
        <Ionicons name="log-in-outline" size={16} color={colors.primary} />
        <Text style={styles.secondaryText}>Receive back</Text>
      </Pressable>
    </Card>
  )

  return (
    <View style={styles.container}>
      {overdue > 0 && (
        <View style={styles.banner}>
          <Ionicons name="alert-circle" size={18} color="#fff" />
          <Text style={styles.bannerText}>
            {overdue} key{overdue === 1 ? ' is' : 's are'} past their return time
          </Text>
        </View>
      )}

      <FlatList
        data={inCabinet}
        keyExtractor={(k) => k.id}
        contentContainerStyle={styles.list}
        ListHeaderComponent={
          <View style={styles.headerBlock}>
            <Text style={styles.sectionTitle}>
              Out of the cabinet ({outstanding?.out ?? 0})
            </Text>
            {(outstanding?.keys.length ?? 0) === 0 ? (
              <Card><Text style={styles.muted}>Every key is accounted for.</Text></Card>
            ) : (
              outstanding!.keys.map(renderOutstanding)
            )}
            <Text style={[styles.sectionTitle, { marginTop: spacing.md }]}>
              In the cabinet ({inCabinet.length})
            </Text>
          </View>
        }
        ListEmptyComponent={
          <Card><Text style={styles.muted}>No keys in the cabinet for your sites.</Text></Card>
        }
        renderItem={({ item }) => (
          <Card>
            <View style={styles.row}>
              <View style={styles.flex}>
                <Text style={styles.title}>{item.key_code} — {item.label}</Text>
                <Text style={styles.meta}>
                  {item.site_name}
                  {item.cabinet_position ? ` · position ${item.cabinet_position}` : ''}
                </Text>
              </View>
              <Pressable
                style={styles.secondaryBtn}
                onPress={() => { setIssuing(item); setName(''); setCompany(''); setPurpose('') }}
              >
                <Ionicons name="log-out-outline" size={16} color={colors.primary} />
                <Text style={styles.secondaryText}>Issue</Text>
              </Pressable>
            </View>
          </Card>
        )}
      />

      <Modal visible={!!issuing} animationType="slide" onRequestClose={() => setIssuing(null)}>
        <View style={styles.modal}>
          <View style={styles.modalHeader}>
            <Text style={styles.modalTitle} numberOfLines={2}>
              Issue {issuing?.key_code} — {issuing?.label}
            </Text>
            <Pressable onPress={() => setIssuing(null)} hitSlop={12}>
              <Ionicons name="close" size={26} color={colors.text} />
            </Pressable>
          </View>
          <ScrollView contentContainerStyle={styles.body}>
            <Text style={styles.label}>Issued to</Text>
            <TextInput
              style={styles.input} value={name} onChangeText={setName} autoFocus
              placeholder="Name of the person taking it"
              placeholderTextColor={colors.textSecondary}
            />
            <Text style={styles.label}>Company (optional)</Text>
            <TextInput
              style={styles.input} value={company} onChangeText={setCompany}
              placeholder="Contractor or tenant"
              placeholderTextColor={colors.textSecondary}
            />
            <Text style={styles.label}>Purpose (optional)</Text>
            <TextInput
              style={styles.input} value={purpose} onChangeText={setPurpose}
              placeholder="What they need it for"
              placeholderTextColor={colors.textSecondary}
            />
          </ScrollView>
          <Pressable
            style={[styles.primaryBtn, (!name.trim() || issue.isPending) && styles.disabled]}
            onPress={() => issue.mutate()}
            disabled={!name.trim() || issue.isPending}
          >
            <Text style={styles.primaryText}>
              {issue.isPending ? 'Recording…' : 'Issue key'}
            </Text>
          </Pressable>
        </View>
      </Modal>
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
  list: { padding: spacing.md, gap: spacing.sm },
  headerBlock: { gap: spacing.sm },
  sectionTitle: { color: colors.text, fontSize: fontSize.sm, fontWeight: '700', textTransform: 'uppercase' },
  row: { flexDirection: 'row', alignItems: 'flex-start', justifyContent: 'space-between', gap: spacing.sm },
  title: { color: colors.text, fontSize: fontSize.md, fontWeight: '600' },
  meta: { color: colors.textSecondary, fontSize: fontSize.xs, marginTop: 2 },
  muted: { color: colors.textSecondary, fontSize: fontSize.sm },
  badge: { paddingHorizontal: spacing.sm, paddingVertical: 2, borderRadius: radius.sm },
  badgeText: { color: '#fff', fontSize: fontSize.xs, fontWeight: '700' },
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
  primaryBtn: {
    backgroundColor: colors.primary, borderRadius: radius.md,
    paddingVertical: spacing.md, alignItems: 'center', margin: spacing.md,
  },
  primaryText: { color: '#fff', fontWeight: '700', fontSize: fontSize.md },
  disabled: { opacity: 0.5 },
})

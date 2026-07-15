import React, { useState } from 'react'
import {
  ActivityIndicator, FlatList, RefreshControl,
  StyleSheet, Text, TouchableOpacity, View,
} from 'react-native'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getPlateWatchlist, getFaceWatchlist } from '@/api/watchlist'
import { Card } from '@/components/Card'
import { colors, fontSize, spacing, radius } from '@/theme'

type Tab = 'plates' | 'faces'

function TabBar({ active, onChange }: { active: Tab; onChange: (t: Tab) => void }) {
  return (
    <View style={styles.tabBar}>
      {(['plates', 'faces'] as Tab[]).map((t) => (
        <TouchableOpacity
          key={t}
          style={[styles.tab, active === t && styles.tabActive]}
          onPress={() => onChange(t)}
        >
          <Text style={[styles.tabText, active === t && styles.tabTextActive]}>
            {t === 'plates' ? 'Plates' : 'Faces'}
          </Text>
        </TouchableOpacity>
      ))}
    </View>
  )
}

function ListTypeBadge({ value }: { value: 'allow' | 'block' }) {
  const isBlock = value === 'block'
  return (
    <View style={[styles.badge, { backgroundColor: isBlock ? colors.error + '22' : colors.success + '22' }]}>
      <Text style={[styles.badgeText, { color: isBlock ? colors.error : colors.success }]}>
        {isBlock ? 'BLOCK' : 'ALLOW'}
      </Text>
    </View>
  )
}

export function WatchlistsScreen() {
  const [tab, setTab] = useState<Tab>('plates')
  const qc = useQueryClient()

  const platesQuery = useQuery({ queryKey: ['plate-watchlist'], queryFn: getPlateWatchlist })
  const facesQuery  = useQuery({ queryKey: ['face-watchlist'],  queryFn: getFaceWatchlist })

  const isLoading  = tab === 'plates' ? platesQuery.isLoading  : facesQuery.isLoading
  const onRefresh  = () => qc.invalidateQueries({ queryKey: [tab === 'plates' ? 'plate-watchlist' : 'face-watchlist'] })

  return (
    <View style={styles.root}>
      <TabBar active={tab} onChange={setTab} />

      {isLoading ? (
        <View style={styles.center}>
          <ActivityIndicator color={colors.primary} />
        </View>
      ) : tab === 'plates' ? (
        <FlatList
          data={platesQuery.data ?? []}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={platesQuery.isFetching} onRefresh={onRefresh} tintColor={colors.primary} />}
          ListEmptyComponent={<Text style={styles.empty}>No plate entries</Text>}
          renderItem={({ item }) => (
            <Card style={styles.item}>
              <View style={styles.itemRow}>
                <View style={styles.iconWrap}>
                  <Ionicons name="car-outline" size={18} color={colors.primary} />
                </View>
                <View style={styles.itemBody}>
                  <Text style={styles.itemTitle}>{item.plate_number}</Text>
                  {item.reason ? (
                    <Text style={styles.itemSub} numberOfLines={1}>{item.reason}</Text>
                  ) : null}
                </View>
                <ListTypeBadge value={item.list_type} />
              </View>
              {item.expires_at ? (
                <Text style={styles.meta}>
                  Expires {new Date(item.expires_at).toLocaleDateString()}
                </Text>
              ) : null}
            </Card>
          )}
          ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
        />
      ) : (
        <FlatList
          data={facesQuery.data ?? []}
          keyExtractor={(item) => item.id}
          contentContainerStyle={styles.list}
          refreshControl={<RefreshControl refreshing={facesQuery.isFetching} onRefresh={onRefresh} tintColor={colors.primary} />}
          ListEmptyComponent={<Text style={styles.empty}>No face entries</Text>}
          renderItem={({ item }) => (
            <Card style={styles.item}>
              <View style={styles.itemRow}>
                <View style={styles.iconWrap}>
                  <Ionicons name="person-outline" size={18} color={colors.primary} />
                </View>
                <View style={styles.itemBody}>
                  <Text style={styles.itemTitle}>{item.person_name}</Text>
                  <Text style={styles.itemSub}>
                    Added {new Date(item.created_at).toLocaleDateString()}
                  </Text>
                </View>
                <ListTypeBadge value={item.list_type} />
              </View>
            </Card>
          )}
          ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
        />
      )}
    </View>
  )
}

const styles = StyleSheet.create({
  root:          { flex: 1, backgroundColor: colors.background },
  center:        { flex: 1, alignItems: 'center', justifyContent: 'center' },
  tabBar:        { flexDirection: 'row', backgroundColor: colors.surface, borderBottomWidth: 1, borderBottomColor: colors.divider },
  tab:           { flex: 1, paddingVertical: spacing.sm + 2, alignItems: 'center' },
  tabActive:     { borderBottomWidth: 2, borderBottomColor: colors.primary },
  tabText:       { fontSize: fontSize.sm, fontWeight: '600', color: colors.textSecondary },
  tabTextActive: { color: colors.primary },
  list:          { padding: spacing.md, paddingBottom: spacing.xl },
  item:          { paddingVertical: spacing.sm },
  itemRow:       { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  iconWrap:      { width: 36, height: 36, borderRadius: radius.sm, backgroundColor: colors.primary + '18', alignItems: 'center', justifyContent: 'center' },
  itemBody:      { flex: 1, minWidth: 0 },
  itemTitle:     { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  itemSub:       { fontSize: fontSize.xs, color: colors.textSecondary, marginTop: 2 },
  meta:          { fontSize: fontSize.xs, color: colors.textDisabled, marginTop: spacing.xs },
  badge:         { paddingHorizontal: spacing.sm, paddingVertical: 3, borderRadius: radius.sm },
  badgeText:     { fontSize: fontSize.xs, fontWeight: '700', letterSpacing: 0.5 },
  empty:         { textAlign: 'center', color: colors.textSecondary, fontSize: fontSize.sm, paddingTop: spacing.xl },
})

import React from 'react'
import { ActivityIndicator, FlatList, Pressable, StyleSheet, Text, View } from 'react-native'
import { useQuery } from '@tanstack/react-query'
import { useRoute, useNavigation, type RouteProp } from '@react-navigation/native'
import type { NativeStackNavigationProp } from '@react-navigation/native-stack'
import { Ionicons } from '@expo/vector-icons'
import { getRoutes } from '@/api/patrols'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'
import type { PatrolStackParamList } from '@/navigation'

type RoutePropType = RouteProp<PatrolStackParamList, 'PatrolSelect'>
type NavProp = NativeStackNavigationProp<PatrolStackParamList>

export function PatrolSelectScreen() {
  const { params } = useRoute<RoutePropType>()
  const navigation = useNavigation<NavProp>()

  const { data: routes = [], isLoading } = useQuery({
    queryKey: ['patrol-routes'],
    queryFn: () => getRoutes(),
  })

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    )
  }

  return (
    <FlatList
      data={routes as any[]}
      keyExtractor={(r) => r.id}
      contentContainerStyle={styles.list}
      ListHeaderComponent={
        <Text style={styles.header}>Select Patrol Route</Text>
      }
      ListEmptyComponent={
        <Card>
          <Text style={styles.empty}>No patrol routes configured for your site.</Text>
        </Card>
      }
      renderItem={({ item: route }) => (
        <Pressable
          onPress={() =>
            navigation.navigate('PatrolScan', { routeId: route.id, shiftId: params.shiftId })
          }
        >
          <Card style={styles.routeCard}>
            <View style={styles.routeRow}>
              <View style={styles.routeInfo}>
                <Text style={styles.routeName}>{route.name}</Text>
                <Text style={styles.routeSite}>{route.site_name || '—'}</Text>
                <Text style={styles.routeCp}>
                  {route.checkpoint_count} checkpoint{route.checkpoint_count !== 1 ? 's' : ''}
                </Text>
              </View>
              <Ionicons name="chevron-forward" size={20} color={colors.textSecondary} />
            </View>
          </Card>
        </Pressable>
      )}
      ItemSeparatorComponent={() => <View style={{ height: spacing.xs }} />}
    />
  )
}

const styles = StyleSheet.create({
  center: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.background },
  list: { padding: spacing.md },
  header: { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.sm },
  empty: { color: colors.textSecondary, textAlign: 'center', paddingVertical: spacing.sm },
  routeCard: {},
  routeRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between' },
  routeInfo: { flex: 1 },
  routeName: { fontSize: fontSize.md, fontWeight: '700', color: colors.text },
  routeSite: { fontSize: fontSize.sm, color: colors.textSecondary, marginTop: 2 },
  routeCp: { fontSize: fontSize.xs, color: colors.primary, marginTop: 2 },
})

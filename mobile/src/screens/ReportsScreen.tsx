import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, Linking, Pressable,
  ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useQuery } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getSites, type Site } from '@/api/sites'
import { getReportUrl } from '@/api/reports'
import { Card } from '@/components/Card'
import { colors, fontSize, radius, spacing } from '@/theme'

interface ReportType {
  key: string
  label: string
  description: string
  icon: React.ComponentProps<typeof Ionicons>['name']
  color: string
}

const REPORT_TYPES: ReportType[] = [
  {
    key: 'site-summary',
    label: 'Site Summary',
    description: 'Daily or weekly alert and incident summary for a site.',
    icon: 'bar-chart-outline',
    color: colors.primary,
  },
  {
    key: 'dob',
    label: 'Daily Occurrence Book',
    description: 'Full occurrence book printout for a date range.',
    icon: 'book-outline',
    color: colors.secondary,
  },
]

export function ReportsScreen() {
  const [selected, setSelected] = useState<ReportType | null>(null)
  const [siteId, setSiteId]   = useState<string>('')
  const [loading, setLoading] = useState(false)

  const { data: sites = [] } = useQuery({
    queryKey: ['sites'],
    queryFn: getSites,
  })

  const today = new Date().toISOString().slice(0, 10)
  const weekAgo = new Date(Date.now() - 7 * 86400_000).toISOString().slice(0, 10)

  const generateReport = async (type: ReportType) => {
    if (type.key === 'site-summary' && sites.length > 0 && !siteId) {
      Alert.alert('Site required', 'Please select a site for the summary report.')
      return
    }
    setLoading(true)
    try {
      const params: Record<string, string> = {}
      if (type.key === 'site-summary') {
        if (siteId) params.site_id = siteId
        params.report_type = 'weekly'
        params.date = today
      } else if (type.key === 'dob') {
        params.date_from = weekAgo
        params.date_to   = today
        if (siteId) params.site_id = siteId
      }
      const url = getReportUrl(type.key as any, params)
      const supported = await Linking.canOpenURL(url)
      if (supported) {
        await Linking.openURL(url)
      } else {
        Alert.alert('Cannot open', 'No app available to open PDF files.')
      }
    } catch {
      Alert.alert('Error', 'Failed to generate report.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.hint}>
        Reports open as PDF in your browser. Make sure you're connected to the backend server.
      </Text>

      {/* Site selector */}
      {sites.length > 0 && (
        <>
          <Text style={styles.sectionTitle}>Filter by Site (optional)</Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} contentContainerStyle={styles.chipRow}>
            <Pressable
              style={[styles.chip, siteId === '' && styles.chipActive]}
              onPress={() => setSiteId('')}
            >
              <Text style={[styles.chipText, siteId === '' && styles.chipTextActive]}>All Sites</Text>
            </Pressable>
            {sites.map((s: Site) => (
              <Pressable
                key={s.id}
                style={[styles.chip, siteId === s.id && styles.chipActive]}
                onPress={() => setSiteId(s.id)}
              >
                <Text style={[styles.chipText, siteId === s.id && styles.chipTextActive]}>{s.name}</Text>
              </Pressable>
            ))}
          </ScrollView>
        </>
      )}

      <Text style={styles.sectionTitle}>Generate Report</Text>
      {REPORT_TYPES.map((type) => (
        <Card key={type.key} style={styles.card}>
          <View style={styles.cardTop}>
            <View style={[styles.iconWrap, { backgroundColor: type.color + '28' }]}>
              <Ionicons name={type.icon} size={22} color={type.color} />
            </View>
            <View style={styles.cardInfo}>
              <Text style={styles.cardTitle}>{type.label}</Text>
              <Text style={styles.cardDesc}>{type.description}</Text>
            </View>
          </View>
          <Pressable
            style={[styles.generateBtn, { borderColor: type.color }, loading && { opacity: 0.5 }]}
            onPress={() => generateReport(type)}
            disabled={loading}
          >
            {loading ? (
              <ActivityIndicator size="small" color={type.color} />
            ) : (
              <>
                <Ionicons name="download-outline" size={15} color={type.color} />
                <Text style={[styles.generateText, { color: type.color }]}>Open PDF</Text>
              </>
            )}
          </Pressable>
        </Card>
      ))}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root:          { flex: 1, backgroundColor: colors.background },
  content:       { padding: spacing.md, paddingBottom: spacing.xxl },
  hint:          { fontSize: fontSize.xs, color: colors.textSecondary, lineHeight: 18, marginBottom: spacing.md, backgroundColor: colors.infoMuted, borderRadius: radius.sm, padding: spacing.sm },
  sectionTitle:  { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.sm, marginTop: spacing.sm },
  chipRow:       { flexDirection: 'row', gap: spacing.xs, marginBottom: spacing.md, paddingBottom: 2 },
  chip:          { borderRadius: radius.full, borderWidth: 1, borderColor: colors.cardBorder, paddingHorizontal: spacing.md, paddingVertical: 6 },
  chipActive:    { backgroundColor: colors.primary, borderColor: colors.primary },
  chipText:      { fontSize: fontSize.sm, color: colors.textSecondary },
  chipTextActive:{ color: '#fff', fontWeight: '600' },
  card:          { marginBottom: spacing.sm },
  cardTop:       { flexDirection: 'row', gap: spacing.md, alignItems: 'flex-start', marginBottom: spacing.md },
  iconWrap:      { width: 44, height: 44, borderRadius: radius.md, alignItems: 'center', justifyContent: 'center' },
  cardInfo:      { flex: 1 },
  cardTitle:     { fontSize: fontSize.md, fontWeight: '700', color: colors.text, marginBottom: 4 },
  cardDesc:      { fontSize: fontSize.sm, color: colors.textSecondary, lineHeight: 18 },
  generateBtn:   { flexDirection: 'row', alignItems: 'center', justifyContent: 'center', gap: spacing.xs, paddingVertical: 10, borderRadius: radius.sm, borderWidth: 1 },
  generateText:  { fontSize: fontSize.sm, fontWeight: '700' },
})

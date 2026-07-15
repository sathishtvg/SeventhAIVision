import React from 'react'
import {
  ActivityIndicator, Alert as RNAlert, Pressable,
  ScrollView, StyleSheet, Text, View,
} from 'react-native'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { useRoute, type RouteProp } from '@react-navigation/native'
import { Ionicons } from '@expo/vector-icons'
import { getAlerts, acknowledgeAlert, markFalsePositive, type Alert } from '@/api/alerts'
import { Card } from '@/components/Card'
import { SeverityBadge } from '@/components/SeverityBadge'
import { StatusBadge } from '@/components/StatusBadge'
import { colors, fontSize, spacing } from '@/theme'
import type { AlertsStackParamList } from '@/navigation'

type RoutePropType = RouteProp<AlertsStackParamList, 'AlertDetail'>

function InfoRow({ label, value }: { label: string; value: string | null | undefined }) {
  if (!value) return null
  return (
    <View style={styles.infoRow}>
      <Text style={styles.infoLabel}>{label}</Text>
      <Text style={styles.infoValue}>{value}</Text>
    </View>
  )
}

export function AlertDetailScreen() {
  const { params } = useRoute<RoutePropType>()
  const qc = useQueryClient()

  const { data: alerts = [], isLoading } = useQuery({
    queryKey: ['alerts', 'all'],
    queryFn: () => getAlerts(undefined),
  })

  const alert = alerts.find((a: Alert) => a.id === params.alertId)

  const { mutate: ack, isPending } = useMutation({
    mutationFn: () => acknowledgeAlert(params.alertId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] })
      RNAlert.alert('Acknowledged', 'The alert has been acknowledged.')
    },
    onError: () => {
      RNAlert.alert('Error', 'Failed to acknowledge the alert. Please try again.')
    },
  })

  const { mutate: markFp, isPending: fpPending } = useMutation({
    mutationFn: () => markFalsePositive(params.alertId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['alerts'] })
      RNAlert.alert('Marked', 'The alert has been marked as a false positive.')
    },
    onError: () => {
      RNAlert.alert('Error', 'Failed to mark the alert. Please try again.')
    },
  })

  const confirmFalsePositive = () => {
    RNAlert.alert(
      'Mark as false positive?',
      'This tells the system the AI detection was incorrect.',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Mark False Positive', style: 'destructive', onPress: () => markFp() },
      ],
    )
  }

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    )
  }

  if (!alert) {
    return (
      <View style={styles.center}>
        <Ionicons name="alert-circle-outline" size={48} color={colors.textDisabled} />
        <Text style={styles.emptyText}>Alert not found</Text>
      </View>
    )
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Card>
        <Text style={styles.title}>{alert.title}</Text>
        <View style={styles.badgeRow}>
          <SeverityBadge value={alert.severity} />
          <StatusBadge value={alert.status} />
        </View>
        {alert.message && <Text style={styles.message}>{alert.message}</Text>}
      </Card>

      <Card style={styles.detailsCard}>
        <Text style={styles.sectionTitle}>Details</Text>
        <InfoRow label="Module" value={alert.module_type} />
        <InfoRow label="Alert Code" value={alert.alert_code} />
        <InfoRow label="Camera" value={alert.camera_name} />
        <InfoRow label="Created" value={new Date(alert.created_at).toLocaleString()} />
        {alert.acknowledged_at && (
          <InfoRow
            label="Acknowledged"
            value={`${new Date(alert.acknowledged_at).toLocaleString()}${alert.acknowledged_by_name ? ` by ${alert.acknowledged_by_name}` : ''}${alert.acknowledged_via ? ` (${alert.acknowledged_via})` : ''}`}
          />
        )}
        {alert.fp_marked_at && (
          <InfoRow
            label="False positive"
            value={`${new Date(alert.fp_marked_at).toLocaleString()}${alert.fp_marked_by_name ? ` by ${alert.fp_marked_by_name}` : ''}${alert.fp_marked_via ? ` (${alert.fp_marked_via})` : ''}`}
          />
        )}
      </Card>

      {(alert.status === 'open' || alert.status === 'acknowledged') && (
        <View style={styles.actionRow}>
          {alert.status === 'open' && (
            <Pressable
              style={[styles.ackBtn, isPending && styles.ackBtnDisabled]}
              onPress={() => ack()}
              disabled={isPending}
            >
              {isPending
                ? <ActivityIndicator color="#fff" size="small" />
                : (
                  <>
                    <Ionicons name="checkmark-circle" size={18} color="#fff" />
                    <Text style={styles.ackBtnText}>Acknowledge Alert</Text>
                  </>
                )
              }
            </Pressable>
          )}
          <Pressable
            style={[styles.fpBtn, fpPending && styles.ackBtnDisabled]}
            onPress={confirmFalsePositive}
            disabled={fpPending}
          >
            {fpPending
              ? <ActivityIndicator color={colors.warning} size="small" />
              : (
                <>
                  <Ionicons name="close-circle-outline" size={18} color={colors.warning} />
                  <Text style={styles.fpBtnText}>Mark False Positive</Text>
                </>
              )
            }
          </Pressable>
        </View>
      )}
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    backgroundColor: colors.background,
  },
  content: {
    padding: spacing.md,
    gap: spacing.md,
    paddingBottom: spacing.xl,
  },
  center: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    backgroundColor: colors.background,
    gap: spacing.sm,
  },
  title: {
    fontSize: fontSize.lg,
    fontWeight: '700',
    color: colors.text,
    marginBottom: spacing.sm,
  },
  badgeRow: {
    flexDirection: 'row',
    gap: spacing.xs,
    marginBottom: spacing.sm,
  },
  message: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
    lineHeight: 20,
  },
  detailsCard: {
    gap: spacing.xs,
  },
  sectionTitle: {
    fontSize: fontSize.md,
    fontWeight: '700',
    color: colors.text,
    marginBottom: spacing.xs,
  },
  infoRow: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    paddingVertical: 4,
    borderBottomWidth: StyleSheet.hairlineWidth,
    borderBottomColor: colors.divider,
  },
  infoLabel: {
    fontSize: fontSize.sm,
    color: colors.textSecondary,
  },
  infoValue: {
    fontSize: fontSize.sm,
    color: colors.text,
    fontWeight: '500',
    maxWidth: '60%',
    textAlign: 'right',
  },
  actionRow: {
    gap: spacing.sm,
  },
  ackBtn: {
    backgroundColor: colors.success,
    borderRadius: 8,
    paddingVertical: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
  },
  ackBtnDisabled: {
    opacity: 0.6,
  },
  ackBtnText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: fontSize.md,
  },
  fpBtn: {
    backgroundColor: 'transparent',
    borderRadius: 8,
    borderWidth: 1,
    borderColor: colors.warning,
    paddingVertical: spacing.md,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
  },
  fpBtnText: {
    color: colors.warning,
    fontWeight: '700',
    fontSize: fontSize.md,
  },
  emptyText: {
    fontSize: fontSize.md,
    color: colors.textSecondary,
  },
})

import React, { useEffect, useRef, useState } from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { Ionicons } from '@expo/vector-icons'
import { useNetworkStatus } from '@/hooks/useNetworkStatus'
import { flushOutbox, subscribeOutbox } from '@/offline/outbox'
import { colors, fontSize, spacing } from '@/theme'

export function OfflineBanner() {
  const { isOnline } = useNetworkStatus()
  const [queued, setQueued] = useState(0)
  const wasOffline = useRef(false)

  useEffect(() => subscribeOutbox(setQueued), [])

  // Replay queued actions the moment connectivity returns (Gap 88)
  useEffect(() => {
    if (isOnline && (wasOffline.current || queued > 0)) {
      void flushOutbox()
    }
    wasOffline.current = !isOnline
  }, [isOnline, queued])

  if (isOnline && queued === 0) return null

  return (
    <View style={[styles.banner, isOnline && styles.syncing]}>
      <Ionicons
        name={isOnline ? 'cloud-upload-outline' : 'cloud-offline-outline'}
        size={13}
        color="#fff"
      />
      <Text style={styles.text}>
        {isOnline
          ? `Syncing ${queued} queued action${queued === 1 ? '' : 's'}…`
          : queued > 0
            ? `No internet — ${queued} action${queued === 1 ? '' : 's'} queued for sync`
            : 'No internet — showing cached data'}
      </Text>
    </View>
  )
}

const styles = StyleSheet.create({
  banner: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.xs,
    backgroundColor: colors.warning,
    paddingVertical: 6,
    paddingHorizontal: spacing.md,
  },
  syncing: {
    backgroundColor: colors.primary,
  },
  text: {
    fontSize: fontSize.xs,
    color: '#fff',
    fontWeight: '600',
  },
})

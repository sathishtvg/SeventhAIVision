import React from 'react'
import { StyleSheet, Text, View } from 'react-native'
import { severity as severityColors, fontSize, radius } from '@/theme'

export function SeverityBadge({ value }: { value: string }) {
  const color = severityColors[value] ?? '#888'
  return (
    <View style={[styles.badge, { backgroundColor: color + '28', borderColor: color + '80' }]}>
      <Text style={[styles.text, { color }]}>{value.toUpperCase()}</Text>
    </View>
  )
}

const styles = StyleSheet.create({
  badge: {
    borderRadius: radius.sm,
    borderWidth: 1,
    paddingHorizontal: 8,
    paddingVertical: 3,
    alignSelf: 'flex-start',
  },
  text: {
    fontSize: fontSize.xs,
    fontWeight: '700',
    letterSpacing: 0.6,
  },
})

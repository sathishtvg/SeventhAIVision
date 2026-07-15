import React from 'react'
import { Platform, StyleSheet, View, type ViewProps } from 'react-native'
import { BlurView } from 'expo-blur'
import { colors, radius } from '@/theme'

interface GlassCardProps extends ViewProps {
  intensity?: number
  variant?: 'default' | 'elevated' | 'glow'
}

export function GlassCard({
  style,
  children,
  intensity = 20,
  variant = 'default',
  ...rest
}: GlassCardProps) {
  const borderColor =
    variant === 'glow'
      ? 'rgba(108,99,255,0.40)'
      : variant === 'elevated'
      ? 'rgba(255,255,255,0.22)'
      : colors.glassBorder

  const androidBg =
    variant === 'elevated'
      ? 'rgba(13,8,38,0.90)'
      : 'rgba(13,8,38,0.85)'

  return (
    <View style={[styles.wrapper, style]} {...rest}>
      {/* Blur on iOS, solid rgba on Android */}
      {Platform.OS === 'ios' ? (
        <BlurView intensity={intensity} tint="dark" style={StyleSheet.absoluteFill} />
      ) : (
        <View style={[StyleSheet.absoluteFill, { backgroundColor: androidBg }]} />
      )}
      {/* Glass border overlay */}
      <View
        style={[styles.borderOverlay, { borderColor }]}
        pointerEvents="none"
      />
      {/* Content renders on top */}
      {children}
    </View>
  )
}

const styles = StyleSheet.create({
  wrapper: {
    borderRadius: radius.xl,
    overflow: 'hidden',
  },
  borderOverlay: {
    ...StyleSheet.absoluteFillObject,
    borderWidth: 1,
    borderRadius: radius.xl,
  },
})

import React, { useEffect, useRef, useState } from 'react'
import {
  Alert, KeyboardAvoidingView, Platform, Pressable, StyleSheet,
  Text, TextInput, View, ActivityIndicator,
} from 'react-native'
import { LinearGradient } from 'expo-linear-gradient'
import { BlurView } from 'expo-blur'
import { Ionicons } from '@expo/vector-icons'
import * as SecureStore from 'expo-secure-store'
import { useAuthStore } from '@/store/auth'
import { useServerStore } from '@/store/server'
import { useBiometricStore } from '@/store/biometric'
import { setApiBaseUrl } from '@/api/client'
import { colors, fontSize, radius, spacing } from '@/theme'

const REFRESH_TOKEN_KEY = 'seventh_ai_refresh_token'

export function LoginScreen() {
  const login = useAuthStore((s) => s.login)
  const restoreSession = useAuthStore((s) => s.restoreSession)
  const { serverUrl, setServerUrl } = useServerStore()
  const { initialized, enabled: biometricEnabled, hardwareAvailable, biometricType, authenticate } = useBiometricStore()
  const [serverInput, setServerInput] = useState(serverUrl)
  const [tenantSlug, setTenantSlug] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [showPw, setShowPw] = useState(false)
  const [canBiometric, setCanBiometric] = useState(false)
  const [biometricLoading, setBiometricLoading] = useState(false)
  const autoPromptedRef = useRef(false)

  // Once biometric store is initialized, check whether quick sign-in is available
  useEffect(() => {
    if (!initialized) return
    if (!biometricEnabled || !hardwareAvailable) return
    SecureStore.getItemAsync(REFRESH_TOKEN_KEY).then((token) => {
      if (token) {
        setCanBiometric(true)
        if (!autoPromptedRef.current) {
          autoPromptedRef.current = true
          handleBiometric()
        }
      }
    })
  }, [initialized, biometricEnabled, hardwareAvailable])

  const handleBiometric = async () => {
    if (!serverUrl) return
    setBiometricLoading(true)
    try {
      setApiBaseUrl(serverUrl)
      const ok = await authenticate('Sign in to Seventh AI Vision')
      if (!ok) return  // User cancelled — fall through to password form
      await restoreSession()
      // If successful, accessToken is set and navigation handles the transition.
      // If refresh token was expired, accessToken stays null and we show an error.
      if (!useAuthStore.getState().accessToken) {
        setCanBiometric(false)
        Alert.alert('Session expired', 'Please sign in with your credentials.')
      }
    } catch {
      Alert.alert('Sign-in failed', 'Please enter your credentials manually.')
    } finally {
      setBiometricLoading(false)
    }
  }

  const handleLogin = async () => {
    if (!serverInput || !tenantSlug || !email || !password) {
      Alert.alert('Missing fields', 'Please fill in all fields.')
      return
    }
    setLoading(true)
    try {
      await setServerUrl(serverInput)
      setApiBaseUrl(serverInput)
      await login(email, password, tenantSlug)
    } catch {
      Alert.alert('Login failed', 'Check your server URL and credentials.')
    } finally {
      setLoading(false)
    }
  }

  return (
    <LinearGradient
      colors={['#020617', '#0D0826', '#020B17']}
      locations={[0, 0.5, 1]}
      style={styles.root}
    >
      {/* Ambient glow blobs */}
      <View style={styles.glowBlob1} />
      <View style={styles.glowBlob2} />

      <KeyboardAvoidingView
        style={styles.kav}
        behavior={Platform.OS === 'ios' ? 'padding' : undefined}
      >
        {/* Glass card */}
        <View style={styles.card}>
          {Platform.OS === 'ios' ? (
            <BlurView intensity={25} tint="dark" style={StyleSheet.absoluteFill} />
          ) : (
            <View style={[StyleSheet.absoluteFill, styles.androidCardBg]} />
          )}
          {/* Glass border */}
          <View style={styles.cardBorder} pointerEvents="none" />

          <View style={styles.cardContent}>
            {/* Logo + brand */}
            <View style={styles.logoRow}>
              <View style={styles.logoIconWrap}>
                <Ionicons name="shield-checkmark" size={26} color={colors.primary} />
              </View>
              <View>
                <Text style={styles.brandTitle}>Seventh AI Vision</Text>
                <Text style={styles.brandSub}>SECURITY OPERATIONS</Text>
              </View>
            </View>

            {/* Shimmer accent bar */}
            <LinearGradient
              colors={['#6C63FF', '#00D9C0', '#6C63FF']}
              start={{ x: 0, y: 0 }}
              end={{ x: 1, y: 0 }}
              style={styles.accentBar}
            />

            {/* Server URL */}
            <View style={styles.field}>
              <Text style={styles.label}>Server URL</Text>
              <View style={styles.inputRow}>
                <Ionicons name="server-outline" size={15} color={colors.textSecondary} style={styles.inputIcon} />
                <TextInput
                  style={styles.input}
                  placeholder="http://192.168.1.x:8000"
                  placeholderTextColor={colors.textDisabled}
                  value={serverInput}
                  onChangeText={setServerInput}
                  autoCapitalize="none"
                  autoCorrect={false}
                  keyboardType="url"
                />
              </View>
            </View>

            {/* Organization */}
            <View style={styles.field}>
              <Text style={styles.label}>Organization</Text>
              <View style={styles.inputRow}>
                <Ionicons name="business-outline" size={15} color={colors.textSecondary} style={styles.inputIcon} />
                <TextInput
                  style={styles.input}
                  placeholder="your-org-slug"
                  placeholderTextColor={colors.textDisabled}
                  value={tenantSlug}
                  onChangeText={setTenantSlug}
                  autoCapitalize="none"
                  autoCorrect={false}
                />
              </View>
            </View>

            {/* Email */}
            <View style={styles.field}>
              <Text style={styles.label}>Email</Text>
              <View style={styles.inputRow}>
                <Ionicons name="mail-outline" size={15} color={colors.textSecondary} style={styles.inputIcon} />
                <TextInput
                  style={styles.input}
                  placeholder="you@example.com"
                  placeholderTextColor={colors.textDisabled}
                  value={email}
                  onChangeText={setEmail}
                  keyboardType="email-address"
                  autoCapitalize="none"
                  autoCorrect={false}
                />
              </View>
            </View>

            {/* Password */}
            <View style={styles.field}>
              <Text style={styles.label}>Password</Text>
              <View style={styles.inputRow}>
                <Ionicons name="lock-closed-outline" size={15} color={colors.textSecondary} style={styles.inputIcon} />
                <TextInput
                  style={[styles.input, { flex: 1 }]}
                  placeholder="••••••••"
                  placeholderTextColor={colors.textDisabled}
                  value={password}
                  onChangeText={setPassword}
                  secureTextEntry={!showPw}
                />
                <Pressable onPress={() => setShowPw((v) => !v)} style={styles.eyeBtn}>
                  <Ionicons
                    name={showPw ? 'eye-off-outline' : 'eye-outline'}
                    size={16}
                    color={colors.textSecondary}
                  />
                </Pressable>
              </View>
            </View>

            {/* Sign In button */}
            <Pressable onPress={handleLogin} disabled={loading} style={[styles.btnWrap, loading && { opacity: 0.6 }]}>
              <LinearGradient
                colors={['#6C63FF', '#00D9C0']}
                start={{ x: 0, y: 0 }}
                end={{ x: 1, y: 0 }}
                style={styles.btn}
              >
                {loading
                  ? <ActivityIndicator color="#fff" size="small" />
                  : <Text style={styles.btnText}>Sign In</Text>
                }
              </LinearGradient>
            </Pressable>

            {/* Biometric quick sign-in */}
            {canBiometric && (
              <Pressable
                onPress={handleBiometric}
                disabled={biometricLoading}
                style={[styles.biometricBtn, biometricLoading && { opacity: 0.6 }]}
              >
                {biometricLoading ? (
                  <ActivityIndicator color={colors.primary} size="small" />
                ) : (
                  <>
                    <Ionicons
                      name={biometricType === 2 ? 'scan' : biometricType === 3 ? 'eye-outline' : 'finger-print'}
                      size={20}
                      color={colors.primary}
                    />
                    <Text style={styles.biometricText}>
                      {biometricType === 2
                        ? 'Sign in with Face ID'
                        : biometricType === 3
                        ? 'Sign in with Iris'
                        : 'Sign in with Fingerprint'}
                    </Text>
                  </>
                )}
              </Pressable>
            )}
          </View>
        </View>
      </KeyboardAvoidingView>
    </LinearGradient>
  )
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
  },
  kav: {
    width: '100%',
    alignItems: 'center',
    paddingHorizontal: spacing.lg,
  },
  // Ambient background glow blobs
  glowBlob1: {
    position: 'absolute',
    width: 300,
    height: 300,
    borderRadius: 150,
    backgroundColor: 'rgba(108,99,255,0.12)',
    top: -80,
    right: -60,
  },
  glowBlob2: {
    position: 'absolute',
    width: 240,
    height: 240,
    borderRadius: 120,
    backgroundColor: 'rgba(0,217,192,0.08)',
    bottom: -60,
    left: -80,
  },
  // Glass card
  card: {
    width: '100%',
    maxWidth: 420,
    borderRadius: radius.xl,
    overflow: 'hidden',
  },
  androidCardBg: {
    backgroundColor: 'rgba(13,8,38,0.92)',
  },
  cardBorder: {
    ...StyleSheet.absoluteFillObject,
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.18)',
    borderRadius: radius.xl,
  },
  cardContent: {
    padding: spacing.xl,
  },
  // Logo
  logoRow: {
    flexDirection: 'row',
    alignItems: 'center',
    gap: spacing.sm,
    marginBottom: spacing.md,
  },
  logoIconWrap: {
    width: 46,
    height: 46,
    borderRadius: 13,
    backgroundColor: colors.primaryMuted,
    alignItems: 'center',
    justifyContent: 'center',
    borderWidth: 1,
    borderColor: 'rgba(108,99,255,0.35)',
  },
  brandTitle: {
    fontSize: fontSize.xl,
    fontWeight: '700',
    color: colors.text,
    letterSpacing: -0.5,
  },
  brandSub: {
    fontSize: 10,
    color: colors.textSecondary,
    letterSpacing: 1.2,
    marginTop: 2,
  },
  // Accent bar
  accentBar: {
    height: 2,
    borderRadius: 1,
    marginBottom: spacing.lg,
    opacity: 0.8,
  },
  // Form
  field: {
    marginBottom: spacing.sm,
  },
  label: {
    fontSize: fontSize.xs,
    color: colors.textSecondary,
    marginBottom: 5,
    letterSpacing: 0.3,
  },
  inputRow: {
    flexDirection: 'row',
    alignItems: 'center',
    backgroundColor: 'rgba(255,255,255,0.06)',
    borderWidth: 1,
    borderColor: 'rgba(255,255,255,0.12)',
    borderRadius: radius.md,
    paddingHorizontal: spacing.md,
    minHeight: 46,
  },
  inputIcon: {
    marginRight: spacing.sm,
  },
  input: {
    flex: 1,
    color: colors.text,
    fontSize: fontSize.md,
    paddingVertical: spacing.sm,
  },
  eyeBtn: {
    paddingLeft: spacing.sm,
    paddingVertical: spacing.xs,
  },
  // Button
  btnWrap: {
    marginTop: spacing.md,
  },
  btn: {
    borderRadius: radius.md,
    paddingVertical: 14,
    alignItems: 'center',
    justifyContent: 'center',
  },
  btnText: {
    color: '#fff',
    fontWeight: '700',
    fontSize: fontSize.md,
    letterSpacing: 0.5,
  },
  // Biometric quick sign-in
  biometricBtn: {
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: spacing.sm,
    marginTop: spacing.sm,
    paddingVertical: 12,
    borderRadius: radius.md,
    borderWidth: 1,
    borderColor: colors.primary,
    backgroundColor: colors.primaryMuted,
    minHeight: 46,
  },
  biometricText: {
    color: colors.primary,
    fontSize: fontSize.md,
    fontWeight: '600',
  },
})

import React, { useState } from 'react'
import {
  ActivityIndicator, Alert, Image, ScrollView, StyleSheet, Switch,
  Text, TextInput, TouchableOpacity, View,
} from 'react-native'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Ionicons } from '@expo/vector-icons'
import { getSettings, upsertSetting, SETTING_KEYS, type SettingKey, type TenantSetting } from '@/api/settings'
import { get2FAStatus, setup2FA, enable2FA, disable2FA } from '@/api/twofa'
import { useBiometricStore } from '@/store/biometric'
import { Card } from '@/components/Card'
import { colors, fontSize, spacing, radius } from '@/theme'

const SETTING_META: Record<SettingKey, { label: string; description: string; unit: string; min: number; max: number }> = {
  'lpr.confidence_threshold': {
    label: 'LPR Confidence Threshold',
    description: 'Minimum plate detection confidence to create an event.',
    unit: '0–1',
    min: 0,
    max: 1,
  },
  'face.match_threshold': {
    label: 'Face Match Threshold',
    description: 'Minimum cosine similarity to count as a watchlist match.',
    unit: '0–1',
    min: 0,
    max: 1,
  },
  'intrusion.breach_cooldown_seconds': {
    label: 'Intrusion Cooldown',
    description: 'Seconds to suppress repeat alerts for the same zone breach.',
    unit: 'seconds',
    min: 0,
    max: 3600,
  },
  'evidence.retention_days': {
    label: 'Evidence Retention',
    description: 'Days to keep evidence files before automated cleanup.',
    unit: 'days',
    min: 1,
    max: 3650,
  },
}

function SettingRow({ settingKey, currentValue }: { settingKey: SettingKey; currentValue: number }) {
  const qc = useQueryClient()
  const meta = SETTING_META[settingKey]
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState(String(currentValue))

  const mutation = useMutation({
    mutationFn: (val: number) => upsertSetting(settingKey, val),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['settings'] })
      setEditing(false)
    },
    onError: () => Alert.alert('Error', 'Failed to save setting. Please try again.'),
  })

  const save = () => {
    const n = parseFloat(draft)
    if (isNaN(n) || n < meta.min || n > meta.max) {
      Alert.alert('Invalid value', `Must be between ${meta.min} and ${meta.max}.`)
      return
    }
    mutation.mutate(n)
  }

  return (
    <Card style={styles.settingCard}>
      <View style={styles.settingHeader}>
        <View style={styles.settingInfo}>
          <Text style={styles.settingLabel}>{meta.label}</Text>
          <Text style={styles.settingDesc}>{meta.description}</Text>
        </View>
        <TouchableOpacity
          style={styles.editBtn}
          onPress={() => { setDraft(String(currentValue)); setEditing(!editing) }}
        >
          <Text style={styles.editBtnText}>{editing ? 'Cancel' : 'Edit'}</Text>
        </TouchableOpacity>
      </View>

      {editing ? (
        <View style={styles.editRow}>
          <TextInput
            style={styles.input}
            value={draft}
            onChangeText={setDraft}
            keyboardType="numeric"
            selectTextOnFocus
            placeholderTextColor={colors.textDisabled}
          />
          <Text style={styles.unit}>{meta.unit}</Text>
          <TouchableOpacity
            style={[styles.saveBtn, mutation.isPending && styles.saveBtnDisabled]}
            onPress={save}
            disabled={mutation.isPending}
          >
            {mutation.isPending
              ? <ActivityIndicator size="small" color="#fff" />
              : <Text style={styles.saveBtnText}>Save</Text>}
          </TouchableOpacity>
        </View>
      ) : (
        <View style={styles.valueRow}>
          <Text style={styles.currentValue}>{currentValue}</Text>
          <Text style={styles.unit}>{meta.unit}</Text>
        </View>
      )}
    </Card>
  )
}

function TwoFASection() {
  const qc = useQueryClient()
  const [totpCode, setTotpCode] = useState('')
  const [showSetup, setShowSetup] = useState(false)

  const { data: statusData, isLoading: statusLoading } = useQuery({
    queryKey: ['2fa-status'],
    queryFn: get2FAStatus,
  })

  const { data: setupData, isLoading: setupLoading, refetch: fetchSetup } = useQuery({
    queryKey: ['2fa-setup'],
    queryFn: setup2FA,
    enabled: false,
  })

  const { mutate: doEnable, isPending: enabling } = useMutation({
    mutationFn: () => enable2FA(totpCode.trim()),
    onSuccess: () => {
      Alert.alert('2FA Enabled', 'Two-factor authentication is now active.')
      setTotpCode('')
      setShowSetup(false)
      qc.invalidateQueries({ queryKey: ['2fa-status'] })
      qc.invalidateQueries({ queryKey: ['2fa-setup'] })
    },
    onError: (e: any) => {
      const msg = e?.response?.data?.detail ?? 'Invalid code. Please try again.'
      Alert.alert('Error', msg)
    },
  })

  const { mutate: doDisable, isPending: disabling } = useMutation({
    mutationFn: () => disable2FA(totpCode.trim()),
    onSuccess: () => {
      Alert.alert('2FA Disabled', 'Two-factor authentication has been removed.')
      setTotpCode('')
      qc.invalidateQueries({ queryKey: ['2fa-status'] })
    },
    onError: (e: any) => {
      const msg = e?.response?.data?.detail ?? 'Invalid code. Please try again.'
      Alert.alert('Error', msg)
    },
  })

  const handleStartSetup = async () => {
    await fetchSetup()
    setShowSetup(true)
  }

  const handleDisable = () => {
    if (!totpCode.trim()) {
      Alert.alert('Code required', 'Enter your current TOTP code to disable 2FA.')
      return
    }
    Alert.alert(
      'Disable 2FA',
      'Are you sure you want to remove two-factor authentication from your account?',
      [
        { text: 'Cancel', style: 'cancel' },
        { text: 'Disable', style: 'destructive', onPress: () => doDisable() },
      ],
    )
  }

  if (statusLoading) {
    return (
      <Card style={styles.settingCard}>
        <ActivityIndicator color={colors.primary} />
      </Card>
    )
  }

  const is2FAEnabled = statusData?.enabled ?? false

  return (
    <Card style={styles.twoFACard}>
      <View style={styles.twoFAHeader}>
        <Ionicons
          name={is2FAEnabled ? 'shield-checkmark' : 'shield-outline'}
          size={24}
          color={is2FAEnabled ? colors.success : colors.textSecondary}
        />
        <View style={styles.twoFAInfo}>
          <Text style={styles.settingLabel}>Two-Factor Authentication</Text>
          <Text style={[styles.twoFAStatus, { color: is2FAEnabled ? colors.success : colors.textSecondary }]}>
            {is2FAEnabled ? 'Enabled' : 'Disabled'}
          </Text>
        </View>
      </View>

      <Text style={styles.settingDesc}>
        Adds an extra layer of security. You'll need an authenticator app (Google Authenticator, Authy) each time you log in.
      </Text>

      {!is2FAEnabled && !showSetup && (
        <TouchableOpacity style={[styles.saveBtn, { marginTop: spacing.md }]} onPress={handleStartSetup}>
          {setupLoading
            ? <ActivityIndicator size="small" color="#fff" />
            : <Text style={styles.saveBtnText}>Enable 2FA</Text>}
        </TouchableOpacity>
      )}

      {!is2FAEnabled && showSetup && setupData && (
        <View style={styles.setupSection}>
          <Text style={styles.setupStep}>1. Scan this QR code with your authenticator app</Text>
          <Image
            source={{ uri: setupData.qr_code_uri }}
            style={styles.qrCode}
            resizeMode="contain"
          />
          <Text style={styles.setupStep}>
            Or enter this key manually:{'\n'}
            <Text style={styles.manualKey}>{setupData.manual_entry_key}</Text>
          </Text>
          <Text style={styles.setupStep}>2. Enter the 6-digit code from your app</Text>
          <TextInput
            style={styles.totpInput}
            value={totpCode}
            onChangeText={setTotpCode}
            placeholder="000000"
            placeholderTextColor={colors.textDisabled}
            keyboardType="number-pad"
            maxLength={6}
          />
          <View style={styles.setupBtns}>
            <TouchableOpacity
              style={[styles.cancelBtn]}
              onPress={() => { setShowSetup(false); setTotpCode('') }}
            >
              <Text style={styles.cancelBtnText}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              style={[styles.saveBtn, { flex: 1 }, (enabling || totpCode.length < 6) && styles.saveBtnDisabled]}
              onPress={() => doEnable()}
              disabled={enabling || totpCode.length < 6}
            >
              {enabling
                ? <ActivityIndicator size="small" color="#fff" />
                : <Text style={styles.saveBtnText}>Verify & Enable</Text>}
            </TouchableOpacity>
          </View>
        </View>
      )}

      {is2FAEnabled && (
        <View style={styles.setupSection}>
          <Text style={styles.settingDesc}>Enter your current TOTP code to disable:</Text>
          <TextInput
            style={styles.totpInput}
            value={totpCode}
            onChangeText={setTotpCode}
            placeholder="000000"
            placeholderTextColor={colors.textDisabled}
            keyboardType="number-pad"
            maxLength={6}
          />
          <TouchableOpacity
            style={[styles.disableBtn, (disabling || totpCode.length < 6) && styles.saveBtnDisabled]}
            onPress={handleDisable}
            disabled={disabling || totpCode.length < 6}
          >
            {disabling
              ? <ActivityIndicator size="small" color="#fff" />
              : <Text style={styles.saveBtnText}>Disable 2FA</Text>}
          </TouchableOpacity>
        </View>
      )}
    </Card>
  )
}

function BiometricSection() {
  const { hardwareAvailable, enabled, biometricType, enable, disable, authenticate } = useBiometricStore()
  const [toggling, setToggling] = useState(false)

  if (!hardwareAvailable) return null

  const typeLabel = biometricType === 2 ? 'Face ID' : biometricType === 3 ? 'Iris' : 'Fingerprint'
  const iconName: React.ComponentProps<typeof Ionicons>['name'] =
    biometricType === 2 ? 'scan' : biometricType === 3 ? 'eye-outline' : 'finger-print'

  const handleToggle = async (value: boolean) => {
    if (toggling) return
    setToggling(true)
    try {
      if (value) {
        const ok = await authenticate(`Confirm your ${typeLabel} to enable quick sign-in`)
        if (ok) {
          await enable()
          Alert.alert(`${typeLabel} enabled`, `You can now sign in using ${typeLabel}.`)
        }
      } else {
        await disable()
      }
    } finally {
      setToggling(false)
    }
  }

  return (
    <Card style={styles.settingCard}>
      <View style={styles.bioRow}>
        <Ionicons name={iconName} size={22} color={enabled ? colors.primary : colors.textSecondary} />
        <View style={styles.bioInfo}>
          <Text style={styles.settingLabel}>{typeLabel} Authentication</Text>
          <Text style={styles.settingDesc}>
            Sign in quickly using your device {typeLabel.toLowerCase()} instead of entering your password.
          </Text>
        </View>
        {toggling ? (
          <ActivityIndicator size="small" color={colors.primary} />
        ) : (
          <Switch
            value={enabled}
            onValueChange={handleToggle}
            trackColor={{ false: colors.surface, true: colors.primaryMuted }}
            thumbColor={enabled ? colors.primary : colors.textSecondary}
          />
        )}
      </View>
    </Card>
  )
}

export function SettingsScreen() {
  const { data, isLoading } = useQuery({
    queryKey: ['settings'],
    queryFn: getSettings,
  })

  if (isLoading) {
    return (
      <View style={styles.center}>
        <ActivityIndicator color={colors.primary} />
      </View>
    )
  }

  const valueMap = Object.fromEntries((data ?? []).map((s: TenantSetting) => [s.setting_key, s.setting_value]))

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>
      <Text style={styles.sectionTitle}>AI Detection Thresholds</Text>
      {SETTING_KEYS.map((key) => (
        <SettingRow
          key={key}
          settingKey={key}
          currentValue={valueMap[key] ?? 0}
        />
      ))}

      <Text style={[styles.sectionTitle, { marginTop: spacing.lg }]}>Account Security</Text>
      <BiometricSection />
      <TwoFASection />
    </ScrollView>
  )
}

const styles = StyleSheet.create({
  root:           { flex: 1, backgroundColor: colors.background },
  center:         { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: colors.background },
  content:        { padding: spacing.md, paddingBottom: spacing.xl },
  sectionTitle:   { fontSize: fontSize.lg, fontWeight: '700', color: colors.text, marginBottom: spacing.md },
  settingCard:    { marginBottom: spacing.sm },
  settingHeader:  { flexDirection: 'row', alignItems: 'flex-start', gap: spacing.sm },
  settingInfo:    { flex: 1 },
  settingLabel:   { fontSize: fontSize.md, fontWeight: '700', color: colors.text, marginBottom: 3 },
  settingDesc:    { fontSize: fontSize.xs, color: colors.textSecondary, lineHeight: 16, marginTop: spacing.xs },
  editBtn:        { paddingHorizontal: spacing.sm, paddingVertical: 4, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.primary },
  editBtnText:    { fontSize: fontSize.xs, fontWeight: '600', color: colors.primary },
  valueRow:       { flexDirection: 'row', alignItems: 'baseline', gap: spacing.xs, marginTop: spacing.sm },
  currentValue:   { fontSize: fontSize.xl, fontWeight: '800', color: colors.primary },
  unit:           { fontSize: fontSize.sm, color: colors.textSecondary },
  editRow:        { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginTop: spacing.sm },
  input: {
    flex: 1,
    height: 40,
    borderRadius: radius.sm,
    borderWidth: 1,
    borderColor: colors.primary,
    backgroundColor: colors.surface,
    color: colors.text,
    paddingHorizontal: spacing.sm,
    fontSize: fontSize.md,
    fontWeight: '700',
  },
  saveBtn:         { backgroundColor: colors.primary, paddingHorizontal: spacing.md, paddingVertical: 10, borderRadius: radius.sm, minWidth: 60, alignItems: 'center' },
  saveBtnDisabled: { opacity: 0.5 },
  saveBtnText:     { color: '#fff', fontWeight: '700', fontSize: fontSize.sm },
  // Biometric section
  bioRow:  { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
  bioInfo: { flex: 1 },
  // 2FA styles
  twoFACard:    { marginBottom: spacing.sm },
  twoFAHeader:  { flexDirection: 'row', alignItems: 'center', gap: spacing.sm, marginBottom: spacing.xs },
  twoFAInfo:    { flex: 1 },
  twoFAStatus:  { fontSize: fontSize.xs, fontWeight: '600', marginTop: 2 },
  setupSection: { marginTop: spacing.md, gap: spacing.sm },
  setupStep:    { fontSize: fontSize.sm, color: colors.text, lineHeight: 20 },
  manualKey:    { fontFamily: 'Courier', fontWeight: '700', color: colors.primary, letterSpacing: 1 },
  qrCode:       { width: 200, height: 200, alignSelf: 'center', marginVertical: spacing.sm, backgroundColor: '#fff' },
  totpInput:    {
    height: 52,
    borderRadius: radius.sm,
    borderWidth: 2,
    borderColor: colors.primary,
    backgroundColor: colors.surface,
    color: colors.text,
    paddingHorizontal: spacing.md,
    fontSize: 28,
    fontWeight: '700',
    letterSpacing: 8,
    textAlign: 'center',
  },
  setupBtns:  { flexDirection: 'row', gap: spacing.sm },
  cancelBtn:  { paddingHorizontal: spacing.md, paddingVertical: 10, borderRadius: radius.sm, borderWidth: 1, borderColor: colors.cardBorder, alignItems: 'center' },
  cancelBtnText: { color: colors.textSecondary, fontWeight: '600', fontSize: fontSize.sm },
  disableBtn: { backgroundColor: '#E53935', paddingHorizontal: spacing.md, paddingVertical: 10, borderRadius: radius.sm, alignItems: 'center' },
})

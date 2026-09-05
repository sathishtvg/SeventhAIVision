import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import { Box, Typography, Grid, Stack, Slider, Button, Alert, Skeleton, TextField, Chip, Divider, List, ListItem, ListItemText, ListItemSecondaryAction, IconButton, Tooltip, Switch, FormControlLabel, MenuItem, Select, InputLabel, FormControl, Dialog, DialogTitle, DialogContent, DialogActions, InputAdornment } from '@mui/material'
import LogoutIcon from '@mui/icons-material/Logout'
import DeleteSweepIcon from '@mui/icons-material/DeleteSweep'
import DeleteIcon from '@mui/icons-material/Delete'
import AddIcon from '@mui/icons-material/Add'
import PaletteIcon from '@mui/icons-material/Palette'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { useColorMode, BRAND_PRESETS } from '@/context/ColorMode'
import { getSettings, upsertSetting } from '@/api/settings'
import { getBranding, updateBranding } from '@/api/branding'
import { get2FAStatus, setup2FA, enable2FA, disable2FA, get2faPolicy, set2faPolicy } from '@/api/guards'
import { getMySessions, revokeMySession, revokeAllMySessions } from '@/api/sessions'
import { listDedupRules, createDedupRule, deleteDedupRule, type DedupRuleBody } from '@/api/alertDedup'
import { getCameras } from '@/api/cameras'
import { PageHeader } from '@/components/common/PageHeader'
import { PAGE_LABELS, PAGE_LABELS_SETTING, usePageLabelOverrides, type PageKey } from '@/hooks/usePageLabels'
import type { PageLabelOverrides } from '@/lib/pageLabels'

interface SettingKnobProps {
  label: string
  description: string
  settingKey: string
  min: number
  max: number
  step: number
  currentValue: number | undefined
  isLoading: boolean
  onSave: (key: string, value: number) => void
  isSaving: boolean
  formatValue?: (v: number) => string
}

function SettingKnob({ label, description, settingKey, min, max, step, currentValue, isLoading, onSave, isSaving, formatValue }: SettingKnobProps) {
  const [local, setLocal] = useState<number | null>(null)
  const displayed = local ?? currentValue ?? min

  useEffect(() => {
    if (currentValue != null) setLocal(null)
  }, [currentValue])

  return (
    <GlassCard sx={{ p: 3 }}>
      <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>{label}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>{description}</Typography>
      {isLoading ? (
        <Skeleton height={40} />
      ) : (
        <Box sx={{ px: 1 }}>
          <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 1 }}>
            <Typography variant="caption" color="text.secondary">{min}</Typography>
            <Typography variant="body1" sx={{ fontWeight: 700, color: 'primary.main' }}>
              {formatValue ? formatValue(displayed) : displayed}
            </Typography>
            <Typography variant="caption" color="text.secondary">{max}</Typography>
          </Box>
          <PermissionGuard permission="settings:write">
            <Slider
              value={displayed}
              min={min}
              max={max}
              step={step}
              onChange={(_, v) => setLocal(v as number)}
              disabled={isSaving}
            />
            <Box sx={{ display: 'flex', justifyContent: 'flex-end', mt: 1 }}>
              <Button
                variant="contained"
                size="small"
                disabled={local === null || isSaving}
                onClick={() => onSave(settingKey, local!)}
              >
                Save
              </Button>
            </Box>
          </PermissionGuard>
        </Box>
      )}
    </GlassCard>
  )
}

const SETTING_SECTIONS = [
  {
    title: 'Core AI Tuning',
    defs: [
      {
        label: 'LPR Confidence Threshold',
        description: 'Minimum plate-detection confidence to record a detection.',
        key: 'lpr.confidence_threshold',
        min: 0, max: 1, step: 0.01,
        formatValue: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      {
        label: 'Face Match Threshold',
        description: 'Minimum cosine similarity to count as a watchlist match.',
        key: 'face.match_threshold',
        min: 0, max: 1, step: 0.01,
        formatValue: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      {
        label: 'Intrusion Breach Cooldown',
        description: 'Seconds between repeated alerts for the same camera + zone.',
        key: 'intrusion.breach_cooldown_seconds',
        min: 0, max: 600, step: 5,
        formatValue: (v: number) => `${v}s`,
      },
      {
        label: 'Evidence Retention Days',
        description: 'How many days to keep evidence files before purging.',
        key: 'evidence.retention_days',
        min: 1, max: 730, step: 1,
        formatValue: (v: number) => `${v} days`,
      },
    ],
  },
  {
    title: 'Phase 3 AI Modules',
    defs: [
      {
        label: 'PPE Confidence Threshold',
        description: 'Minimum confidence to record a PPE violation detection.',
        key: 'ppe.confidence_threshold',
        min: 0, max: 1, step: 0.01,
        formatValue: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      {
        label: 'Crowd Alert Threshold',
        description: 'Occupancy ratio above which a crowd density alert fires.',
        key: 'crowd.alert_threshold_ratio',
        min: 0, max: 1, step: 0.01,
        formatValue: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      {
        label: 'Crowd Breach Cooldown',
        description: 'Seconds between repeated crowd density alerts.',
        key: 'crowd.breach_cooldown_seconds',
        min: 0, max: 600, step: 5,
        formatValue: (v: number) => `${v}s`,
      },
      {
        label: 'Fire/Smoke Confidence Threshold',
        description: 'Minimum confidence to record a fire or smoke detection.',
        key: 'fire_smoke.confidence_threshold',
        min: 0, max: 1, step: 0.01,
        formatValue: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      {
        label: 'Weapon Confidence Threshold',
        description: 'Minimum confidence to record a weapon detection.',
        key: 'weapon.confidence_threshold',
        min: 0, max: 1, step: 0.01,
        formatValue: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      {
        label: 'Behavior Loitering Dwell',
        description: 'Seconds a person must remain in a zone before a loitering alert fires.',
        key: 'behavior.loitering_dwell_seconds',
        min: 5, max: 300, step: 5,
        formatValue: (v: number) => `${v}s`,
      },
      {
        label: 'Behavior Breach Cooldown',
        description: 'Seconds between repeated behavior alerts for the same camera + zone.',
        key: 'behavior.breach_cooldown_seconds',
        min: 0, max: 600, step: 5,
        formatValue: (v: number) => `${v}s`,
      },
    ],
  },
  {
    title: 'Phase 5 Safety Modules',
    defs: [
      {
        label: 'Tampering Score Threshold',
        description: 'Minimum tampering score to trigger a camera tampering alert.',
        key: 'tampering.score_threshold',
        min: 0, max: 1, step: 0.01,
        formatValue: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
      {
        label: 'Abandoned Object Dwell',
        description: 'Seconds an object must be stationary before an abandoned object alert fires.',
        key: 'abandoned.dwell_seconds',
        min: 5, max: 300, step: 5,
        formatValue: (v: number) => `${v}s`,
      },
      {
        label: 'Fall Detection Confidence',
        description: 'Minimum confidence to record a slip/fall detection.',
        key: 'fall.confidence_threshold',
        min: 0, max: 1, step: 0.01,
        formatValue: (v: number) => `${(v * 100).toFixed(0)}%`,
      },
    ],
  },
]

function BrandingSection() {
  const qc = useQueryClient()
  const [logoUrl, setLogoUrl] = useState('')
  const [primaryColor, setPrimaryColor] = useState('#6C63FF')
  const [companyName, setCompanyName] = useState('')
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  const [dirty, setDirty] = useState(false)

  const { data, isLoading } = useQuery({ queryKey: ['branding'], queryFn: getBranding })

  useEffect(() => {
    if (!data) return
    setLogoUrl(data.branding.logo_url ?? '')
    setPrimaryColor(data.branding.primary_color ?? '#6C63FF')
    setCompanyName(data.branding.company_name ?? '')
    setDirty(false)
  }, [data])

  const { mutate: save, isPending } = useMutation({
    mutationFn: () => updateBranding({
      ...(logoUrl       ? { logo_url:       logoUrl }       : {}),
      ...(primaryColor  ? { primary_color:  primaryColor }  : {}),
      ...(companyName   ? { company_name:   companyName }   : {}),
    }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['branding'] })
      setDirty(false)
      setMsg({ type: 'success', text: 'Branding saved.' })
      setTimeout(() => setMsg(null), 3000)
    },
    onError: () => setMsg({ type: 'error', text: 'Failed to save branding.' }),
  })

  const isValidHex = /^#[0-9A-Fa-f]{3,8}$/.test(primaryColor)

  return (
    <GlassCard sx={{ p: 3, maxWidth: 640 }}>
      {msg && (
        <Alert severity={msg.type} sx={{ mb: 2 }} onClose={() => setMsg(null)}>{msg.text}</Alert>
      )}
      <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
        Customise how your organisation appears within the platform. The logo and colours are
        applied to the sidebar, login page, and subdomain experience.
      </Typography>

      {isLoading ? (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
          <Skeleton height={56} />
          <Skeleton height={56} />
          <Skeleton height={56} />
        </Box>
      ) : (
        <Box sx={{ display: 'flex', flexDirection: 'column', gap: 2.5 }}>
          {/* Company display name */}
          <PermissionGuard permission="settings:write">
            <TextField
              label="Company display name"
              value={companyName}
              onChange={(e) => { setCompanyName(e.target.value); setDirty(true) }}
              fullWidth
              size="small"
              placeholder={data?.name ?? 'My Company'}
              helperText="Shown in the sidebar and login page header. Leave blank to use your account name."
            />
          </PermissionGuard>

          {/* Logo URL */}
          <PermissionGuard permission="settings:write">
            <TextField
              label="Logo URL"
              value={logoUrl}
              onChange={(e) => { setLogoUrl(e.target.value); setDirty(true) }}
              fullWidth
              size="small"
              placeholder="https://example.com/logo.png"
              helperText="Hosted image URL (PNG/SVG, 1:1 ratio recommended). Leave blank to use the default shield icon."
              slotProps={{
                input: {
                  endAdornment: logoUrl ? (
                    <InputAdornment position="end">
                      <Box
                        component="img"
                        src={logoUrl}
                        alt=""
                        sx={{ width: 24, height: 24, objectFit: 'contain', borderRadius: '4px', opacity: 0.85 }}
                        onError={(e: any) => { e.target.style.display = 'none' }}
                      />
                    </InputAdornment>
                  ) : undefined,
                },
              }}
            />
          </PermissionGuard>

          {/* Primary colour */}
          <PermissionGuard permission="settings:write">
            <TextField
              label="Primary colour"
              value={primaryColor}
              onChange={(e) => { setPrimaryColor(e.target.value); setDirty(true) }}
              fullWidth
              size="small"
              error={!isValidHex}
              helperText={isValidHex ? 'Hex colour used for accent, buttons, and nav highlight.' : 'Enter a valid hex colour, e.g. #6C63FF'}
              slotProps={{
                input: {
                  startAdornment: (
                    <InputAdornment position="start">
                      <Box
                        sx={{
                          width: 18,
                          height: 18,
                          borderRadius: '4px',
                          background: isValidHex ? primaryColor : 'transparent',
                          border: '1px solid rgba(255,255,255,0.25)',
                          flexShrink: 0,
                          transition: 'background 0.2s',
                        }}
                      />
                    </InputAdornment>
                  ),
                  endAdornment: (
                    <InputAdornment position="end">
                      <Tooltip title="Open colour picker">
                        <Box
                          component="input"
                          type="color"
                          value={isValidHex ? primaryColor : '#6C63FF'}
                          onChange={(e: any) => { setPrimaryColor(e.target.value); setDirty(true) }}
                          sx={{
                            width: 24,
                            height: 24,
                            border: 'none',
                            borderRadius: '4px',
                            padding: 0,
                            cursor: 'pointer',
                            background: 'transparent',
                            '&::-webkit-color-swatch-wrapper': { padding: 0 },
                            '&::-webkit-color-swatch': { border: 'none', borderRadius: '4px' },
                          }}
                        />
                      </Tooltip>
                    </InputAdornment>
                  ),
                },
              }}
            />
          </PermissionGuard>

          {/* Preview strip */}
          {isValidHex && (
            <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, p: 1.5, borderRadius: '10px', background: 'rgba(255,255,255,0.04)', border: '1px solid rgba(255,255,255,0.08)' }}>
              <Box sx={{ width: 28, height: 28, borderRadius: '8px', background: `linear-gradient(135deg, ${primaryColor} 0%, #00D9C0 100%)`, display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0 }}>
                <PaletteIcon sx={{ fontSize: 14, color: '#fff' }} />
              </Box>
              <Typography variant="caption" color="text.secondary">
                Accent colour preview &nbsp;
                <Box component="span" sx={{ fontFamily: 'monospace', color: primaryColor }}>{primaryColor}</Box>
              </Typography>
              <Box sx={{ ml: 'auto', px: 1.5, py: 0.4, borderRadius: '6px', background: primaryColor, fontSize: '0.72rem', fontWeight: 700, color: '#fff', boxShadow: `0 2px 8px ${primaryColor}66` }}>
                Button
              </Box>
            </Box>
          )}

          <PermissionGuard permission="settings:write">
            <Box sx={{ display: 'flex', justifyContent: 'flex-end' }}>
              <Button
                variant="contained"
                disabled={!dirty || isPending || !isValidHex}
                onClick={() => save()}
                size="small"
              >
                {isPending ? 'Saving…' : 'Save Branding'}
              </Button>
            </Box>
          </PermissionGuard>
        </Box>
      )}
    </GlassCard>
  )
}

export default function Settings() {
  const queryClient = useQueryClient()
  const [saved, setSaved] = useState<string | null>(null)

  const { data: settings, isLoading } = useQuery({ queryKey: ['settings'], queryFn: getSettings })

  const { mutate: save, isPending } = useMutation({
    mutationFn: ({ key, value }: { key: string; value: number }) => upsertSetting(key, value),
    onSuccess: (_, vars) => {
      queryClient.invalidateQueries({ queryKey: ['settings'] })
      setSaved(vars.key)
      setTimeout(() => setSaved(null), 3000)
    },
  })

  const getValue = (key: string) => settings?.find((s) => s.setting_key === key)?.setting_value

  return (
    <Box>
      <PageHeader pageKey="settings" />
      {/* ── My appearance ─────────────────────────────────────────────────
          Deliberately first, and deliberately separate from Tenant Branding
          below: branding is what every user of this tenant sees, this is
          only what *you* see. Keeping them adjacent but clearly labelled is
          what stops an admin changing their own accent and assuming they
          just rebranded the company. */}
      <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 2, textTransform: 'uppercase', letterSpacing: '0.08em', fontSize: '0.7rem' }}>
        My Appearance
      </Typography>
      <AppearanceSection />

      <Divider sx={{ my: 4, borderColor: 'rgba(255,255,255,0.08)' }} />

      {/* ── Branding ──────────────────────────────────────────────────────── */}
      <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 2, textTransform: 'uppercase', letterSpacing: '0.08em', fontSize: '0.7rem' }}>
        Tenant Branding
      </Typography>
      <BrandingSection />

      <Divider sx={{ my: 4, borderColor: 'rgba(255,255,255,0.08)' }} />

      {/* ── Page names ────────────────────────────────────────────────────── */}
      <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 2, textTransform: 'uppercase', letterSpacing: '0.08em', fontSize: '0.7rem' }}>
        Page Names
      </Typography>
      <PageLabelsSection />

      <Divider sx={{ my: 4, borderColor: 'rgba(255,255,255,0.08)' }} />

      {saved && (
        <Alert severity="success" sx={{ mb: 3 }}>
          Setting saved: <strong>{saved}</strong>
        </Alert>
      )}
      {SETTING_SECTIONS.map((section, si) => (
        <Box key={section.title}>
          {si > 0 && <Divider sx={{ my: 3, borderColor: 'rgba(255,255,255,0.08)' }} />}
          <Typography variant="subtitle2" color="text.secondary" sx={{ mb: 2, textTransform: 'uppercase', letterSpacing: '0.08em', fontSize: '0.7rem' }}>
            {section.title}
          </Typography>
          <Grid container spacing={3}>
            {section.defs.map((def) => (
              <Grid size={{ xs: 12, sm: 6 }} key={def.key}>
                <SettingKnob
                  label={def.label}
                  description={def.description}
                  settingKey={def.key}
                  min={def.min}
                  max={def.max}
                  step={def.step}
                  currentValue={getValue(def.key)}
                  isLoading={isLoading}
                  onSave={(key, value) => save({ key, value })}
                  isSaving={isPending}
                  formatValue={def.formatValue}
                />
              </Grid>
            ))}
          </Grid>
        </Box>
      ))}

      <Divider sx={{ my: 4, borderColor: 'rgba(255,255,255,0.08)' }} />
      <Typography variant="h6" sx={{ mb: 2, fontWeight: 700 }}>Active Sessions</Typography>
      <SessionsSection />

      <Divider sx={{ my: 4, borderColor: 'rgba(255,255,255,0.08)' }} />
      <Typography variant="h6" sx={{ mb: 2, fontWeight: 700 }}>Two-Factor Authentication</Typography>
      <TwoFASection />

      <PermissionGuard permission="2fa:policy">
        <Divider sx={{ my: 4, borderColor: 'rgba(255,255,255,0.08)' }} />
        <Typography variant="h6" sx={{ mb: 2, fontWeight: 700 }}>Tenant 2FA Enforcement Policy</Typography>
        <TwoFAPolicySection />
      </PermissionGuard>

      <PermissionGuard permission="alert:dedup:manage">
        <Divider sx={{ my: 4, borderColor: 'rgba(255,255,255,0.08)' }} />
        <Typography variant="h6" sx={{ mb: 2, fontWeight: 700 }}>Alert Deduplication Rules</Typography>
        <AlertDedupRulesSection />
      </PermissionGuard>

      <AdvancedLinksSection />
    </Box>
  )
}

/** Setup-once plumbing that used to occupy permanent slots in a 53-item
 *  sidebar an operator reads every shift. The pages are unchanged and the
 *  routes still work — they just live here now, where you go when you are
 *  actually configuring the system rather than running it.
 *
 *  Each link is permission-gated exactly as its old nav entry was, so nobody
 *  gains or loses access by the move. */
/**
 * Per-user theme choice. Stored against the signed-in user, so on a shared
 * control-room PC one operator's accent does not follow the next person who
 * signs in — everyone else keeps the usual colours until they pick their own.
 */
function AppearanceSection() {
  const { mode, toggle, brandColor, setBrandColor, reset } = useColorMode()

  return (
    <GlassCard sx={{ p: 3, maxWidth: 640 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2.5 }}>
        These settings apply to your account only, on this device. They do not
        change what anyone else sees.
      </Typography>

      <FormControlLabel
        control={<Switch checked={mode === 'light'} onChange={toggle} />}
        label={mode === 'light' ? 'Light mode' : 'Dark mode'}
        sx={{ mb: 3 }}
      />

      <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
        Accent colour
      </Typography>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1, mb: 2 }}>
        {BRAND_PRESETS.map((preset) => (
          <Tooltip key={preset.hex} title={preset.name}>
            <Box
              component="button"
              aria-label={preset.name}
              onClick={() => setBrandColor(preset.hex)}
              sx={{
                width: 34, height: 34, borderRadius: '50%', cursor: 'pointer',
                background: preset.hex, padding: 0,
                border: brandColor.toLowerCase() === preset.hex.toLowerCase()
                  ? '3px solid currentColor' : '2px solid rgba(128,128,128,0.35)',
                color: 'text.primary',
                transition: 'transform 0.12s',
                '&:hover': { transform: 'scale(1.08)' },
              }}
            />
          </Tooltip>
        ))}
      </Box>

      <Box sx={{ display: 'flex', gap: 1.5, alignItems: 'center', flexWrap: 'wrap' }}>
        <TextField
          type="color"
          label="Custom"
          size="small"
          value={brandColor}
          onChange={(e) => setBrandColor(e.target.value)}
          sx={{ width: 110 }}
          slotProps={{ inputLabel: { shrink: true } }}
        />
        <TextField
          size="small"
          label="Hex"
          value={brandColor}
          onChange={(e) => {
            const v = e.target.value
            // Only push a valid colour into the theme — a half-typed "#6C6"
            // would otherwise repaint the whole app mid-keystroke.
            if (/^#[0-9a-fA-F]{6}$/.test(v)) setBrandColor(v)
          }}
          sx={{ width: 130 }}
        />
        <Button size="small" variant="outlined" onClick={reset}>Reset to default</Button>
      </Box>

      <Alert severity="info" variant="outlined" sx={{ mt: 2.5 }}>
        Very light accents can be hard to read in light mode. The presets above
        are checked to stay legible in both.
      </Alert>
    </GlassCard>
  )
}

function AdvancedLinksSection() {
  const navigate = useNavigate()

  const links: { label: string; description: string; path: string; permission: string }[] = [
    { label: 'API Keys', description: 'Issue and revoke machine credentials', path: '/api-keys', permission: 'apikey:manage' },
    { label: 'IP Allowlist', description: 'Restrict access to known networks', path: '/ip-allowlist', permission: 'iplist:manage' },
    { label: 'Alert Dedup Rules', description: 'Full editor for the rules above', path: '/alert-dedup', permission: 'alert:dedup:manage' },
    { label: 'Developer Tools', description: 'API reference and webhook testing', path: '/developer', permission: 'apikey:manage' },
  ]

  return (
    <PermissionGuard permission="settings:read">
      <Divider sx={{ my: 4, borderColor: 'rgba(255,255,255,0.08)' }} />
      <Typography variant="h6" sx={{ mb: 0.5, fontWeight: 700 }}>Advanced</Typography>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Configured once during setup — kept out of the main menu.
      </Typography>
      <Grid container spacing={2}>
        {links.map((l) => (
          <PermissionGuard permission={l.permission} key={l.path}>
            <Grid size={{ xs: 12, sm: 6, md: 3 }}>
              <Button
                fullWidth
                variant="outlined"
                onClick={() => navigate(l.path)}
                sx={{
                  justifyContent: 'flex-start',
                  textAlign: 'left',
                  flexDirection: 'column',
                  alignItems: 'flex-start',
                  gap: 0.25,
                  py: 1.25,
                  textTransform: 'none',
                }}
              >
                <Typography variant="body2" sx={{ fontWeight: 700 }}>{l.label}</Typography>
                <Typography variant="caption" color="text.secondary">{l.description}</Typography>
              </Button>
            </Grid>
          </PermissionGuard>
        ))}
      </Grid>
    </PermissionGuard>
  )
}

function SessionsSection() {
  const queryClient = useQueryClient()
  const { data: sessions, isLoading } = useQuery({ queryKey: ['my-sessions'], queryFn: getMySessions })

  const { mutate: revoke } = useMutation({
    mutationFn: (sessionId: string) => revokeMySession(sessionId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['my-sessions'] }),
  })

  const { mutate: revokeAll, isPending: revokingAll } = useMutation({
    mutationFn: revokeAllMySessions,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['my-sessions'] }),
  })

  return (
    <GlassCard sx={{ maxWidth: 640 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 2, pb: 0 }}>
        <Typography variant="subtitle2" color="text.secondary">
          {sessions?.length ?? 0} active session{sessions?.length !== 1 ? 's' : ''}
        </Typography>
        {(sessions?.length ?? 0) > 1 && (
          <Tooltip title="Revoke all other sessions">
            <Button startIcon={<DeleteSweepIcon />} size="small" color="error" variant="outlined"
              disabled={revokingAll} onClick={() => revokeAll()}>
              Revoke All
            </Button>
          </Tooltip>
        )}
      </Box>
      {isLoading ? (
        <Box sx={{ p: 2 }}><Skeleton /><Skeleton /><Skeleton /></Box>
      ) : !sessions?.length ? (
        <Box sx={{ p: 3, textAlign: 'center' }}>
          <Typography color="text.secondary">No active sessions found.</Typography>
        </Box>
      ) : (
        <List dense>
          {sessions.map((s, i) => (
            <Box key={s.id}>
              {i > 0 && <Divider sx={{ borderColor: 'rgba(255,255,255,0.06)' }} />}
              <ListItem>
                <ListItemText
                  primary={s.device_name ?? 'Unknown device'}
                  secondary={`IP: ${s.last_ip ?? '—'} · Last active: ${s.last_seen_at ? new Date(s.last_seen_at).toLocaleString() : '—'} · Expires ${new Date(s.expires_at).toLocaleDateString()}`}
                />
                <ListItemSecondaryAction>
                  <Tooltip title="Revoke this session">
                    <IconButton size="small" edge="end" onClick={() => revoke(s.id)}>
                      <LogoutIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </ListItemSecondaryAction>
              </ListItem>
            </Box>
          ))}
        </List>
      )}
    </GlassCard>
  )
}

function TwoFASection() {
  const qc = useQueryClient()
  const [setupData, setSetupData] = useState<{ qr_code_svg: string; manual_key: string } | null>(null)
  const [totpCode, setTotpCode] = useState('')
  const [disableCode, setDisableCode] = useState('')
  const [showDisable, setShowDisable] = useState(false)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: status, isLoading } = useQuery({
    queryKey: ['2fa_status'],
    queryFn: () => get2FAStatus(),
  })

  const setupMut = useMutation({
    mutationFn: () => setup2FA(),
    onSuccess: (data) => setSetupData(data),
  })

  const enableMut = useMutation({
    mutationFn: () => enable2FA(totpCode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['2fa_status'] })
      setSetupData(null)
      setTotpCode('')
      setMsg({ type: 'success', text: '2FA enabled successfully. Keep your backup codes safe.' })
    },
    onError: () => setMsg({ type: 'error', text: 'Invalid TOTP code. Please try again.' }),
  })

  const disableMut = useMutation({
    mutationFn: () => disable2FA(disableCode),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['2fa_status'] })
      setShowDisable(false)
      setDisableCode('')
      setMsg({ type: 'success', text: '2FA has been disabled.' })
    },
    onError: () => setMsg({ type: 'error', text: 'Invalid TOTP code.' }),
  })

  if (isLoading) return <Skeleton height={80} />

  const is2FAEnabled = (status as any)?.totp_enabled

  return (
    <GlassCard sx={{ p: 3, maxWidth: 560 }}>
      {msg && (
        <Alert severity={msg.type} sx={{ mb: 2 }} onClose={() => setMsg(null)}>{msg.text}</Alert>
      )}

      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mb: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>TOTP Authenticator App</Typography>
        <Chip
          label={is2FAEnabled ? 'Enabled' : 'Disabled'}
          color={is2FAEnabled ? 'success' : 'default'}
          size="small"
        />
      </Box>

      {!is2FAEnabled && !setupData && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Add an extra layer of security. After enabling, every login will require a time-based code from your authenticator app.
          </Typography>
          <Button variant="contained" onClick={() => setupMut.mutate()} disabled={setupMut.isPending}>
            Set Up 2FA
          </Button>
        </>
      )}

      {setupData && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            Scan this QR code with your authenticator app (Google Authenticator, Authy, etc.), then enter the 6-digit code to confirm.
          </Typography>
          <Box
            sx={{ mb: 2, p: 1, background: '#fff', display: 'inline-block', borderRadius: 1 }}
            dangerouslySetInnerHTML={{ __html: setupData.qr_code_svg }}
          />
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
            Manual key: <code style={{ userSelect: 'all', letterSpacing: '0.1em' }}>{setupData.manual_key}</code>
          </Typography>
          <TextField
            label="Verification Code" value={totpCode} size="small"
            onChange={(e) => setTotpCode(e.target.value.replace(/\D/g, '').slice(0, 6))}

            sx={{ mb: 1, mr: 1, width: 180 }} slotProps={{ htmlInput: { inputMode: 'numeric', maxLength: 6 } }}
          />
          <Button
            variant="contained" size="small"
            disabled={totpCode.length !== 6 || enableMut.isPending}
            onClick={() => enableMut.mutate()}
          >
            Verify & Enable
          </Button>
          <Button size="small" sx={{ ml: 1 }} onClick={() => { setSetupData(null); setTotpCode('') }}>
            Cancel
          </Button>
        </>
      )}

      {is2FAEnabled && !showDisable && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
            2FA is active. Every login requires your authenticator app code.
          </Typography>
          <Button variant="outlined" color="error" size="small" onClick={() => setShowDisable(true)}>
            Disable 2FA
          </Button>
        </>
      )}

      {is2FAEnabled && showDisable && (
        <>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Enter your current TOTP code to disable 2FA:
          </Typography>
          <TextField
            label="TOTP Code" value={disableCode} size="small"
            onChange={(e) => setDisableCode(e.target.value.replace(/\D/g, '').slice(0, 6))}

            sx={{ mb: 1, mr: 1, width: 180 }} slotProps={{ htmlInput: { inputMode: 'numeric', maxLength: 6 } }}
          />
          <Button
            variant="contained" color="error" size="small"
            disabled={disableCode.length !== 6 || disableMut.isPending}
            onClick={() => disableMut.mutate()}
          >
            Confirm Disable
          </Button>
          <Button size="small" sx={{ ml: 1 }} onClick={() => { setShowDisable(false); setDisableCode('') }}>
            Cancel
          </Button>
        </>
      )}
    </GlassCard>
  )
}

function AlertDedupRulesSection() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [moduleType, setModuleType] = useState('')
  const [cameraId, setCameraId] = useState('')
  const [windowSeconds, setWindowSeconds] = useState(300)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: rules, isLoading } = useQuery({ queryKey: ['dedup-rules'], queryFn: listDedupRules })
  const { data: cameras } = useQuery({ queryKey: ['cameras'], queryFn: () => getCameras() })

  const createMut = useMutation({
    mutationFn: (data: DedupRuleBody) => createDedupRule(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['dedup-rules'] })
      setOpen(false)
      setModuleType('')
      setCameraId('')
      setWindowSeconds(300)
      setMsg({ type: 'success', text: 'Rule created.' })
      setTimeout(() => setMsg(null), 3000)
    },
    onError: () => setMsg({ type: 'error', text: 'Failed to create rule.' }),
  })

  const deleteMut = useMutation({
    mutationFn: (id: string) => deleteDedupRule(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['dedup-rules'] }),
  })

  const MODULE_TYPES = ['lpr', 'face', 'intrusion', 'ppe', 'crowd', 'fire_smoke', 'weapon', 'behavior', 'tampering', 'abandoned', 'fall']

  const formatWindow = (s: number) => s >= 3600 ? `${s / 3600}h` : s >= 60 ? `${s / 60}m` : `${s}s`

  return (
    <GlassCard sx={{ maxWidth: 720, p: 0 }}>
      {msg && (
        <Alert severity={msg.type} sx={{ m: 2, mb: 0 }} onClose={() => setMsg(null)}>{msg.text}</Alert>
      )}
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', p: 2 }}>
        <Typography variant="body2" color="text.secondary">
          Suppress duplicate alerts of the same type on the same camera within a configurable time window.
        </Typography>
        <Button startIcon={<AddIcon />} variant="contained" size="small" onClick={() => setOpen(true)}>
          Add Rule
        </Button>
      </Box>
      {isLoading ? (
        <Box sx={{ p: 2 }}><Skeleton /><Skeleton /><Skeleton /></Box>
      ) : !rules?.length ? (
        <Box sx={{ p: 3, textAlign: 'center' }}>
          <Typography color="text.secondary" variant="body2">No deduplication rules configured.</Typography>
        </Box>
      ) : (
        <List dense>
          {rules.map((rule, i) => (
            <Box key={rule.id}>
              {i > 0 && <Divider sx={{ borderColor: 'rgba(255,255,255,0.06)' }} />}
              <ListItem>
                <ListItemText
                  primary={
                    <Box sx={{ display: 'flex', gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
                      <Chip label={rule.module_type ?? 'all modules'} size="small" color="primary" variant="outlined" />
                      <Chip label={rule.camera_name ?? 'all cameras'} size="small" variant="outlined" />
                      <Chip label={`window: ${formatWindow(rule.window_seconds)}`} size="small" />
                      {!rule.is_active && <Chip label="inactive" size="small" color="default" />}
                    </Box>
                  }
                  secondary={`Created ${new Date(rule.created_at).toLocaleDateString()}`}
                />
                <ListItemSecondaryAction>
                  <Tooltip title="Delete rule">
                    <IconButton size="small" edge="end" onClick={() => deleteMut.mutate(rule.id)}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </ListItemSecondaryAction>
              </ListItem>
            </Box>
          ))}
        </List>
      )}

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="xs" fullWidth>
        <DialogTitle>New Deduplication Rule</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <FormControl size="small" fullWidth>
            <InputLabel>Module type (blank = all)</InputLabel>
            <Select
              value={moduleType}
              label="Module type (blank = all)"
              onChange={(e) => setModuleType(e.target.value)}
            >
              <MenuItem value=""><em>All modules</em></MenuItem>
              {MODULE_TYPES.map((m) => <MenuItem key={m} value={m}>{m}</MenuItem>)}
            </Select>
          </FormControl>

          <FormControl size="small" fullWidth>
            <InputLabel>Camera (blank = all)</InputLabel>
            <Select
              value={cameraId}
              label="Camera (blank = all)"
              onChange={(e) => setCameraId(e.target.value)}
            >
              <MenuItem value=""><em>All cameras</em></MenuItem>
              {cameras?.map((c: any) => <MenuItem key={c.id} value={c.id}>{c.name}</MenuItem>)}
            </Select>
          </FormControl>

          <TextField
            label="Window (seconds)"
            type="number"
            size="small"
            value={windowSeconds}
            onChange={(e) => setWindowSeconds(Math.max(1, Math.min(86400, parseInt(e.target.value) || 300)))}
            helperText={`${formatWindow(windowSeconds)} — suppress duplicates within this period`}
            slotProps={{ htmlInput: { min: 1, max: 86400 } }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={createMut.isPending}
            onClick={() => createMut.mutate({
              module_type: moduleType || null,
              camera_id: cameraId || null,
              window_seconds: windowSeconds,
            })}
          >
            Create
          </Button>
        </DialogActions>
      </Dialog>
    </GlassCard>
  )
}

function TwoFAPolicySection() {
  const qc = useQueryClient()
  const [graceHours, setGraceHours] = useState<number>(0)
  const [msg, setMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)

  const { data: policy, isLoading } = useQuery({
    queryKey: ['2fa_policy'],
    queryFn: get2faPolicy,
  })

  useEffect(() => {
    if (policy) setGraceHours(policy.grace_hours)
  }, [policy])

  const { mutate: savePolicy, isPending } = useMutation({
    mutationFn: (data: { required: boolean; grace_hours: number }) => set2faPolicy(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['2fa_policy'] })
      setMsg({ type: 'success', text: 'Policy saved.' })
      setTimeout(() => setMsg(null), 3000)
    },
    onError: () => setMsg({ type: 'error', text: 'Failed to save policy.' }),
  })

  if (isLoading) return <Skeleton height={120} sx={{ maxWidth: 560 }} />

  const required = policy?.required ?? false

  return (
    <GlassCard sx={{ p: 3, maxWidth: 560 }}>
      {msg && (
        <Alert severity={msg.type} sx={{ mb: 2 }} onClose={() => setMsg(null)}>{msg.text}</Alert>
      )}
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        When enabled, all tenant users without TOTP set up will be blocked at login and directed to the 2FA setup flow. A grace period exempts newly created accounts.
      </Typography>
      <FormControlLabel
        control={
          <Switch
            checked={required}
            disabled={isPending}
            onChange={(e) => savePolicy({ required: e.target.checked, grace_hours: graceHours })}
          />
        }
        label={<Typography variant="subtitle2">{required ? 'Enforce 2FA for all users (ON)' : 'No enforcement (OFF)'}</Typography>}
        sx={{ mb: 2 }}
      />
      <Box sx={{ display: 'flex', alignItems: 'center', gap: 2, mt: 1 }}>
        <TextField
          label="Grace period (hours)"
          type="number"
          size="small"
          value={graceHours}
          onChange={(e) => setGraceHours(Math.max(0, parseInt(e.target.value, 10) || 0))}

          helperText="0 = immediate; new accounts are exempt for this many hours"
          sx={{ width: 220 }} slotProps={{ htmlInput: { min: 0, max: 720 } }}
        />
        <Button
          variant="outlined"
          size="small"
          disabled={isPending || graceHours === (policy?.grace_hours ?? 0)}
          onClick={() => savePolicy({ required, grace_hours: graceHours })}
        >
          Save
        </Button>
      </Box>
    </GlassCard>
  )
}


// ── Page names ──────────────────────────────────────────────────────────────

/**
 * Rename any page to the words this company actually uses.
 *
 * Every trade says it differently — occurrence book, day book, DOB — and a
 * product that insists on its own vocabulary makes staff translate on every
 * screen. Only edited entries are stored, so a tenant who renames one page
 * still receives our wording everywhere else, including on pages added later.
 */
function PageLabelsSection() {
  const qc = useQueryClient()
  const overrides = usePageLabelOverrides()
  const [filter, setFilter] = useState('')
  const [draft, setDraft] = useState<Record<string, { title: string; subtitle: string }>>({})

  const save = useMutation({
    mutationFn: (next: PageLabelOverrides) => upsertSetting(PAGE_LABELS_SETTING, next),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  const keys = (Object.keys(PAGE_LABELS) as PageKey[]).filter((k) => {
    if (!filter.trim()) return true
    const q = filter.toLowerCase()
    return k.includes(q) || PAGE_LABELS[k].title.toLowerCase().includes(q)
  })

  const valueFor = (k: PageKey) =>
    draft[k] ?? {
      title: overrides[k]?.title ?? PAGE_LABELS[k].title,
      subtitle: overrides[k]?.subtitle ?? PAGE_LABELS[k].subtitle,
    }

  const commit = (k: PageKey) => {
    const v = valueFor(k)
    const base = PAGE_LABELS[k]
    const next: PageLabelOverrides = { ...overrides }
    const entry: { title?: string; subtitle?: string } = {}
    // Store only what differs from the default — that is what lets our copy
    // keep improving for everything this tenant did not deliberately reword.
    if (v.title.trim() && v.title.trim() !== base.title) entry.title = v.title.trim()
    if (v.subtitle.trim() !== base.subtitle) entry.subtitle = v.subtitle.trim()
    if (Object.keys(entry).length) next[k] = entry
    else delete next[k]
    save.mutate(next)
    setDraft((d) => { const { [k]: _drop, ...rest } = d; return rest })
  }

  const reset = (k: PageKey) => {
    const next = { ...overrides }
    delete next[k]
    save.mutate(next)
    setDraft((d) => { const { [k]: _drop, ...rest } = d; return rest })
  }

  return (
    <GlassCard sx={{ p: 3 }}>
      <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
        Rename any page and rewrite its description to match your own terminology. Everyone in this
        tenant sees your wording. Anything you leave alone keeps the default and will pick up future
        improvements.
      </Typography>
      <TextField
        size="small" fullWidth placeholder="Filter pages…" value={filter}
        onChange={(e) => setFilter(e.target.value)} sx={{ mb: 2 }}
      />
      <Box sx={{ maxHeight: 460, overflowY: 'auto', pr: 1 }}>
        {keys.map((k) => {
          const v = valueFor(k)
          const customised = Boolean(overrides[k])
          const dirty = Boolean(draft[k])
          return (
            <Box key={k} sx={{ py: 1.5, borderTop: '1px solid rgba(255,255,255,0.06)' }}>
              <Stack direction="row" spacing={1} sx={{ alignItems: 'center', mb: 0.75 }}>
                <Typography variant="caption" color="text.disabled" sx={{ flex: 1, fontFamily: 'monospace' }}>
                  {k}
                </Typography>
                {customised && <Chip size="small" label="Customised" sx={{ height: 18, fontSize: '0.62rem' }} />}
                {dirty && (
                  <Button size="small" variant="contained" onClick={() => commit(k)} disabled={save.isPending}>
                    Save
                  </Button>
                )}
                {customised && !dirty && (
                  <Button size="small" color="inherit" onClick={() => reset(k)} disabled={save.isPending}>
                    Reset
                  </Button>
                )}
              </Stack>
              <Stack direction={{ xs: 'column', md: 'row' }} spacing={1}>
                <TextField
                  size="small" label="Title" value={v.title} sx={{ flex: 1 }}
                  slotProps={{ htmlInput: { maxLength: 60 } }}
                  onChange={(e) => setDraft((d) => ({ ...d, [k]: { ...v, title: e.target.value } }))}
                />
                <TextField
                  size="small" label="Description" value={v.subtitle} sx={{ flex: 2 }}
                  slotProps={{ htmlInput: { maxLength: 160 } }}
                  onChange={(e) => setDraft((d) => ({ ...d, [k]: { ...v, subtitle: e.target.value } }))}
                />
              </Stack>
            </Box>
          )
        })}
      </Box>
    </GlassCard>
  )
}

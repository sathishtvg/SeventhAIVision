/**
 * Per-site recording policy editor (Phase X-A).
 *
 * The design problem this dialog solves is the empty field. A blank retention
 * box has to mean "inherit the tenant setting" — not zero — because zero is a
 * real, different, destructive answer ("keep nothing centrally"). So both
 * retention inputs show the inherited value as placeholder text and send
 * `null` when blank, and the helper text says which one is in force. An admin
 * who clears the box gets the tenant default back, not an instant purge.
 */
import { useEffect, useState } from 'react'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  Divider, FormControlLabel, Grid, MenuItem, Stack, Switch, TextField, Typography,
} from '@mui/material'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import {
  deleteRecordingPolicy,
  getEffectivePolicy,
  getRecordingPolicy,
  upsertRecordingPolicy,
  RECORD_MODE_LABELS,
  SYNC_MODE_LABELS,
  type Compression,
  type PolicyUpsert,
  type RecordMode,
  type SyncMode,
} from '@/api/recordingPolicies'

interface Props {
  open: boolean
  siteId: string
  siteName: string
  onClose: () => void
}

/** '' -> null (inherit); otherwise the number, including 0. */
function toNullableNumber(v: string): number | null {
  return v.trim() === '' ? null : Number(v)
}

export function RecordingPolicyDialog({ open, siteId, siteName, onClose }: Props) {
  const qc = useQueryClient()

  const { data: effective } = useQuery({
    queryKey: ['recording-policy-effective', siteId],
    queryFn: () => getEffectivePolicy(siteId),
    enabled: open,
  })

  // 404 is the normal "not configured yet" answer, not an error worth showing.
  const { data: policy, isFetched } = useQuery({
    queryKey: ['recording-policy', siteId],
    queryFn: () => getRecordingPolicy(siteId).catch(() => null),
    enabled: open,
  })

  const [recordMode, setRecordMode] = useState<RecordMode>('continuous')
  const [syncMode, setSyncMode] = useState<SyncMode>('central')
  const [centralDays, setCentralDays] = useState('')
  const [localDays, setLocalDays] = useState('')
  const [clipPre, setClipPre] = useState('20')
  const [clipPost, setClipPost] = useState('60')
  const [bandwidth, setBandwidth] = useState('')
  const [windowStart, setWindowStart] = useState('')
  const [windowEnd, setWindowEnd] = useState('')
  const [compression, setCompression] = useState<Compression>('none')
  const [encrypt, setEncrypt] = useState(false)
  const [checksums, setChecksums] = useState(true)

  // Re-seed whenever the dialog opens for a different site. A useState
  // initializer only runs on first mount, so without this the form would keep
  // showing the previously-opened site's policy — the exact resync bug already
  // fixed once in Users.tsx's UserFormDialog.
  useEffect(() => {
    if (!open) return
    setRecordMode(policy?.record_mode ?? 'continuous')
    setSyncMode(policy?.sync_mode ?? 'central')
    setCentralDays(policy?.central_retention_days != null ? String(policy.central_retention_days) : '')
    setLocalDays(policy?.local_retention_days != null ? String(policy.local_retention_days) : '')
    setClipPre(String(policy?.clip_pre_seconds ?? 20))
    setClipPost(String(policy?.clip_post_seconds ?? 60))
    setBandwidth(policy?.bandwidth_limit_kbps != null ? String(policy.bandwidth_limit_kbps) : '')
    setWindowStart(policy?.sync_window_start?.slice(0, 5) ?? '')
    setWindowEnd(policy?.sync_window_end?.slice(0, 5) ?? '')
    setCompression(policy?.compression ?? 'none')
    setEncrypt(policy?.encrypt_archives ?? false)
    setChecksums(policy?.verify_checksums ?? true)
  }, [open, siteId, policy])

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ['recording-policy', siteId] })
    qc.invalidateQueries({ queryKey: ['recording-policy-effective', siteId] })
    qc.invalidateQueries({ queryKey: ['recording-policies'] })
  }

  const save = useMutation({
    mutationFn: () => {
      const body: PolicyUpsert = {
        record_mode: recordMode,
        sync_mode: syncMode,
        central_retention_days: toNullableNumber(centralDays),
        local_retention_days: toNullableNumber(localDays),
        clip_pre_seconds: Number(clipPre || 0),
        clip_post_seconds: Number(clipPost || 0),
        bandwidth_limit_kbps: toNullableNumber(bandwidth),
        // Send seconds — the API's `time` field accepts HH:MM:SS.
        sync_window_start: windowStart ? `${windowStart}:00` : null,
        sync_window_end: windowEnd ? `${windowEnd}:00` : null,
        compression,
        encrypt_archives: encrypt,
        verify_checksums: checksums,
        is_active: true,
      }
      return upsertRecordingPolicy(siteId, body)
    },
    onSuccess: () => {
      invalidate()
      onClose()
    },
  })

  const revert = useMutation({
    mutationFn: () => deleteRecordingPolicy(siteId),
    onSuccess: () => {
      invalidate()
      onClose()
    },
  })

  const inheritedDays = effective?.tenant_retention_days
  const saveError = save.error as { response?: { data?: { detail?: string } } } | null

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>
        Recording Policy
        <Typography variant="body2" color="text.secondary">{siteName}</Typography>
      </DialogTitle>

      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: '12px !important' }}>
        {isFetched && !policy && (
          <Alert severity="info" variant="outlined">
            This site has no policy yet — it currently inherits the tenant-wide
            settings. Saving creates one that applies to this site only.
          </Alert>
        )}

        {saveError?.response?.data?.detail && (
          <Alert severity="error">{saveError.response.data.detail}</Alert>
        )}

        <TextField
          select label="Recording Mode" value={recordMode} fullWidth
          onChange={(e) => setRecordMode(e.target.value as RecordMode)}
          helperText={
            recordMode === 'off'
              ? 'No footage will be captured at this site.'
              : recordMode === 'motion' || recordMode === 'scheduled'
                ? 'Gating is not active yet — this site keeps recording continuously for now.'
                : undefined
          }
        >
          {(Object.keys(RECORD_MODE_LABELS) as RecordMode[]).map((m) => (
            <MenuItem key={m} value={m}>{RECORD_MODE_LABELS[m]}</MenuItem>
          ))}
        </TextField>

        <TextField
          select label="Sync to Central Server" value={syncMode} fullWidth
          onChange={(e) => setSyncMode(e.target.value as SyncMode)}
        >
          {(Object.keys(SYNC_MODE_LABELS) as SyncMode[]).map((m) => (
            <MenuItem key={m} value={m}>{SYNC_MODE_LABELS[m]}</MenuItem>
          ))}
        </TextField>

        <Divider textAlign="left">
          <Typography variant="caption" color="text.secondary">RETENTION</Typography>
        </Divider>

        <Grid container spacing={2}>
          <Grid size={{ xs: 12, sm: 6 }}>
            <TextField
              label="Central Retention (days)" type="number" fullWidth
              value={centralDays}
              onChange={(e) => setCentralDays(e.target.value)}
              placeholder={inheritedDays != null ? String(inheritedDays) : ''}
              slotProps={{ inputLabel: { shrink: true } }}
              helperText={
                centralDays.trim() === ''
                  ? `Inheriting ${inheritedDays ?? '—'} days from tenant settings`
                  : Number(centralDays) === 0
                    ? 'Zero keeps nothing on the central server'
                    : 'Overrides the tenant setting for this site'
              }
            />
          </Grid>
          <Grid size={{ xs: 12, sm: 6 }}>
            <TextField
              label="Local Retention (days)" type="number" fullWidth
              value={localDays}
              onChange={(e) => setLocalDays(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
              helperText="On-site storage. Applies once an edge gateway is deployed."
            />
          </Grid>
        </Grid>

        {effective && (
          <Box>
            <Chip
              size="small"
              color={effective.central_retention_inherited ? 'default' : 'primary'}
              variant="outlined"
              label={
                `In force now: ${effective.central_retention_days} days` +
                (effective.central_retention_inherited ? ' (inherited)' : ' (site policy)')
              }
            />
          </Box>
        )}

        <Divider textAlign="left">
          <Typography variant="caption" color="text.secondary">EVENT CLIPS &amp; BANDWIDTH</Typography>
        </Divider>

        <Grid container spacing={2}>
          <Grid size={{ xs: 6, sm: 4 }}>
            <TextField
              label="Pre-event (s)" type="number" fullWidth
              value={clipPre} onChange={(e) => setClipPre(e.target.value)}
            />
          </Grid>
          <Grid size={{ xs: 6, sm: 4 }}>
            <TextField
              label="Post-event (s)" type="number" fullWidth
              value={clipPost} onChange={(e) => setClipPost(e.target.value)}
            />
          </Grid>
          <Grid size={{ xs: 12, sm: 4 }}>
            <TextField
              label="Bandwidth cap (kbps)" type="number" fullWidth
              value={bandwidth} onChange={(e) => setBandwidth(e.target.value)}
              placeholder="Unlimited"
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Grid>
        </Grid>

        <Grid container spacing={2}>
          <Grid size={{ xs: 6 }}>
            <TextField
              label="Sync window start" type="time" fullWidth
              value={windowStart} onChange={(e) => setWindowStart(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Grid>
          <Grid size={{ xs: 6 }}>
            <TextField
              label="Sync window end" type="time" fullWidth
              value={windowEnd} onChange={(e) => setWindowEnd(e.target.value)}
              slotProps={{ inputLabel: { shrink: true } }}
              helperText="Overnight windows are fine (e.g. 22:00 to 06:00)"
            />
          </Grid>
        </Grid>

        <TextField
          select label="Compression" value={compression} fullWidth
          onChange={(e) => setCompression(e.target.value as Compression)}
        >
          <MenuItem value="none">None — store as captured</MenuItem>
          <MenuItem value="h264">H.264</MenuItem>
          <MenuItem value="h265">H.265 — smaller, more CPU</MenuItem>
        </TextField>

        <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap' }}>
          <FormControlLabel
            control={<Switch checked={encrypt} onChange={(e) => setEncrypt(e.target.checked)} />}
            label="Encrypt archives"
          />
          <FormControlLabel
            control={<Switch checked={checksums} onChange={(e) => setChecksums(e.target.checked)} />}
            label="Verify checksums"
          />
        </Stack>
      </DialogContent>

      <DialogActions sx={{ px: 3, pb: 2 }}>
        {policy && (
          <Button
            color="error"
            onClick={() => revert.mutate()}
            disabled={revert.isPending}
            sx={{ mr: 'auto' }}
          >
            Revert to tenant default
          </Button>
        )}
        <Button onClick={onClose}>Cancel</Button>
        <Button variant="contained" onClick={() => save.mutate()} disabled={save.isPending}>
          {save.isPending ? 'Saving…' : 'Save Policy'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

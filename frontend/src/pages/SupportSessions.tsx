/**
 * Support sessions — how a platform operator looks at a customer's tenant.
 *
 * Super Admin holds four permissions and none of them read a customer's
 * rosters, payroll or key register (migration 0102). That is right for almost
 * all platform work and useless when a ticket comes in and somebody has to
 * look. So looking became an event: name the tenant, write down why, and the
 * customer gets the entry in their own audit log.
 *
 * The form asks for a reason before it will do anything, and the shortest
 * useful duration is the default. Both are deliberate — a control that is
 * easier to hold open than to close gets held open.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  FormControl, InputLabel, MenuItem, Select, Skeleton, Table, TableBody,
  TableCell, TableContainer, TableHead, TableRow, TextField, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import SupportAgentIcon from '@mui/icons-material/SupportAgent'
import LoginIcon from '@mui/icons-material/Login'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import {
  endSupportSession, listSupportSessions, openSupportSession,
} from '@/api/supportSessions'
import { getTenants } from '@/api/tenants'
import { useAuthStore } from '@/store/auth'

/** Matches SUPPORT_TOKEN_MAX_MINUTES on the server, which clamps anything
 *  longer. An eight-hour "temporary" is a standing grant with extra steps. */
const DURATIONS = [15, 30, 60]

/** The server requires ten characters. Saying so before the request is refused
 *  is kinder than saying it after. */
const MIN_REASON = 10

function when(iso: string) {
  return new Date(iso).toLocaleString()
}

export default function SupportSessions() {
  const queryClient = useQueryClient()
  const enterSupportSession = useAuthStore((s) => s.enterSupportSession)
  const activeSession = useAuthStore((s) => s.supportSession)

  const [open, setOpen] = useState(false)
  const [tenantId, setTenantId] = useState('')
  const [reason, setReason] = useState('')
  const [minutes, setMinutes] = useState(30)
  const [error, setError] = useState('')

  const { data: sessions, isLoading } = useQuery({
    queryKey: ['support-sessions'],
    queryFn: listSupportSessions,
  })
  const { data: tenants } = useQuery({ queryKey: ['tenants'], queryFn: getTenants })

  const invalidate = () =>
    queryClient.invalidateQueries({ queryKey: ['support-sessions'] })

  const openSession = useMutation({
    mutationFn: () => openSupportSession({ tenant_id: tenantId, reason, minutes }),
    onSuccess: async (s) => {
      setOpen(false)
      setReason('')
      setTenantId('')
      setError('')
      await enterSupportSession(
        { id: s.id, tenantId: s.tenant_id, tenantName: s.tenant_name, expiresAt: s.expires_at },
        s.access_token,
      )
      invalidate()
    },
    onError: (e: { response?: { data?: { detail?: string } } }) =>
      setError(e.response?.data?.detail || 'Could not open the session'),
  })

  const endSession = useMutation({
    mutationFn: (id: string) => endSupportSession(id),
    onSuccess: invalidate,
  })

  const reasonTooShort = reason.trim().length < MIN_REASON

  return (
    <Box>
      <PageHeader
        title="Support Sessions"
        subtitle="Entering a customer's tenant is recorded in their audit log as well as ours"
        action={
          <Button
            variant="contained" size="small" startIcon={<SupportAgentIcon />}
            disabled={Boolean(activeSession)}
            onClick={() => { setError(''); setOpen(true) }}
          >
            Open a session
          </Button>
        }
      />

      {activeSession && (
        <Alert severity="info" sx={{ mb: 2 }}>
          You are inside <strong>{activeSession.tenantName}</strong> until{' '}
          {when(activeSession.expiresAt)}. Leave it before opening another —
          two live sessions would mean two tenants' tokens in the same hands.
        </Alert>
      )}

      <GlassCard>
        {isLoading ? (
          <Skeleton variant="rectangular" height={180} />
        ) : !sessions?.length ? (
          <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
            No support session has been opened yet.
          </Typography>
        ) : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Tenant</TableCell>
                  <TableCell>Opened by</TableCell>
                  <TableCell>Reason</TableCell>
                  <TableCell>Started</TableCell>
                  <TableCell>Ends</TableCell>
                  <TableCell align="right">Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {sessions.map((s) => (
                  <TableRow key={s.id} hover>
                    <TableCell>{s.tenant_name}</TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {s.platform_user_email ?? s.platform_user_id}
                      </Typography>
                    </TableCell>
                    <TableCell sx={{ maxWidth: 320 }}>
                      <Tooltip title={s.reason}>
                        <Typography variant="body2" noWrap>{s.reason}</Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption">{when(s.started_at)}</Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption">
                        {s.ended_at ? when(s.ended_at) : when(s.expires_at)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      {s.is_live ? (
                        <Stack direction="row" spacing={1} sx={{ justifyContent: 'flex-end' }}>
                          <Chip label="live" size="small" color="warning" />
                          <Button
                            size="small" onClick={() => endSession.mutate(s.id)}
                            disabled={endSession.isPending}
                          >
                            End
                          </Button>
                        </Stack>
                      ) : (
                        <Chip
                          label={s.ended_at ? 'ended' : 'expired'}
                          size="small" variant="outlined"
                        />
                      )}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>

      <Dialog open={open} onClose={() => setOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle>Open a support session</DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            {error && <Alert severity="warning">{error}</Alert>}

            <Alert severity="info" icon={<LoginIcon fontSize="small" />}>
              This writes an entry into the customer's own audit log naming you
              and the reason below. It ends by itself, and ending it revokes
              your access straight away.
            </Alert>

            <FormControl size="small" fullWidth>
              <InputLabel>Tenant</InputLabel>
              <Select
                value={tenantId} label="Tenant"
                onChange={(e) => setTenantId(e.target.value)}
              >
                {(tenants ?? []).filter((t) => t.is_active).map((t) => (
                  <MenuItem key={t.id} value={t.id}>{t.name}</MenuItem>
                ))}
              </Select>
            </FormControl>

            <TextField
              label="Why" value={reason} onChange={(e) => setReason(e.target.value)}
              size="small" fullWidth multiline minRows={2}
              placeholder="Ticket 4821 — customer reports the roster grid is empty for September"
              error={reason.length > 0 && reasonTooShort}
              helperText={
                reason.length > 0 && reasonTooShort
                  ? `A few more words — at least ${MIN_REASON} characters.`
                  : 'The customer will read this. A ticket number and a symptom is enough.'
              }
            />

            <FormControl size="small" fullWidth>
              <InputLabel>Length</InputLabel>
              <Select
                value={minutes} label="Length"
                onChange={(e) => setMinutes(Number(e.target.value))}
              >
                {DURATIONS.map((m) => (
                  <MenuItem key={m} value={m}>{m} minutes</MenuItem>
                ))}
              </Select>
            </FormControl>
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpen(false)}>Cancel</Button>
          <Button
            variant="contained"
            disabled={!tenantId || reasonTooShort || openSession.isPending}
            onClick={() => openSession.mutate()}
          >
            Open session
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

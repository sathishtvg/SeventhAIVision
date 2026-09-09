/**
 * Platform Health & Error Centre (§18, §19).
 *
 * What makes this a console rather than a log is that it lists PROBLEMS, not
 * occurrences. One bad deploy produces the same failure thousands of times;
 * grouped by fingerprint it is a single row saying 40,000, and the question
 * "how many distinct things are broken" has an answer you can read at a glance
 * instead of a scrollbar.
 *
 * The affected-tenant count is the other half. "Something is failing" is a
 * shrug; "this is failing for three customers, one of them since Tuesday" is a
 * decision about what to fix first.
 *
 * Resolving is not deleting: if the same fingerprint arrives again the
 * collector reopens it, because a fix that did not hold is worse news than a
 * new bug.
 */
import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Alert, Box, Button, Chip, Dialog, DialogActions, DialogContent, DialogTitle,
  Skeleton, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  TextField, ToggleButton, ToggleButtonGroup, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import {
  getPlatformError, getPlatformErrors, updatePlatformError,
} from '@/api/platform'

const SEVERITY_COLOUR: Record<string, string> = {
  critical: '#FF4560',
  warning: '#FFB020',
  info: '#6C63FF',
}

function when(iso: string | null) {
  return iso ? new Date(iso).toLocaleString() : '—'
}

export default function ErrorCentre() {
  const queryClient = useQueryClient()
  const [statusFilter, setStatusFilter] = useState<string>('open')
  const [openId, setOpenId] = useState<string | null>(null)
  const [resolution, setResolution] = useState('')

  const { data: errors, isLoading } = useQuery({
    queryKey: ['platform-errors', statusFilter],
    queryFn: () => getPlatformErrors(
      statusFilter === 'all' ? {} : { status: statusFilter },
    ),
  })

  const { data: detail } = useQuery({
    queryKey: ['platform-error', openId],
    queryFn: () => getPlatformError(openId!),
    enabled: Boolean(openId),
  })

  const invalidate = () => {
    queryClient.invalidateQueries({ queryKey: ['platform-errors'] })
    queryClient.invalidateQueries({ queryKey: ['platform-dashboard'] })
  }

  const setStatus = useMutation({
    mutationFn: ({ id, status }: { id: string; status: 'acknowledged' | 'resolved' }) =>
      updatePlatformError(id, { status, resolution: resolution || undefined }),
    onSuccess: () => { setOpenId(null); setResolution(''); invalidate() },
  })

  return (
    <Box>
      <PageHeader
        title="Error Centre"
        subtitle="Distinct problems, not occurrences — and which customers each one is hurting"
      />

      <ToggleButtonGroup
        size="small" exclusive value={statusFilter} sx={{ mb: 2 }}
        onChange={(_, v) => v && setStatusFilter(v)}
      >
        <ToggleButton value="open">Open</ToggleButton>
        <ToggleButton value="acknowledged">Acknowledged</ToggleButton>
        <ToggleButton value="resolved">Resolved</ToggleButton>
        <ToggleButton value="all">All</ToggleButton>
      </ToggleButtonGroup>

      <GlassCard sx={{ p: 0 }}>
        {isLoading ? (
          <Skeleton variant="rectangular" height={200} />
        ) : !errors?.length ? (
          <Alert severity="success" sx={{ m: 2 }}>
            Nothing {statusFilter === 'all' ? 'recorded' : statusFilter}. The platform is quiet.
          </Alert>
        ) : (
          <TableContainer sx={{ overflowX: 'auto' }}>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Severity</TableCell>
                  <TableCell>Problem</TableCell>
                  <TableCell>Route</TableCell>
                  <TableCell align="right">Seen</TableCell>
                  <TableCell align="right">Tenants</TableCell>
                  <TableCell>First</TableCell>
                  <TableCell>Last</TableCell>
                  <TableCell align="right">Status</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {errors.map((e) => (
                  <TableRow
                    key={e.id} hover sx={{ cursor: 'pointer' }}
                    onClick={() => { setOpenId(e.id); setResolution(e.resolution ?? '') }}
                  >
                    <TableCell>
                      <Chip
                        label={e.severity} size="small"
                        sx={{ height: 19, fontSize: '0.62rem',
                              color: SEVERITY_COLOUR[e.severity],
                              bgcolor: `${SEVERITY_COLOUR[e.severity]}22` }}
                      />
                    </TableCell>
                    <TableCell sx={{ maxWidth: 300 }}>
                      <Typography variant="body2" sx={{ fontWeight: 600 }}>
                        {e.error_type}
                      </Typography>
                      <Tooltip title={e.message ?? ''}>
                        <Typography variant="caption" color="text.secondary" noWrap
                                    sx={{ display: 'block' }}>
                          {e.message}
                        </Typography>
                      </Tooltip>
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                        {e.method} {e.path_pattern}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Typography variant="body2" sx={{ fontWeight: 700 }}>
                        {e.occurrence_count.toLocaleString()}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">{e.tenants_affected}</TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {when(e.first_seen_at)}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary">
                        {when(e.last_seen_at)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Chip label={e.status} size="small" variant="outlined"
                            sx={{ height: 19, fontSize: '0.62rem' }} />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </TableContainer>
        )}
      </GlassCard>

      <Dialog open={Boolean(openId)} onClose={() => setOpenId(null)} maxWidth="md" fullWidth>
        <DialogTitle>{detail?.error.error_type ?? 'Problem'}</DialogTitle>
        <DialogContent dividers>
          {!detail ? <Skeleton height={220} /> : (
            <Stack spacing={2}>
              <Box>
                <Typography variant="body2" sx={{ mb: 0.5 }}>{detail.error.message}</Typography>
                <Typography variant="caption" sx={{ fontFamily: 'monospace' }}
                            color="text.secondary">
                  {detail.error.method} {detail.error.path_pattern}
                </Typography>
              </Box>

              <Stack direction="row" spacing={3} sx={{ flexWrap: 'wrap', gap: 1 }}>
                <Box>
                  <Typography variant="caption" color="text.secondary">Occurrences</Typography>
                  <Typography variant="h6">{detail.error.occurrence_count}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">First seen</Typography>
                  <Typography variant="body2">{when(detail.error.first_seen_at)}</Typography>
                </Box>
                <Box>
                  <Typography variant="caption" color="text.secondary">Last seen</Typography>
                  <Typography variant="body2">{when(detail.error.last_seen_at)}</Typography>
                </Box>
              </Stack>

              {detail.tenants_affected.length > 0 && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    Customers affected
                  </Typography>
                  <Stack direction="row" sx={{ flexWrap: 'wrap', gap: 0.5 }}>
                    {detail.tenants_affected.map((t) => (
                      <Chip key={t.id} size="small" label={`${t.name} · ${t.occurrences}`}
                            sx={{ height: 21, fontSize: '0.62rem' }} />
                    ))}
                  </Stack>
                </Box>
              )}

              {detail.recent_events[0]?.stack && (
                <Box>
                  <Typography variant="subtitle2" sx={{ mb: 0.5 }}>
                    Most recent stack
                  </Typography>
                  <Box
                    component="pre"
                    sx={{ m: 0, p: 1.5, maxHeight: 240, overflow: 'auto',
                          fontSize: '0.68rem', bgcolor: 'rgba(0,0,0,0.35)',
                          borderRadius: 1, whiteSpace: 'pre-wrap' }}
                  >
                    {detail.recent_events[0].stack}
                  </Box>
                </Box>
              )}

              <TextField
                label="Resolution" size="small" fullWidth multiline minRows={2}
                value={resolution} onChange={(e) => setResolution(e.target.value)}
                helperText="Recorded against the problem. If it happens again it reopens by itself."
              />
            </Stack>
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenId(null)}>Close</Button>
          <Button
            disabled={setStatus.isPending}
            onClick={() => openId && setStatus.mutate({ id: openId, status: 'acknowledged' })}
          >
            Acknowledge
          </Button>
          <Button
            variant="contained" disabled={setStatus.isPending}
            onClick={() => openId && setStatus.mutate({ id: openId, status: 'resolved' })}
          >
            Resolve
          </Button>
        </DialogActions>
      </Dialog>
    </Box>
  )
}

import { useState } from 'react'
import {
  Box,
  Typography,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Chip,
  IconButton,
  Button,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogActions,
  TextField,
  Skeleton,
  Tooltip,
  Alert,
  InputAdornment,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import ContentCopyIcon from '@mui/icons-material/ContentCopy'
import CheckIcon from '@mui/icons-material/Check'
import VpnKeyIcon from '@mui/icons-material/VpnKey'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { listApiKeys, createApiKey, revokeApiKey, type ApiKeyCreated } from '@/api/apikeys'
import { PageHeader } from '@/components/common/PageHeader'

function fmtDate(d: string | null) {
  if (!d) return '—'
  return new Date(d).toLocaleString()
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false)
  function handle() {
    navigator.clipboard.writeText(text).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }
  return (
    <Tooltip title={copied ? 'Copied!' : 'Copy to clipboard'}>
      <IconButton size="small" onClick={handle} color={copied ? 'success' : 'default'}>
        {copied ? <CheckIcon fontSize="small" /> : <ContentCopyIcon fontSize="small" />}
      </IconButton>
    </Tooltip>
  )
}

interface CreateDialogProps {
  open: boolean
  onClose: () => void
}

function CreateKeyDialog({ open, onClose }: CreateDialogProps) {
  const queryClient = useQueryClient()
  const [name, setName] = useState('')
  const [expiresAt, setExpiresAt] = useState('')
  const [created, setCreated] = useState<ApiKeyCreated | null>(null)

  const { mutate, isPending } = useMutation({
    mutationFn: createApiKey,
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['api-keys'] })
      setCreated(data)
    },
  })

  function handleClose() {
    setName('')
    setExpiresAt('')
    setCreated(null)
    onClose()
  }

  function handleCreate() {
    mutate({ name: name.trim(), expires_at: expiresAt || undefined })
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="sm" fullWidth>
      <DialogTitle>Create API Key</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        {!created ? (
          <>
            <TextField
              label="Key name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              size="small"
              fullWidth
              placeholder="e.g. CI pipeline, SIEM integration"
            />
            <TextField
              label="Expires at (optional)"
              type="datetime-local"
              value={expiresAt}
              onChange={(e) => setExpiresAt(e.target.value)}
              size="small"
              fullWidth
              slotProps={{ inputLabel: { shrink: true } }}
              helperText="Leave blank for a non-expiring key"
            />
          </>
        ) : (
          <>
            <Alert severity="warning" sx={{ mb: 1 }}>
              Copy this key now — it will <strong>never be shown again</strong>.
            </Alert>
            <TextField
              label="Your new API key"
              value={created.key}
              size="small"
              fullWidth
              slotProps={{
                input: {
                  readOnly: true,
                  sx: { fontFamily: 'monospace', fontSize: '0.8rem' },
                  endAdornment: (
                    <InputAdornment position="end">
                      <CopyButton text={created.key} />
                    </InputAdornment>
                  ),
                },
              }}
            />
            <Typography variant="caption" color="text.secondary">
              Prefix: <code>{created.key_prefix}</code> · Created: {fmtDate(created.created_at)}
            </Typography>
          </>
        )}
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>
          {created ? 'Done' : 'Cancel'}
        </Button>
        {!created && (
          <Button
            variant="contained"
            onClick={handleCreate}
            disabled={!name.trim() || isPending}
          >
            Create
          </Button>
        )}
      </DialogActions>
    </Dialog>
  )
}

export default function ApiKeys() {
  const queryClient = useQueryClient()
  const [createOpen, setCreateOpen] = useState(false)

  const { data: keys = [], isLoading } = useQuery({
    queryKey: ['api-keys'],
    queryFn: listApiKeys,
  })

  const { mutate: revoke } = useMutation({
    mutationFn: revokeApiKey,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['api-keys'] }),
  })

  return (
    <PermissionGuard permission="apikey:manage">
      <PageHeader pageKey="api-keys" />
      <Box sx={{ p: 3 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" mb={3}>
          <Stack direction="row" alignItems="center" gap={1.5}>
            <VpnKeyIcon sx={{ color: 'primary.main' }} />
            <Box>
              <Typography variant="h6" fontWeight={700}>API Keys</Typography>
              <Typography variant="caption" color="text.secondary">
                Machine-to-machine auth for external integrations
              </Typography>
            </Box>
          </Stack>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            size="small"
            onClick={() => setCreateOpen(true)}
          >
            New Key
          </Button>
        </Stack>

        <GlassCard>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>Name</TableCell>
                  <TableCell>Prefix</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Last used</TableCell>
                  <TableCell>Expires</TableCell>
                  <TableCell>Created</TableCell>
                  <TableCell>Created by</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {isLoading
                  ? Array.from({ length: 3 }).map((_, i) => (
                      <TableRow key={i}>
                        {Array.from({ length: 8 }).map((_, j) => (
                          <TableCell key={j}><Skeleton /></TableCell>
                        ))}
                      </TableRow>
                    ))
                  : keys.length === 0
                    ? (
                      <TableRow>
                        <TableCell colSpan={8} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                          No API keys yet. Create one to enable machine-to-machine integrations.
                        </TableCell>
                      </TableRow>
                    )
                    : keys.map((k) => (
                      <TableRow key={k.id} hover>
                        <TableCell sx={{ fontWeight: 600 }}>{k.name}</TableCell>
                        <TableCell>
                          <Typography variant="caption" fontFamily="monospace" sx={{ opacity: 0.85 }}>
                            sav1_{k.key_prefix}…
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={k.is_active ? 'Active' : 'Revoked'}
                            size="small"
                            color={k.is_active ? 'success' : 'default'}
                          />
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.78rem', color: 'text.secondary' }}>
                          {fmtDate(k.last_used_at)}
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.78rem', color: k.expires_at ? 'warning.main' : 'text.secondary' }}>
                          {fmtDate(k.expires_at)}
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.78rem', color: 'text.secondary' }}>
                          {fmtDate(k.created_at)}
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.78rem', color: 'text.secondary' }}>
                          {k.created_by_email ?? '—'}
                        </TableCell>
                        <TableCell align="right">
                          {k.is_active && (
                            <Tooltip title="Revoke key">
                              <IconButton
                                size="small"
                                color="error"
                                onClick={() => {
                                  if (confirm(`Revoke key "${k.name}"? This cannot be undone.`)) {
                                    revoke(k.id)
                                  }
                                }}
                              >
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
              </TableBody>
            </Table>
          </TableContainer>
        </GlassCard>

        <CreateKeyDialog open={createOpen} onClose={() => setCreateOpen(false)} />
      </Box>
    </PermissionGuard>
  )
}

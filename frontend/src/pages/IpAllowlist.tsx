import { useState } from 'react'
import {
  Box, Typography, Table, TableBody, TableCell, TableContainer, TableHead, TableRow,
  Chip, IconButton, Button, Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, Skeleton, Tooltip, Stack, Alert,
} from '@mui/material'
import AddIcon from '@mui/icons-material/Add'
import DeleteIcon from '@mui/icons-material/Delete'
import ToggleOnIcon from '@mui/icons-material/ToggleOn'
import ToggleOffIcon from '@mui/icons-material/ToggleOff'
import LanIcon from '@mui/icons-material/Lan'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { PermissionGuard } from '@/components/common/PermissionGuard'
import { listIpRules, addIpRule, deleteIpRule, toggleIpRule } from '@/api/ipallowlist'

function fmtDate(d: string | null) {
  if (!d) return '—'
  return new Date(d).toLocaleString()
}

interface AddRuleDialogProps {
  open: boolean
  onClose: () => void
}

function AddRuleDialog({ open, onClose }: AddRuleDialogProps) {
  const queryClient = useQueryClient()
  const [cidr, setCidr] = useState('')
  const [description, setDescription] = useState('')
  const [error, setError] = useState<string | null>(null)

  const { mutate, isPending } = useMutation({
    mutationFn: addIpRule,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['ip-allowlist'] })
      handleClose()
    },
    onError: (err: unknown) => {
      const msg = (err as { response?: { data?: { detail?: string } } })?.response?.data?.detail
      setError(msg ?? 'Failed to add rule')
    },
  })

  function handleClose() {
    setCidr('')
    setDescription('')
    setError(null)
    onClose()
  }

  return (
    <Dialog open={open} onClose={handleClose} maxWidth="xs" fullWidth>
      <DialogTitle>Add IP Rule</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}
        <TextField
          label="CIDR or IP"
          value={cidr}
          onChange={(e) => setCidr(e.target.value)}
          size="small"
          fullWidth
          placeholder="e.g. 192.168.1.0/24 or 10.0.0.1"
          helperText="IPv4 or IPv6 CIDR block. Bare IPs become /32."
        />
        <TextField
          label="Description (optional)"
          value={description}
          onChange={(e) => setDescription(e.target.value)}
          size="small"
          fullWidth
          placeholder="e.g. Head office LAN"
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={handleClose}>Cancel</Button>
        <Button
          variant="contained"
          onClick={() => mutate({ cidr: cidr.trim(), description: description.trim() || undefined })}
          disabled={!cidr.trim() || isPending}
        >
          Add
        </Button>
      </DialogActions>
    </Dialog>
  )
}

export default function IpAllowlist() {
  const queryClient = useQueryClient()
  const [addOpen, setAddOpen] = useState(false)

  const { data: rules = [], isLoading } = useQuery({
    queryKey: ['ip-allowlist'],
    queryFn: listIpRules,
  })

  const { mutate: remove } = useMutation({
    mutationFn: deleteIpRule,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ip-allowlist'] }),
  })

  const { mutate: toggle } = useMutation({
    mutationFn: toggleIpRule,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['ip-allowlist'] }),
  })

  const activeCount = rules.filter((r) => r.is_active).length

  return (
    <PermissionGuard permission="iplist:manage">
      <Box sx={{ p: 3 }}>
        <Stack direction="row" alignItems="center" justifyContent="space-between" mb={3}>
          <Stack direction="row" alignItems="center" gap={1.5}>
            <LanIcon sx={{ color: 'primary.main' }} />
            <Box>
              <Typography variant="h6" fontWeight={700}>IP Allowlist</Typography>
              <Typography variant="caption" color="text.secondary">
                Restrict tenant access to specific IP ranges
              </Typography>
            </Box>
          </Stack>
          <Button
            variant="contained"
            startIcon={<AddIcon />}
            size="small"
            onClick={() => setAddOpen(true)}
          >
            Add Rule
          </Button>
        </Stack>

        {activeCount > 0 && (
          <Alert severity="info" sx={{ mb: 2 }}>
            <strong>{activeCount} active rule{activeCount > 1 ? 's' : ''}</strong> — only clients
            matching a listed CIDR can authenticate for this tenant.
            IPs that do not match any active rule receive <strong>403 Forbidden</strong>.
          </Alert>
        )}

        {rules.length === 0 && !isLoading && (
          <Alert severity="success" sx={{ mb: 2 }}>
            No active rules — all client IPs are currently allowed (default open).
          </Alert>
        )}

        <GlassCard>
          <TableContainer>
            <Table size="small">
              <TableHead>
                <TableRow>
                  <TableCell>CIDR</TableCell>
                  <TableCell>Description</TableCell>
                  <TableCell>Status</TableCell>
                  <TableCell>Added by</TableCell>
                  <TableCell>Added at</TableCell>
                  <TableCell align="right">Actions</TableCell>
                </TableRow>
              </TableHead>
              <TableBody>
                {isLoading
                  ? Array.from({ length: 3 }).map((_, i) => (
                      <TableRow key={i}>
                        {Array.from({ length: 6 }).map((_, j) => (
                          <TableCell key={j}><Skeleton /></TableCell>
                        ))}
                      </TableRow>
                    ))
                  : rules.length === 0
                    ? (
                      <TableRow>
                        <TableCell colSpan={6} align="center" sx={{ py: 4, color: 'text.secondary' }}>
                          No IP rules defined. Add a CIDR to restrict access.
                        </TableCell>
                      </TableRow>
                    )
                    : rules.map((rule) => (
                      <TableRow key={rule.id} hover sx={{ opacity: rule.is_active ? 1 : 0.5 }}>
                        <TableCell>
                          <Typography variant="body2" fontFamily="monospace" fontWeight={600}>
                            {rule.cidr}
                          </Typography>
                        </TableCell>
                        <TableCell sx={{ color: 'text.secondary', fontSize: '0.8rem' }}>
                          {rule.description ?? '—'}
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={rule.is_active ? 'Active' : 'Disabled'}
                            size="small"
                            color={rule.is_active ? 'success' : 'default'}
                          />
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.78rem', color: 'text.secondary' }}>
                          {rule.created_by_email ?? '—'}
                        </TableCell>
                        <TableCell sx={{ fontSize: '0.78rem', color: 'text.secondary' }}>
                          {fmtDate(rule.created_at)}
                        </TableCell>
                        <TableCell align="right">
                          <Stack direction="row" justifyContent="flex-end" gap={0.5}>
                            <Tooltip title={rule.is_active ? 'Disable rule' : 'Enable rule'}>
                              <IconButton
                                size="small"
                                color={rule.is_active ? 'warning' : 'success'}
                                onClick={() => toggle(rule.id)}
                              >
                                {rule.is_active
                                  ? <ToggleOffIcon fontSize="small" />
                                  : <ToggleOnIcon fontSize="small" />
                                }
                              </IconButton>
                            </Tooltip>
                            <Tooltip title="Delete rule">
                              <IconButton
                                size="small"
                                color="error"
                                onClick={() => {
                                  if (confirm(`Delete rule for "${rule.cidr}"?`)) {
                                    remove(rule.id)
                                  }
                                }}
                              >
                                <DeleteIcon fontSize="small" />
                              </IconButton>
                            </Tooltip>
                          </Stack>
                        </TableCell>
                      </TableRow>
                    ))}
              </TableBody>
            </Table>
          </TableContainer>
        </GlassCard>

        <AddRuleDialog open={addOpen} onClose={() => setAddOpen(false)} />
      </Box>
    </PermissionGuard>
  )
}

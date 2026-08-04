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
  Skeleton,
  Paper,
  Button,
  Alert,
  CircularProgress,
  Tooltip,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import VerifiedIcon from '@mui/icons-material/Verified'
import GppBadIcon from '@mui/icons-material/GppBad'
import { useQuery, useMutation } from '@tanstack/react-query'
import { GlassCard } from '@/components/common/GlassCard'
import { getAuditLogs, verifyAuditChain, type VerifyResult } from '@/api/audit'
import { usePermission } from '@/hooks/usePermission'

function VerifyBanner({ result }: { result: VerifyResult }) {
  if (result.verified) {
    return (
      <Alert
        severity="success"
        icon={<VerifiedIcon />}
        sx={{ mb: 2 }}
      >
        <strong>Chain verified.</strong> {result.total_checked} signed rows checked — no tampering detected.
      </Alert>
    )
  }
  return (
    <Alert severity="error" icon={<GppBadIcon />} sx={{ mb: 2 }}>
      <strong>Tamper detected!</strong>{' '}
      {result.tampered_count} row{result.tampered_count !== 1 ? 's' : ''} with mismatched
      hash{result.chain_broken ? '; chain continuity also broken (rows may have been deleted)' : ''}.
      {result.tampered_ids.length > 0 && (
        <Box mt={0.5}>
          <Typography variant="caption" fontFamily="monospace">
            Affected IDs: {result.tampered_ids.join(', ')}
          </Typography>
        </Box>
      )}
    </Alert>
  )
}

export default function Audit() {
  const canVerify = usePermission('audit:verify')
  const [verifyResult, setVerifyResult] = useState<VerifyResult | null>(null)

  const { data: logs, isLoading } = useQuery({
    queryKey: ['audit-logs'],
    queryFn: () => getAuditLogs(200),
  })

  const { mutate: verify, isPending: isVerifying } = useMutation({
    mutationFn: () => verifyAuditChain(500),
    onSuccess: setVerifyResult,
  })

  return (
    <Box>
      <Stack direction="row" alignItems="center" justifyContent="space-between" mb={2}>
        <Typography variant="h6" fontWeight={700}>Audit Logs</Typography>
        {canVerify && (
          <Tooltip title="Re-compute HMAC hashes for all signed rows and report any tampering">
            <span>
              <Button
                variant="outlined"
                size="small"
                startIcon={isVerifying ? <CircularProgress size={14} /> : <VerifiedIcon />}
                onClick={() => verify()}
                disabled={isVerifying}
              >
                Verify Chain
              </Button>
            </span>
          </Tooltip>
        )}
      </Stack>

      {verifyResult && <VerifyBanner result={verifyResult} />}

      <GlassCard>
        <TableContainer component={Paper} elevation={0} sx={{ background: 'transparent' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Action</TableCell>
                <TableCell>Resource</TableCell>
                <TableCell>Resource ID</TableCell>
                <TableCell>User</TableCell>
                <TableCell>IP</TableCell>
                <TableCell>Signed</TableCell>
                <TableCell>Time</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {isLoading
                ? Array.from({ length: 10 }).map((_, i) => (
                    <TableRow key={i}>
                      {Array.from({ length: 7 }).map((__, j) => (
                        <TableCell key={j}><Skeleton /></TableCell>
                      ))}
                    </TableRow>
                  ))
                : !logs?.items?.length
                ? (
                    <TableRow>
                      <TableCell colSpan={7} align="center" sx={{ py: 4 }}>
                        <Typography color="text.secondary">No audit logs</Typography>
                      </TableCell>
                    </TableRow>
                  )
                : logs?.items?.map((log) => (
                    <TableRow
                      key={log.id}
                      hover
                      sx={
                        verifyResult && !verifyResult.verified && verifyResult.tampered_ids.includes(log.id)
                          ? { background: 'rgba(255,69,96,0.08)' }
                          : {}
                      }
                    >
                      <TableCell>
                        <Chip label={log.action} size="small" variant="outlined" />
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {log.resource_type ?? '—'}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        {log.resource_id ? (
                          <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                            {log.resource_id.slice(0, 8)}…
                          </Typography>
                        ) : '—'}
                      </TableCell>
                      <TableCell>
                        {log.user_id ? (
                          <Typography variant="caption" sx={{ fontFamily: 'monospace' }}>
                            {log.user_id.slice(0, 8)}…
                          </Typography>
                        ) : (
                          <Chip label="system" size="small" variant="outlined" color="secondary" />
                        )}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {log.ip_address ?? '—'}
                        </Typography>
                      </TableCell>
                      <TableCell>
                        <Tooltip title={(log as { has_hash?: boolean }).has_hash ? 'HMAC signed' : 'Legacy row (no hash)'}>
                          <span>
                            {(log as { has_hash?: boolean }).has_hash
                              ? <VerifiedIcon sx={{ fontSize: 14, color: 'success.main' }} />
                              : <Typography variant="caption" color="text.disabled">—</Typography>
                            }
                          </span>
                        </Tooltip>
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">
                          {new Date(log.created_at).toLocaleString()}
                        </Typography>
                      </TableCell>
                    </TableRow>
                  ))}
            </TableBody>
          </Table>
        </TableContainer>
      </GlassCard>
    </Box>
  )
}

/**
 * How the business is moving, as opposed to where it stands.
 *
 * The dashboard answers "how many customers do I have". This answers "is that
 * going up", which is the only version of the question anybody acts on. Every
 * figure comes from the nightly snapshots, so on a fresh installation the
 * charts are honestly empty rather than drawing a flat line through one point
 * and calling it a trend.
 *
 * MODULE ADOPTION (§22) is here because it is a product decision dressed as a
 * statistic: a module nobody switched on is priced wrong, hard to find, or not
 * worth building further, and the only way to tell is to look at how many
 * customers took it.
 *
 * TENANT HEALTH (§23) is sorted worst first. It is a work queue, not a
 * leaderboard — the customer at 38% is the one somebody should telephone this
 * week, and putting the happiest at the top would bury them.
 *
 * No charting library. The existing Analytics page draws its bars with
 * LinearProgress and a hand-rolled trend, and one convention is worth more
 * than a slightly nicer curve.
 */
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import {
  Alert, Box, Chip, LinearProgress, Skeleton, Table, TableBody, TableCell,
  TableContainer, TableHead, TableRow, Tooltip, Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import {
  getModuleAdoption, getPlatformGrowth, getTenantHealth,
} from '@/api/platform'

function gb(bytes: number) {
  return `${(bytes / 1024 ** 3).toFixed(1)} GB`
}

/** Green above 70, amber above 40, red below. A score is a prompt to act, and
 *  three bands is as much nuance as anybody uses when deciding whether to
 *  telephone somebody. */
function healthColour(score: number) {
  return score >= 70 ? '#00D9C0' : score >= 40 ? '#FFB020' : '#FF4560'
}

/** A bar chart drawn from divs, matching the existing Analytics page rather
 *  than pulling in a charting library for six series. */
function TrendBars({ points, label, colour = '#6C63FF' }: {
  points: { day: string; value: number }[]
  label: string
  colour?: string
}) {
  const max = Math.max(...points.map((p) => p.value), 1)
  return (
    <Box>
      <Typography variant="caption" color="text.secondary">{label}</Typography>
      <Stack direction="row" spacing={0.3} alignItems="flex-end"
             sx={{ height: 64, mt: 0.5 }}>
        {points.map((p) => (
          <Tooltip key={p.day} title={`${p.day}: ${p.value.toLocaleString()}`}>
            <Box
              sx={{
                flex: 1, minWidth: 2, borderRadius: '2px 2px 0 0',
                height: `${Math.max(3, (p.value / max) * 100)}%`,
                bgcolor: colour, opacity: 0.75,
                transition: 'height 0.4s ease',
              }}
            />
          </Tooltip>
        ))}
      </Stack>
    </Box>
  )
}

export default function PlatformAnalytics() {
  const navigate = useNavigate()

  const { data: growth, isLoading } = useQuery({
    queryKey: ['platform-growth'], queryFn: () => getPlatformGrowth(90),
  })
  const { data: adoption } = useQuery({
    queryKey: ['platform-adoption'], queryFn: getModuleAdoption,
  })
  const { data: health } = useQuery({
    queryKey: ['platform-health-scores'], queryFn: getTenantHealth,
  })

  const points = growth ?? []
  const latest = points[points.length - 1]
  const first = points[0]

  /** Change over the window, which is the only reason to plot anything. */
  const delta = (key: 'tenants' | 'users' | 'cameras') =>
    first && latest ? Number(latest[key]) - Number(first[key]) : 0

  return (
    <Box>
      <PageHeader
        title="Growth & Adoption"
        subtitle="Where the business is going, from the nightly snapshots"
      />

      {isLoading ? (
        <Skeleton variant="rectangular" height={200} sx={{ mb: 2 }} />
      ) : points.length < 2 ? (
        // Honest rather than decorative: one point is not a trend, and drawing
        // a flat line through it would imply a stability nobody measured.
        <Alert severity="info" sx={{ mb: 2 }}>
          {points.length === 0
            ? 'No snapshots yet. The nightly rollup records one per customer per day; charts appear once there are a few.'
            : 'Only one day of history so far. Trends appear once the rollup has run a few more nights.'}
        </Alert>
      ) : (
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" spacing={3} sx={{ flexWrap: 'wrap', gap: 3 }}>
            <Box sx={{ flex: '1 1 240px' }}>
              <TrendBars
                label={`Customers (${delta('tenants') >= 0 ? '+' : ''}${delta('tenants')} over ${points.length} days)`}
                points={points.map((p) => ({ day: p.day, value: Number(p.tenants) }))}
              />
            </Box>
            <Box sx={{ flex: '1 1 240px' }}>
              <TrendBars
                label={`Users (${delta('users') >= 0 ? '+' : ''}${delta('users')})`}
                points={points.map((p) => ({ day: p.day, value: Number(p.users) }))}
                colour="#00D9C0"
              />
            </Box>
            <Box sx={{ flex: '1 1 240px' }}>
              <TrendBars
                label={`Cameras (${delta('cameras') >= 0 ? '+' : ''}${delta('cameras')})`}
                points={points.map((p) => ({ day: p.day, value: Number(p.cameras) }))}
                colour="#8B85FF"
              />
            </Box>
            <Box sx={{ flex: '1 1 240px' }}>
              <TrendBars
                label={`Storage (${latest ? gb(Number(latest.storage_bytes)) : '—'})`}
                points={points.map((p) => ({ day: p.day,
                                             value: Number(p.storage_bytes) }))}
                colour="#FFB020"
              />
            </Box>
          </Stack>
        </GlassCard>
      )}

      <Stack direction="row" spacing={2} sx={{ flexWrap: 'wrap', gap: 2 }}>
        <GlassCard sx={{ p: 2, flex: '1 1 380px' }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 600, mb: 0.5 }}>
            Module adoption
          </Typography>
          <Typography variant="caption" color="text.secondary">
            A module nobody switched on is priced wrong, hard to find, or not
            worth building further
          </Typography>
          <Box sx={{ mt: 1.5 }}>
            {!adoption ? <Skeleton height={160} /> : adoption.modules.map((m) => (
              <Box key={m.code} sx={{ mb: 1 }}>
                <Stack direction="row" sx={{ justifyContent: 'space-between' }}>
                  <Typography variant="caption">{m.name}</Typography>
                  <Typography variant="caption" color="text.secondary">
                    {m.tenants} of {adoption.customers} · {m.adoption_percent}%
                  </Typography>
                </Stack>
                <LinearProgress
                  variant="determinate" value={m.adoption_percent}
                  sx={{ height: 5, borderRadius: 3,
                        bgcolor: 'rgba(255,255,255,0.06)',
                        '& .MuiLinearProgress-bar': {
                          bgcolor: m.adoption_percent >= 50 ? '#00D9C0' : '#6C63FF',
                        } }}
                />
              </Box>
            ))}
          </Box>
        </GlassCard>

        <GlassCard sx={{ p: 0, flex: '1 1 420px' }}>
          <Box sx={{ px: 2, pt: 2, pb: 1 }}>
            <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>
              Customer health
            </Typography>
            <Typography variant="caption" color="text.secondary">
              Worst first — this is a work queue, not a leaderboard
            </Typography>
          </Box>
          {!health ? <Skeleton height={180} /> : (
            <TableContainer sx={{ overflowX: 'auto' }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Customer</TableCell>
                    <TableCell align="right">Login</TableCell>
                    <TableCell align="right">Usage</TableCell>
                    <TableCell align="right">Billing</TableCell>
                    <TableCell align="right">Adoption</TableCell>
                    <TableCell align="right">Score</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {health.map((h) => (
                    <TableRow
                      key={h.tenant_id} hover sx={{ cursor: 'pointer' }}
                      onClick={() => navigate(`/platform/tenants/${h.tenant_id}`)}
                    >
                      <TableCell>{h.tenant_name}</TableCell>
                      {/* The parts, not just the total: 62% because nobody has
                          logged in is a different conversation from 62%
                          because they are three invoices behind. */}
                      <TableCell align="right">
                        <Typography variant="caption">{h.login_score}</Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Typography variant="caption">{h.usage_score}</Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Typography variant="caption">{h.billing_score}</Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Typography variant="caption">{h.adoption_score}</Typography>
                      </TableCell>
                      <TableCell align="right">
                        <Chip
                          label={`${h.score}%`} size="small"
                          sx={{ height: 20, fontSize: '0.65rem', fontWeight: 700,
                                color: healthColour(h.score),
                                bgcolor: `${healthColour(h.score)}22` }}
                        />
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </GlassCard>
      </Stack>
    </Box>
  )
}

/**
 * The bar that says you are standing in someone else's tenant.
 *
 * A support session makes every page in the app show a customer's data. Without
 * something loud and permanent on screen, the only way to tell is to recognise
 * the site names — which is exactly the mistake worth preventing: a change made
 * in the wrong tenant because it looked like your own.
 *
 * So it is red, it is at the top of every page, it names the tenant, it counts
 * down, and leaving is one click away. Ending the session revokes access on the
 * server too; this is not a client-side pretence.
 */
import { useEffect, useState } from 'react'
import { Box, Button, Typography } from '@mui/material'
import Stack from '@/components/common/Stack'
import SupportAgentIcon from '@mui/icons-material/SupportAgent'
import { endSupportSession } from '@/api/supportSessions'
import { useAuthStore } from '@/store/auth'

function remaining(expiresAt: string) {
  const ms = new Date(expiresAt).getTime() - Date.now()
  if (ms <= 0) return 'expired'
  const total = Math.floor(ms / 1000)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}`
}

export function SupportSessionBanner() {
  const session = useAuthStore((s) => s.supportSession)
  const exitSupportSession = useAuthStore((s) => s.exitSupportSession)
  const [left, setLeft] = useState('')
  const [leaving, setLeaving] = useState(false)

  useEffect(() => {
    if (!session) return
    const tick = () => setLeft(remaining(session.expiresAt))
    tick()
    const timer = setInterval(tick, 1000)
    return () => clearInterval(timer)
  }, [session])

  if (!session) return null

  async function leave() {
    setLeaving(true)
    try {
      // Told to the server first, so the token is dead even if this tab is
      // closed before the state below settles.
      await endSupportSession(session!.id)
    } catch {
      // Already ended or expired server-side — leaving locally is still right.
    }
    await exitSupportSession()
    setLeaving(false)
  }

  return (
    <Box
      sx={{
        px: 2, py: 0.75,
        bgcolor: 'rgba(211,47,47,0.16)',
        borderBottom: '1px solid rgba(211,47,47,0.45)',
      }}
    >
      <Stack
        direction="row" spacing={1.5} alignItems="center"
        sx={{ flexWrap: 'wrap', gap: 1 }}
      >
        <SupportAgentIcon fontSize="small" sx={{ color: '#ff6b6b' }} />
        <Typography variant="body2" sx={{ color: '#ff9a9a', fontWeight: 600 }}>
          Support session — you are{' '}
          {session.accessLevel === 'elevated' ? 'working in' : 'viewing'}{' '}
          {session.tenantName}
        </Typography>
        {/* An operator who thinks they can edit and cannot is confused; one who
            thinks they cannot and can is dangerous. So it says which. */}
        <Typography
          variant="caption"
          sx={{ px: 0.8, py: 0.1, borderRadius: 0.5, fontWeight: 700,
                color: session.accessLevel === 'elevated' ? '#FFB020' : '#ff9a9a',
                bgcolor: session.accessLevel === 'elevated'
                  ? 'rgba(255,176,32,0.18)' : 'rgba(255,255,255,0.08)' }}
        >
          {session.accessLevel === 'elevated' ? 'ELEVATED' : 'READ ONLY'}
        </Typography>
        <Typography variant="caption" sx={{ color: '#ff9a9a', opacity: 0.85 }}>
          {left === 'expired' ? 'expired' : `ends in ${left}`}
        </Typography>
        <Box sx={{ flex: 1 }} />
        <Button
          size="small" variant="outlined" color="error"
          disabled={leaving} onClick={leave}
        >
          Leave now
        </Button>
      </Stack>
    </Box>
  )
}

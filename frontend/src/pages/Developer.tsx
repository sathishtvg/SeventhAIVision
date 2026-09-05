import {
  Box,
  Typography,
  Button,
  Chip,
  Divider,
  Tab,
  Tabs,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  Paper,
  CircularProgress,
  Alert,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import OpenInNewIcon from '@mui/icons-material/OpenInNew'
import CodeIcon from '@mui/icons-material/Code'
import ApiIcon from '@mui/icons-material/Api'
import BackupIcon from '@mui/icons-material/Backup'
import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { apiClient } from '@/api/client'
import { GlassCard } from '@/components/common/GlassCard'

const BASE = '/api/v1'

const ENDPOINTS = [
  { method: 'POST', path: '/auth/login',        tag: 'auth',        desc: 'Obtain access + refresh tokens' },
  { method: 'POST', path: '/auth/refresh',       tag: 'auth',        desc: 'Rotate access token' },
  { method: 'GET',  path: '/cameras',            tag: 'cameras',     desc: 'List cameras' },
  { method: 'GET',  path: '/alerts',             tag: 'alerts',      desc: 'List alerts (filters: status, severity, site_id)' },
  { method: 'GET',  path: '/incidents',          tag: 'incidents',   desc: 'List incidents' },
  { method: 'GET',  path: '/analytics/summary',  tag: 'analytics',   desc: 'Detection counts by module' },
  { method: 'GET',  path: '/system/health',      tag: 'system',      desc: 'Per-service health status' },
  { method: 'WS',   path: '/ws/live?token=...',  tag: 'realtime',    desc: 'Real-time event push' },
]

const METHOD_COLORS: Record<string, string> = {
  GET:  '#22C55E', POST: '#6C63FF', PUT: '#F59E0B',
  DELETE: '#FF4560', PATCH: '#00D9C0', WS: '#38BDF8',
}

const CURL_EXAMPLE = `# 1. Login
curl -s -X POST https://your-host/api/v1/auth/login \\
  -H "Content-Type: application/json" \\
  -d '{"email":"admin@acme.com","password":"secret","tenant_slug":"acme"}' \\
  | jq .access_token

# 2. List open alerts (replace $TOKEN with access token above)
curl -s https://your-host/api/v1/alerts?status=open \\
  -H "Authorization: Bearer $TOKEN" | jq .`

const PYTHON_EXAMPLE = `import httpx

BASE = "https://your-host/api/v1"

# 1. Login
resp = httpx.post(f"{BASE}/auth/login", json={
    "email": "admin@acme.com",
    "password": "secret",
    "tenant_slug": "acme",
})
token = resp.json()["access_token"]
headers = {"Authorization": f"Bearer {token}"}

# 2. List open alerts
alerts = httpx.get(f"{BASE}/alerts", params={"status": "open"}, headers=headers)
for alert in alerts.json()["items"]:
    print(alert["title"], alert["severity"])`

const JS_EXAMPLE = `const BASE = "https://your-host/api/v1";

// 1. Login
const { access_token } = await fetch(\`\${BASE}/auth/login\`, {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ email: "admin@acme.com", password: "secret", tenant_slug: "acme" }),
}).then(r => r.json());

// 2. List open alerts
const { items } = await fetch(\`\${BASE}/alerts?status=open\`, {
  headers: { Authorization: \`Bearer \${access_token}\` },
}).then(r => r.json());

// 3. Real-time WebSocket push
const ws = new WebSocket(\`wss://your-host/ws/live?token=\${access_token}\`);
ws.onmessage = (e) => {
  const event = JSON.parse(e.data);  // { event_type, payload, occurred_at }
  console.log(event.event_type, event.payload);
};`

function BackupsPanel() {
  const qc = useQueryClient()
  const { data, isLoading, error } = useQuery({
    queryKey: ['system-backups'],
    queryFn: () => apiClient.get<{ backups: { filename: string; size_bytes: number; created_at: string }[] }>(
      '/api/v1/system/backups'
    ).then(r => r.data),
    refetchInterval: 10_000,
  })
  const trigger = useMutation({
    mutationFn: () => apiClient.post('/api/v1/system/backup').then(r => r.data),
    onSuccess: () => setTimeout(() => qc.invalidateQueries({ queryKey: ['system-backups'] }), 5000),
  })

  function fmtBytes(b: number) {
    if (b >= 1024 * 1024) return `${(b / 1024 / 1024).toFixed(1)} MB`
    if (b >= 1024) return `${(b / 1024).toFixed(0)} KB`
    return `${b} B`
  }

  return (
    <GlassCard sx={{ p: 2.5 }}>
      <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', mb: 2 }}>
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
          <BackupIcon sx={{ fontSize: 18, color: 'secondary.main' }} />
          <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Database Backups</Typography>
        </Box>
        <Button
          size="small"
          variant="outlined"
          color="secondary"
          onClick={() => trigger.mutate()}
          disabled={trigger.isPending}
          startIcon={trigger.isPending ? <CircularProgress size={14} /> : <BackupIcon />}
        >
          {trigger.isPending ? 'Running…' : 'Run Now'}
        </Button>
      </Box>

      {trigger.isSuccess && (
        <Alert severity="success" sx={{ mb: 1.5, py: 0.5 }}>
          Backup started — refresh list in a few seconds.
        </Alert>
      )}
      {error && (
        <Alert severity="error" sx={{ mb: 1.5, py: 0.5 }}>
          Failed to load backup list.
        </Alert>
      )}

      {isLoading ? (
        <Box sx={{ display: 'flex', justifyContent: 'center', py: 2 }}>
          <CircularProgress size={24} />
        </Box>
      ) : (data?.backups?.length ?? 0) === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ textAlign: 'center', py: 2 }}>
          No backups yet — the scheduler runs daily, or click Run Now.
        </Typography>
      ) : (
        <TableContainer component={Paper} elevation={0} sx={{ bgcolor: 'transparent' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Filename</TableCell>
                <TableCell align="right">Size</TableCell>
                <TableCell>Created</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {data!.backups.map((b) => (
                <TableRow key={b.filename}>
                  <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.75rem' }}>{b.filename}</TableCell>
                  <TableCell align="right">
                    <Typography variant="caption" color="text.secondary">{fmtBytes(b.size_bytes)}</Typography>
                  </TableCell>
                  <TableCell>
                    <Typography variant="caption" color="text.secondary">
                      {new Date(b.created_at).toLocaleString()}
                    </Typography>
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </TableContainer>
      )}
      <Typography variant="caption" color="text.secondary" sx={{ mt: 1.5, display: 'block' }}>
        Backups stored in the <code>backups_data</code> Docker volume. See <code>docs/DR-RUNBOOK.md</code> for restore procedure.
      </Typography>
    </GlassCard>
  )
}

function CodeBlock({ code }: { code: string }) {
  return (
    <Box
      component="pre"
      sx={{
        m: 0, p: 2,
        background: 'rgba(0,0,0,0.45)',
        border: '1px solid rgba(255,255,255,0.10)',
        borderRadius: 2,
        fontSize: '0.78rem',
        fontFamily: '"Fira Code", monospace',
        color: '#E2E8F0',
        overflowX: 'auto',
        lineHeight: 1.65,
        whiteSpace: 'pre',
      }}
    >
      {code}
    </Box>
  )
}

export default function Developer() {
  const [tab, setTab] = useState(0)

  const swaggerUrl = `${window.location.origin}/api/v1/docs`
  const redocUrl   = `${window.location.origin}/api/v1/redoc`

  return (
    <Box>
      <Box sx={{ mb: 3, display: 'flex', alignItems: 'center', gap: 1.5 }}>
        <ApiIcon sx={{ fontSize: 28, color: 'primary.main' }} />
        <Typography variant="h5" sx={{ fontWeight: 700 }}>Developer Portal</Typography>
      </Box>

      {/* Quick links */}
      <GlassCard sx={{ p: 2.5, mb: 3 }}>
        <Typography variant="subtitle2" color="text.secondary" gutterBottom>
          Interactive API Reference
        </Typography>
        <Stack direction="row" spacing={2} flexWrap="wrap">
          <Button
            variant="contained"
            size="small"
            startIcon={<OpenInNewIcon />}
            href={swaggerUrl}
            target="_blank"
            rel="noopener"
          >
            Swagger UI
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<OpenInNewIcon />}
            href={redocUrl}
            target="_blank"
            rel="noopener"
          >
            ReDoc
          </Button>
          <Button
            variant="outlined"
            size="small"
            startIcon={<OpenInNewIcon />}
            href={`${window.location.origin}/api/v1/openapi.json`}
            target="_blank"
            rel="noopener"
          >
            OpenAPI JSON
          </Button>
        </Stack>
      </GlassCard>

      {/* Backup panel */}
      <BackupsPanel />

      <Stack direction={{ xs: 'column', lg: 'row' }} spacing={3} sx={{ mt: 3 }}>
        {/* Left column */}
        <Stack spacing={3} sx={{ flex: 1, minWidth: 0 }}>
          {/* Auth quick-ref */}
          <GlassCard sx={{ p: 2.5 }}>
            <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 700 }}>
              Authentication
            </Typography>
            <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
              All endpoints require a JWT Bearer token obtained via <code>/api/v1/auth/login</code>.
              Access tokens expire in <strong>15 minutes</strong>. Use <code>/api/v1/auth/refresh</code> to rotate.
              For machine-to-machine integrations, create an API key under{' '}
              <strong>Settings → API Keys</strong> — these are long-lived and scoped to specific permissions.
            </Typography>
            <Stack spacing={1}>
              {[
                ['POST', '/api/v1/auth/login',   'email + password + tenant_slug → tokens'],
                ['POST', '/api/v1/auth/refresh',  'refresh_token → new access_token'],
                ['POST', '/api/v1/auth/logout',   'Revoke current refresh token'],
              ].map(([m, p, d]) => (
                <Box key={p} sx={{ display: 'flex', alignItems: 'center', gap: 1, flexWrap: 'wrap' }}>
                  <Chip
                    label={m}
                    size="small"
                    sx={{ fontFamily: 'monospace', fontSize: '0.7rem',
                          bgcolor: `${METHOD_COLORS[m]}22`, color: METHOD_COLORS[m],
                          border: `1px solid ${METHOD_COLORS[m]}55`, fontWeight: 700 }}
                  />
                  <Typography variant="caption" color="text.primary" sx={{ fontFamily: "monospace" }}>{p}</Typography>
                  <Typography variant="caption" color="text.secondary">— {d}</Typography>
                </Box>
              ))}
            </Stack>
          </GlassCard>

          {/* Key endpoints */}
          <GlassCard sx={{ p: 2.5 }}>
            <Typography variant="subtitle1" gutterBottom sx={{ fontWeight: 700 }}>Key Endpoints</Typography>
            <TableContainer component={Paper} elevation={0} sx={{ bgcolor: 'transparent' }}>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell sx={{ width: 64 }}>Method</TableCell>
                    <TableCell>Path</TableCell>
                    <TableCell>Description</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {ENDPOINTS.map((e) => (
                    <TableRow key={e.path}>
                      <TableCell>
                        <Chip
                          label={e.method}
                          size="small"
                          sx={{ fontFamily: 'monospace', fontSize: '0.68rem',
                                bgcolor: `${METHOD_COLORS[e.method]}22`, color: METHOD_COLORS[e.method],
                                border: `1px solid ${METHOD_COLORS[e.method]}55`, fontWeight: 700 }}
                        />
                      </TableCell>
                      <TableCell sx={{ fontFamily: 'monospace', fontSize: '0.78rem' }}>
                        {BASE}{e.path}
                      </TableCell>
                      <TableCell>
                        <Typography variant="caption" color="text.secondary">{e.desc}</Typography>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          </GlassCard>
        </Stack>

        {/* Right column — code examples */}
        <GlassCard sx={{ p: 2.5, flex: 1, minWidth: 0 }}>
          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, mb: 2 }}>
            <CodeIcon sx={{ fontSize: 18, color: 'primary.main' }} />
            <Typography variant="subtitle1" sx={{ fontWeight: 700 }}>Quick Start</Typography>
          </Box>

          <Tabs
            value={tab}
            onChange={(_, v) => setTab(v)}
            sx={{ mb: 2, borderBottom: 1, borderColor: 'divider' }}
          >
            <Tab label="cURL" sx={{ fontSize: '0.8rem', minHeight: 36 }} />
            <Tab label="Python" sx={{ fontSize: '0.8rem', minHeight: 36 }} />
            <Tab label="JavaScript" sx={{ fontSize: '0.8rem', minHeight: 36 }} />
          </Tabs>

          {tab === 0 && <CodeBlock code={CURL_EXAMPLE} />}
          {tab === 1 && <CodeBlock code={PYTHON_EXAMPLE} />}
          {tab === 2 && <CodeBlock code={JS_EXAMPLE} />}

          <Divider sx={{ my: 2 }} />

          <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>
            Rate Limits
          </Typography>
          <Stack spacing={0.5}>
            {[
              ['Auth endpoints (/login, /refresh)', '5 req / min per IP'],
              ['All other endpoints', '100 req / min per IP'],
              ['Exceeding limit', '429 Too Many Requests'],
            ].map(([k, v]) => (
              <Box key={k} sx={{ display: 'flex', justifyContent: 'space-between', gap: 2 }}>
                <Typography variant="caption" color="text.secondary">{k}</Typography>
                <Typography variant="caption" color="text.primary" sx={{ textAlign: 'right', fontFamily: "monospace" }}>{v}</Typography>
              </Box>
            ))}
          </Stack>

          <Divider sx={{ my: 2 }} />

          <Typography variant="subtitle2" gutterBottom sx={{ fontWeight: 700 }}>
            WebSocket Push
          </Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
            Connect to <code style={{ fontSize: '0.78rem' }}>wss://host/ws/live?token=&lt;access_token&gt;</code> to
            receive real-time events without polling.
          </Typography>
          <Stack spacing={0.5}>
            {[
              ['alert_created',          'A new alert was generated by an AI worker'],
              ['incident_created',       'An incident was auto-created or manually opened'],
              ['camera_status_changed',  'A camera went online, degraded, or offline'],
            ].map(([evt, desc]) => (
              <Box key={evt} sx={{ display: 'flex', gap: 1, alignItems: 'flex-start' }}>
                <Chip label={evt} size="small"
                  sx={{ fontFamily: 'monospace', fontSize: '0.68rem', fontWeight: 700,
                        bgcolor: 'rgba(56,189,248,0.12)', color: '#38BDF8',
                        border: '1px solid rgba(56,189,248,0.30)' }} />
                <Typography variant="caption" color="text.secondary" sx={{ pt: 0.3 }}>{desc}</Typography>
              </Box>
            ))}
          </Stack>
        </GlassCard>
      </Stack>
    </Box>
  )
}

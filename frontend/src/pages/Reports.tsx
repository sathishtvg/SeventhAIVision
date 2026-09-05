import { useState } from 'react'
import {
  Box, Tab, Tabs, Typography, Button, Paper, Table, TableBody, TableCell,
  TableHead, TableRow, Chip, Dialog, DialogTitle, DialogContent, DialogActions,
  TextField, MenuItem, CircularProgress, Alert, Grid, Checkbox, FormControlLabel,
  List, ListItem, Divider,
} from '@mui/material'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import {
  downloadSiteSummaryReport, downloadDobReport, downloadIncidentReport,
  getDsars, createDsar, updateDsar, executeDsarErasure, type ErasureExecuteResult,
} from '@/api/reports'
import { getSites } from '@/api/sites'
import { getFaceWatchlist, getPlateWatchlist } from '@/api/watchlist'
import { listVisitors } from '@/api/visitors'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'

export default function Reports() {
  const [tab, setTab] = useState(0)
  return (
    <Box>
      <PageHeader pageKey="reports" />
      <Paper sx={{ mb: 2 }}>
        <Tabs value={tab} onChange={(_, v) => setTab(v)} textColor="inherit" indicatorColor="primary">
          <Tab label="PDF Reports" />
          <Tab label="PDPA / DSAR" />
        </Tabs>
      </Paper>
      {tab === 0 && <PdfReportsTab />}
      {tab === 1 && <DsarTab />}
    </Box>
  )
}

// ── PDF Reports ─────────────────────────────────────────────────────────────

function PdfReportsTab() {
  const today = new Date().toISOString().split('T')[0]
  const sevenDaysAgo = new Date(Date.now() - 7 * 86400000).toISOString().split('T')[0]

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })

  const [siteForm, setSiteForm] = useState({ site_id: '', date_from: sevenDaysAgo, date_until: today })
  const [dobForm, setDobForm] = useState({ date_from: today, date_until: today, site_id: '' })
  const [incidentId, setIncidentId] = useState('')
  const [downloading, setDownloading] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const handleDownload = async (type: string, fn: () => Promise<void>) => {
    setDownloading(type)
    setError(null)
    try {
      await fn()
    } catch (e: any) {
      setError(e?.response?.data?.detail || e?.message || 'Download failed')
    } finally {
      setDownloading(null)
    }
  }

  return (
    <Box sx={{ display: 'flex', flexDirection: 'column', gap: 3 }}>
      {error && <Alert severity="error" onClose={() => setError(null)}>{error}</Alert>}

      {/* Site Summary Report */}
      <GlassCard sx={{ p: 3 }}>
        <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>Site Summary Report</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
          Alerts, incidents, and camera stats for a site over a date range.
        </Typography>
        <Grid container spacing={2} sx={{ alignItems: "flex-end" }}>
          <Grid size={{ xs: 12, sm: 4 }}>
            <TextField
              select label="Site" value={siteForm.site_id} fullWidth size="small"
              onChange={(e) => setSiteForm({ ...siteForm, site_id: e.target.value })}
            >
              {(sites as any[]).map((s) => (
                <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
              ))}
            </TextField>
          </Grid>
          <Grid size={{ xs: 12, sm: 3 }}>
            <TextField
              label="From" type="date" value={siteForm.date_from} fullWidth size="small"
              onChange={(e) => setSiteForm({ ...siteForm, date_from: e.target.value })}
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Grid>
          <Grid size={{ xs: 12, sm: 3 }}>
            <TextField
              label="Until" type="date" value={siteForm.date_until} fullWidth size="small"
              onChange={(e) => setSiteForm({ ...siteForm, date_until: e.target.value })}
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Grid>
          <Grid size={{ xs: 12, sm: 2 }}>
            <Button
              variant="contained" fullWidth
              disabled={!siteForm.site_id || downloading === 'site'}
              startIcon={downloading === 'site' ? <CircularProgress size={14} /> : null}
              onClick={() => handleDownload('site', () =>
                downloadSiteSummaryReport(siteForm.site_id, `${siteForm.date_from}T00:00:00Z`, `${siteForm.date_until}T23:59:59Z`)
              )}
            >
              Download PDF
            </Button>
          </Grid>
        </Grid>
      </GlassCard>

      {/* DOB Report */}
      <GlassCard sx={{ p: 3 }}>
        <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>Daily Occurrence Book (DOB) Report</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
          Printable DOB extract for PLRD compliance and handover purposes.
        </Typography>
        <Grid container spacing={2} sx={{ alignItems: "flex-end" }}>
          <Grid size={{ xs: 12, sm: 3 }}>
            <TextField
              label="From" type="date" value={dobForm.date_from} fullWidth size="small"
              onChange={(e) => setDobForm({ ...dobForm, date_from: e.target.value })}
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Grid>
          <Grid size={{ xs: 12, sm: 3 }}>
            <TextField
              label="Until" type="date" value={dobForm.date_until} fullWidth size="small"
              onChange={(e) => setDobForm({ ...dobForm, date_until: e.target.value })}
              slotProps={{ inputLabel: { shrink: true } }}
            />
          </Grid>
          <Grid size={{ xs: 12, sm: 4 }}>
            <TextField
              select label="Site (optional)" value={dobForm.site_id} fullWidth size="small"
              onChange={(e) => setDobForm({ ...dobForm, site_id: e.target.value })}
            >
              <MenuItem value="">All Sites</MenuItem>
              {(sites as any[]).map((s) => (
                <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>
              ))}
            </TextField>
          </Grid>
          <Grid size={{ xs: 12, sm: 2 }}>
            <Button
              variant="contained" fullWidth
              disabled={downloading === 'dob'}
              startIcon={downloading === 'dob' ? <CircularProgress size={14} /> : null}
              onClick={() => handleDownload('dob', () =>
                downloadDobReport(`${dobForm.date_from}T00:00:00Z`, `${dobForm.date_until}T23:59:59Z`, dobForm.site_id || undefined)
              )}
            >
              Download PDF
            </Button>
          </Grid>
        </Grid>
      </GlassCard>

      {/* Incident Report */}
      <GlassCard sx={{ p: 3 }}>
        <Typography variant="h6" sx={{ fontWeight: 700, mb: 0.5 }}>Incident Detail Report</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
          Full incident report with evidence chain of custody and timeline.
        </Typography>
        <Grid container spacing={2} sx={{ alignItems: "flex-end" }}>
          <Grid size={{ xs: 12, sm: 8 }}>
            <TextField
              label="Incident ID" value={incidentId} fullWidth size="small"
              placeholder="Paste incident UUID here"
              onChange={(e) => setIncidentId(e.target.value.trim())}
            />
          </Grid>
          <Grid size={{ xs: 12, sm: 4 }}>
            <Button
              variant="contained" fullWidth
              disabled={!incidentId || downloading === 'incident'}
              startIcon={downloading === 'incident' ? <CircularProgress size={14} /> : null}
              onClick={() => handleDownload('incident', () => downloadIncidentReport(incidentId))}
            >
              Download PDF
            </Button>
          </Grid>
        </Grid>
      </GlassCard>
    </Box>
  )
}

// ── DSAR / PDPA ─────────────────────────────────────────────────────────────

const DSAR_STATUS_COLORS: Record<string, 'default' | 'warning' | 'success' | 'error'> = {
  pending: 'warning',
  in_progress: 'warning',
  fulfilled: 'success',
  rejected: 'error',
}

function DsarTab() {
  const qc = useQueryClient()
  const [openCreate, setOpenCreate] = useState(false)
  const [openFulfill, setOpenFulfill] = useState<any>(null)
  const [openErase, setOpenErase] = useState<any>(null)
  const [createForm, setCreateForm] = useState({
    request_type: 'access',
    data_subject_name: '',
    data_subject_email: '',
    description: '',
  })
  const [fulfillForm, setFulfillForm] = useState({ status: 'fulfilled', fulfillment_notes: '', records_erased: '' })

  const { data: dsars = [], isLoading } = useQuery({ queryKey: ['dsars'], queryFn: () => getDsars() })

  const createMut = useMutation({
    mutationFn: () => createDsar(createForm),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['dsars'] }); setOpenCreate(false) },
  })

  const updateMut = useMutation({
    mutationFn: (id: string) => updateDsar(id, {
      status: fulfillForm.status,
      fulfillment_notes: fulfillForm.fulfillment_notes,
      records_erased: fulfillForm.records_erased ? parseInt(fulfillForm.records_erased) : undefined,
    }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['dsars'] }); setOpenFulfill(null) },
  })

  return (
    <Box>
      <Box sx={{ display: 'flex', justifyContent: 'space-between', mb: 2 }}>
        <Box>
          <Typography variant="h6" sx={{ fontWeight: 700 }}>Data Subject Access Requests (DSAR)</Typography>
          <Typography variant="caption" color="text.secondary">PDPA compliance — respond within 30 days</Typography>
        </Box>
        <Button variant="contained" onClick={() => setOpenCreate(true)}>+ New Request</Button>
      </Box>

      {isLoading ? (
        <CircularProgress />
      ) : (
        <Paper>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Date</TableCell>
                <TableCell>Type</TableCell>
                <TableCell>Data Subject</TableCell>
                <TableCell>Email</TableCell>
                <TableCell>Status</TableCell>
                <TableCell>Deadline</TableCell>
                <TableCell>Action</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {(dsars as any[]).map((d) => {
                const deadline = new Date(d.created_at)
                deadline.setDate(deadline.getDate() + 30)
                const isOverdue = deadline < new Date() && !['fulfilled', 'rejected'].includes(d.status)
                return (
                  <TableRow key={d.id}>
                    <TableCell>{new Date(d.created_at).toLocaleDateString()}</TableCell>
                    <TableCell>
                      <Chip label={d.request_type} size="small" variant="outlined" />
                    </TableCell>
                    <TableCell>{d.data_subject_name}</TableCell>
                    <TableCell>{d.data_subject_email || '—'}</TableCell>
                    <TableCell>
                      <Chip
                        label={d.status.replace(/_/g, ' ')}
                        color={DSAR_STATUS_COLORS[d.status] || 'default'}
                        size="small"
                      />
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color={isOverdue ? 'error' : 'text.secondary'}>
                        {deadline.toLocaleDateString()}
                        {isOverdue && ' ⚠ OVERDUE'}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      {!['fulfilled', 'rejected'].includes(d.status) && (
                        <>
                          {d.request_type === 'erasure' && (
                            <Button size="small" color="error" onClick={() => setOpenErase(d)} sx={{ mr: 1 }}>
                              Execute Erasure
                            </Button>
                          )}
                          <Button size="small" onClick={() => { setOpenFulfill(d); setFulfillForm({ status: 'fulfilled', fulfillment_notes: '', records_erased: '' }) }}>
                            Update
                          </Button>
                        </>
                      )}
                    </TableCell>
                  </TableRow>
                )
              })}
              {dsars.length === 0 && (
                <TableRow>
                  <TableCell colSpan={7} align="center" sx={{ py: 4 }}>
                    <Typography color="text.secondary">No DSAR requests on file</Typography>
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </Paper>
      )}

      {/* Create DSAR */}
      <Dialog open={openCreate} onClose={() => setOpenCreate(false)} maxWidth="sm" fullWidth>
        <DialogTitle>New DSAR Request</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField
            select label="Request Type" value={createForm.request_type}
            onChange={(e) => setCreateForm({ ...createForm, request_type: e.target.value })} fullWidth
          >
            <MenuItem value="access">Access (SAR)</MenuItem>
            <MenuItem value="erasure">Erasure (Right to be forgotten)</MenuItem>
            <MenuItem value="portability">Data Portability</MenuItem>
            <MenuItem value="correction">Correction</MenuItem>
            <MenuItem value="objection">Objection</MenuItem>
          </TextField>
          <TextField label="Data Subject Name" value={createForm.data_subject_name} onChange={(e) => setCreateForm({ ...createForm, data_subject_name: e.target.value })} fullWidth required />
          <TextField label="Data Subject Email" value={createForm.data_subject_email} onChange={(e) => setCreateForm({ ...createForm, data_subject_email: e.target.value })} fullWidth />
          <TextField label="Description" value={createForm.description} onChange={(e) => setCreateForm({ ...createForm, description: e.target.value })} multiline rows={3} fullWidth />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenCreate(false)}>Cancel</Button>
          <Button variant="contained" onClick={() => createMut.mutate()} disabled={!createForm.data_subject_name || createMut.isPending}>
            Submit
          </Button>
        </DialogActions>
      </Dialog>

      {/* Update DSAR */}
      <Dialog open={!!openFulfill} onClose={() => setOpenFulfill(null)} maxWidth="sm" fullWidth>
        <DialogTitle>Update DSAR — {openFulfill?.data_subject_name}</DialogTitle>
        <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
          <TextField
            select label="New Status" value={fulfillForm.status}
            onChange={(e) => setFulfillForm({ ...fulfillForm, status: e.target.value })} fullWidth
          >
            <MenuItem value="in_progress">In Progress</MenuItem>
            <MenuItem value="fulfilled">Fulfilled</MenuItem>
            <MenuItem value="rejected">Rejected</MenuItem>
          </TextField>
          <TextField
            label="Fulfillment Notes" value={fulfillForm.fulfillment_notes}
            onChange={(e) => setFulfillForm({ ...fulfillForm, fulfillment_notes: e.target.value })}
            multiline rows={3} fullWidth
          />
          {fulfillForm.status === 'fulfilled' && openFulfill?.request_type === 'erasure' && (
            <TextField
              label="Records Erased" type="number" value={fulfillForm.records_erased}
              onChange={(e) => setFulfillForm({ ...fulfillForm, records_erased: e.target.value })} fullWidth
            />
          )}
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOpenFulfill(null)}>Cancel</Button>
          <Button variant="contained" onClick={() => updateMut.mutate(openFulfill.id)} disabled={updateMut.isPending}>
            Save
          </Button>
        </DialogActions>
      </Dialog>

      {/* Execute Erasure */}
      {openErase && (
        <ErasureExecuteDialog
          dsar={openErase}
          onClose={() => setOpenErase(null)}
          onDone={() => { qc.invalidateQueries({ queryKey: ['dsars'] }); setOpenErase(null) }}
        />
      )}
    </Box>
  )
}

// ── Execute Erasure ──────────────────────────────────────────────────────────

function ErasureExecuteDialog({ dsar, onClose, onDone }: { dsar: any; onClose: () => void; onDone: () => void }) {
  const [faceIds, setFaceIds] = useState<string[]>([])
  const [plateIds, setPlateIds] = useState<string[]>([])
  const [visitorIds, setVisitorIds] = useState<string[]>([])
  const [evidenceIdsText, setEvidenceIdsText] = useState('')
  const [result, setResult] = useState<ErasureExecuteResult | null>(null)

  const { data: faceEntries = [] } = useQuery({ queryKey: ['face-watchlist'], queryFn: () => getFaceWatchlist() })
  const { data: plateEntries = [] } = useQuery({ queryKey: ['plate-watchlist'], queryFn: () => getPlateWatchlist() })
  const { data: visitors = [] } = useQuery({ queryKey: ['visitors'], queryFn: () => listVisitors() })

  const eraseMut = useMutation({
    mutationFn: () => {
      const evidence_ids = evidenceIdsText.split(/[\s,]+/).map((s) => s.trim()).filter(Boolean)
      return executeDsarErasure(dsar.id, {
        face_watchlist_entry_ids: faceIds,
        plate_watchlist_entry_ids: plateIds,
        visitor_ids: visitorIds,
        evidence_ids,
      })
    },
    onSuccess: (data) => setResult(data),
  })

  const toggle = (list: string[], setList: (v: string[]) => void, id: string) =>
    setList(list.includes(id) ? list.filter((x) => x !== id) : [...list, id])

  const nothingSelected = faceIds.length === 0 && plateIds.length === 0 && visitorIds.length === 0 && !evidenceIdsText.trim()

  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>Execute Erasure — {dsar.data_subject_name}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 2, pt: 2 }}>
        {result ? (
          <Alert severity="success">
            Erased: {result.erased_counts.face_watchlist_entries} face watchlist,{' '}
            {result.erased_counts.plate_watchlist_entries} plate watchlist,{' '}
            {result.erased_counts.visitors} visitors,{' '}
            {result.erased_counts.evidence} evidence files.
            <br />Total records erased on this DSAR to date: {result.records_erased}.
            <br />Set the DSAR status to "Fulfilled" via Update once done.
          </Alert>
        ) : (
          <>
            <Alert severity="warning">
              This permanently deletes the selected records (biometric embeddings, plate
              watchlist entries, visitor records) and redacts matching historical detection
              rows. This cannot be undone.
            </Alert>

            <Typography variant="subtitle2">Face Watchlist Entries</Typography>
            <List dense sx={{ maxHeight: 140, overflow: 'auto', border: 1, borderColor: 'divider', borderRadius: 1 }}>
              {(faceEntries as any[]).map((f) => (
                <ListItem key={f.id} dense>
                  <FormControlLabel
                    control={<Checkbox size="small" checked={faceIds.includes(f.id)} onChange={() => toggle(faceIds, setFaceIds, f.id)} />}
                    label={`${f.person_name} (${f.list_type})`}
                  />
                </ListItem>
              ))}
              {faceEntries.length === 0 && <ListItem><Typography variant="caption" color="text.secondary">No entries</Typography></ListItem>}
            </List>

            <Typography variant="subtitle2">Plate Watchlist Entries</Typography>
            <List dense sx={{ maxHeight: 140, overflow: 'auto', border: 1, borderColor: 'divider', borderRadius: 1 }}>
              {(plateEntries as any[]).map((p) => (
                <ListItem key={p.id} dense>
                  <FormControlLabel
                    control={<Checkbox size="small" checked={plateIds.includes(p.id)} onChange={() => toggle(plateIds, setPlateIds, p.id)} />}
                    label={`${p.plate_number} (${p.list_type})`}
                  />
                </ListItem>
              ))}
              {plateEntries.length === 0 && <ListItem><Typography variant="caption" color="text.secondary">No entries</Typography></ListItem>}
            </List>

            <Typography variant="subtitle2">Visitors</Typography>
            <List dense sx={{ maxHeight: 140, overflow: 'auto', border: 1, borderColor: 'divider', borderRadius: 1 }}>
              {(visitors as any[]).map((v) => (
                <ListItem key={v.id} dense>
                  <FormControlLabel
                    control={<Checkbox size="small" checked={visitorIds.includes(v.id)} onChange={() => toggle(visitorIds, setVisitorIds, v.id)} />}
                    label={`${v.full_name}${v.id_number ? ` (${v.id_number})` : ''}`}
                  />
                </ListItem>
              ))}
              {visitors.length === 0 && <ListItem><Typography variant="caption" color="text.secondary">No visitors</Typography></ListItem>}
            </List>

            <Divider />
            <TextField
              label="Evidence IDs (comma or newline separated)"
              helperText="Find IDs on the Evidence page — evidence has no name search, so paste IDs directly."
              value={evidenceIdsText}
              onChange={(e) => setEvidenceIdsText(e.target.value)}
              multiline rows={2} fullWidth
            />
          </>
        )}
      </DialogContent>
      <DialogActions>
        {result ? (
          <Button variant="contained" onClick={onDone}>Close</Button>
        ) : (
          <>
            <Button onClick={onClose}>Cancel</Button>
            <Button
              variant="contained" color="error"
              onClick={() => eraseMut.mutate()}
              disabled={nothingSelected || eraseMut.isPending}
            >
              {eraseMut.isPending ? <CircularProgress size={18} /> : 'Erase Selected'}
            </Button>
          </>
        )}
      </DialogActions>
    </Dialog>
  )
}

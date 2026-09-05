/**
 * Post Orders (Gap 87) — per-site standing instructions with acknowledgment
 * tracking. Guards read + acknowledge; supervisors author and see who still
 * hasn't read the current version.
 */
import { useState } from 'react'
import {
  Box,
  Button,
  Checkbox,
  Chip,
  Dialog,
  DialogActions,
  DialogContent,
  DialogTitle,
  Divider,
  FormControlLabel,
  IconButton,
  List,
  ListItem,
  ListItemText,
  MenuItem,
  Select,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material'
import Stack from '@/components/common/Stack'
import AddIcon from '@mui/icons-material/Add'
import CheckCircleIcon from '@mui/icons-material/CheckCircle'
import DeleteIcon from '@mui/icons-material/Delete'
import EditIcon from '@mui/icons-material/Edit'
import GroupIcon from '@mui/icons-material/Group'
import MenuBookIcon from '@mui/icons-material/MenuBook'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  acknowledgePostOrder, createPostOrder, deactivatePostOrder, getPostOrder,
  getPostOrderAcks, listPostOrders, updatePostOrder,
  POST_ORDER_CATEGORIES, type PostOrder,
} from '@/api/postOrders'
import { getSites } from '@/api/sites'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { FilterRail, type FilterGroup } from '@/components/common/FilterRail'
import { PermissionGuard } from '@/components/common/PermissionGuard'

const CATEGORY_COLORS: Record<string, 'default' | 'error' | 'primary' | 'warning' | 'info' | 'success'> = {
  general: 'default', emergency: 'error', access: 'primary',
  patrol: 'info', equipment: 'warning', contacts: 'success',
}

function EditorDialog({ open, onClose, existing }: {
  open: boolean; onClose: () => void; existing?: PostOrder | null
}) {
  const qc = useQueryClient()
  const [siteId, setSiteId] = useState(existing?.site_id ?? '')
  const [title, setTitle] = useState(existing?.title ?? '')
  const [bodyText, setBodyText] = useState(existing?.body ?? '')
  const [category, setCategory] = useState(existing?.category ?? 'general')
  const [reqAck, setReqAck] = useState(existing?.requires_acknowledgment ?? true)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites(), enabled: open })

  const { mutate: save, isPending } = useMutation({
    mutationFn: () => existing
      ? updatePostOrder(existing.id, { title, body: bodyText, category, requires_acknowledgment: reqAck })
      : createPostOrder({ site_id: siteId, title, body: bodyText, category, requires_acknowledgment: reqAck }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ['post-orders'] }); onClose() },
  })

  return (
    <Dialog open={open} onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle>{existing ? `Edit — ${existing.title}` : 'New Post Order'}</DialogTitle>
      <DialogContent sx={{ display: 'flex', flexDirection: 'column', gap: 1.5, pt: 1 }}>
        {existing && (
          <Typography variant="caption" color="warning.main">
            Editing the title or content bumps the version — all guards will
            need to acknowledge again.
          </Typography>
        )}
        {!existing && (
          <Select size="small" displayEmpty value={siteId}
                  onChange={(e) => setSiteId(e.target.value)}
                  renderValue={(v) => sites.find((s: any) => s.id === v)?.name ?? 'Site…'}>
            {sites.map((s: any) => <MenuItem key={s.id} value={s.id}>{s.name}</MenuItem>)}
          </Select>
        )}
        <TextField label="Title" size="small" value={title}
                   onChange={(e) => setTitle(e.target.value)} fullWidth />
        <Select size="small" value={category} onChange={(e) => setCategory(e.target.value)}>
          {POST_ORDER_CATEGORIES.map((c) => (
            <MenuItem key={c} value={c}>{c.charAt(0).toUpperCase() + c.slice(1)}</MenuItem>
          ))}
        </Select>
        <TextField label="Instructions" multiline minRows={8} value={bodyText}
                   onChange={(e) => setBodyText(e.target.value)} fullWidth />
        <FormControlLabel
          control={<Checkbox size="small" checked={reqAck}
                             onChange={(e) => setReqAck(e.target.checked)} />}
          label={<Typography variant="body2">Guards must acknowledge reading this</Typography>}
        />
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose} disabled={isPending}>Cancel</Button>
        <Button variant="contained" onClick={() => save()}
                disabled={isPending || !title.trim() || !bodyText.trim() || (!existing && !siteId)}>
          {existing ? 'Save (new version)' : 'Create'}
        </Button>
      </DialogActions>
    </Dialog>
  )
}

function ReaderDialog({ orderId, onClose }: { orderId: string; onClose: () => void }) {
  const qc = useQueryClient()
  const { data: order } = useQuery({
    queryKey: ['post-order', orderId],
    queryFn: () => getPostOrder(orderId),
  })
  const { mutate: ack, isPending } = useMutation({
    mutationFn: () => acknowledgePostOrder(orderId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['post-orders'] })
      qc.invalidateQueries({ queryKey: ['post-order', orderId] })
    },
  })
  return (
    <Dialog open onClose={onClose} maxWidth="sm" fullWidth>
      <DialogTitle sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
        <MenuBookIcon fontSize="small" />
        <Box sx={{ flex: 1 }}>{order?.title ?? '…'}</Box>
        <Chip label={`v${order?.version ?? ''}`} size="small" variant="outlined" />
      </DialogTitle>
      <DialogContent dividers>
        <Stack direction="row" spacing={1} sx={{ mb: 1.5 }}>
          {order && <Chip label={order.category} size="small" color={CATEGORY_COLORS[order.category] ?? 'default'} />}
          {order?.site_name && <Chip label={order.site_name} size="small" variant="outlined" />}
        </Stack>
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>
          {order?.body}
        </Typography>
      </DialogContent>
      <DialogActions>
        <Button onClick={onClose}>Close</Button>
        {order?.requires_acknowledgment && (
          order.acknowledged ? (
            <Chip icon={<CheckCircleIcon />} label="Acknowledged" color="success" size="small" />
          ) : (
            <Button variant="contained" disabled={isPending} onClick={() => ack()}>
              I have read and understood
            </Button>
          )
        )}
      </DialogActions>
    </Dialog>
  )
}

function AcksDialog({ orderId, onClose }: { orderId: string; onClose: () => void }) {
  const { data } = useQuery({
    queryKey: ['post-order-acks', orderId],
    queryFn: () => getPostOrderAcks(orderId),
  })
  return (
    <Dialog open onClose={onClose} maxWidth="xs" fullWidth>
      <DialogTitle>Acknowledgments — v{data?.current_version}</DialogTitle>
      <DialogContent dividers sx={{ p: 0 }}>
        <List dense subheader={
          <Typography variant="caption" color="success.main" sx={{ px: 2, pt: 1, display: 'block' }}>
            Acknowledged ({data?.acknowledged.length ?? 0})
          </Typography>
        }>
          {data?.acknowledged.map((a) => (
            <ListItem key={a.user_id}>
              <ListItemText primary={a.full_name ?? a.email}
                            secondary={new Date(a.acknowledged_at).toLocaleString()} />
            </ListItem>
          ))}
        </List>
        <Divider />
        <List dense subheader={
          <Typography variant="caption" color="error.main" sx={{ px: 2, pt: 1, display: 'block' }}>
            Pending ({data?.pending.length ?? 0})
          </Typography>
        }>
          {data?.pending.length === 0 ? (
            <ListItem><ListItemText secondary="Everyone assigned has acknowledged." /></ListItem>
          ) : data?.pending.map((p) => (
            <ListItem key={p.user_id}>
              <ListItemText primary={p.full_name ?? p.email} />
            </ListItem>
          ))}
        </List>
      </DialogContent>
      <DialogActions><Button onClick={onClose}>Close</Button></DialogActions>
    </Dialog>
  )
}

export function PostOrdersPage() {
  const qc = useQueryClient()
  const [siteFilter, setSiteFilter] = useState('')
  const [editorOpen, setEditorOpen] = useState(false)
  const [editing, setEditing] = useState<PostOrder | null>(null)
  const [readingId, setReadingId] = useState<string | null>(null)
  const [acksId, setAcksId] = useState<string | null>(null)

  const { data: sites = [] } = useQuery({ queryKey: ['sites'], queryFn: () => getSites() })
  const { data: orders = [] } = useQuery({
    queryKey: ['post-orders', siteFilter],
    queryFn: () => listPostOrders(siteFilter || undefined),
  })

  const { mutate: deactivate } = useMutation({
    mutationFn: (id: string) => deactivatePostOrder(id),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['post-orders'] }),
  })

  const openEditor = async (order: PostOrder) => {
    // Fetch the full body before editing (list omits it)
    const full = await getPostOrder(order.id)
    setEditing(full)
    setEditorOpen(true)
  }

  const filterGroups: FilterGroup[] = [{
    key: 'site',
    label: 'Site',
    value: siteFilter,
    onChange: setSiteFilter,
    options: [
      { value: '', label: 'All sites' },
      ...sites.map((s: any) => ({ value: s.id, label: s.name })),
    ],
  }]

  return (
    <Box sx={{ display: 'flex', alignItems: 'flex-start', gap: 2 }}>
      <Box sx={{ flex: 1, minWidth: 0 }}>
      <PageHeader pageKey="post-orders" />

      <Stack direction="row" spacing={1.5} sx={{ mb: 2, alignItems: 'center', flexWrap: 'wrap' }}>
        <PermissionGuard permission="shift:manage">
          <Button variant="contained" size="small" startIcon={<AddIcon />}
                  onClick={() => { setEditing(null); setEditorOpen(true) }}>
            New Post Order
          </Button>
        </PermissionGuard>
      </Stack>

      <GlassCard>
        <List>
          {orders.length === 0 ? (
            <ListItem>
              <ListItemText secondary="No post orders for this selection." />
            </ListItem>
          ) : orders.map((o) => (
            <ListItem key={o.id} divider
                      sx={{ '&:hover': { background: 'rgba(255,255,255,0.03)' } }}>
              <ListItemText
                primary={
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="body2" fontWeight={700}>{o.title}</Typography>
                    <Chip label={o.category} size="small"
                          color={CATEGORY_COLORS[o.category] ?? 'default'}
                          sx={{ height: 18, fontSize: '0.62rem' }} />
                    <Chip label={`v${o.version}`} size="small" variant="outlined"
                          sx={{ height: 18, fontSize: '0.62rem' }} />
                  </Stack>
                }
                secondary={`${o.site_name ?? ''} · updated ${new Date(o.updated_at).toLocaleDateString()}`}
              />
              <Stack direction="row" spacing={0.5} alignItems="center">
                {o.requires_acknowledgment && (
                  o.acknowledged ? (
                    <Tooltip title="You have acknowledged the current version">
                      <CheckCircleIcon color="success" fontSize="small" />
                    </Tooltip>
                  ) : (
                    <Chip label="Read required" size="small" color="warning"
                          sx={{ height: 18, fontSize: '0.62rem' }} />
                  )
                )}
                <Button size="small" onClick={() => setReadingId(o.id)}>Read</Button>
                <PermissionGuard permission="shift:manage">
                  <Tooltip title="Who has acknowledged">
                    <IconButton size="small" onClick={() => setAcksId(o.id)}>
                      <GroupIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Edit (bumps version)">
                    <IconButton size="small" onClick={() => openEditor(o)}>
                      <EditIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                  <Tooltip title="Deactivate">
                    <IconButton size="small" color="error" onClick={() => deactivate(o.id)}>
                      <DeleteIcon fontSize="small" />
                    </IconButton>
                  </Tooltip>
                </PermissionGuard>
              </Stack>
            </ListItem>
          ))}
        </List>
      </GlassCard>

      {editorOpen && (
        <EditorDialog open onClose={() => setEditorOpen(false)} existing={editing} />
      )}
      {readingId && <ReaderDialog orderId={readingId} onClose={() => setReadingId(null)} />}
      {acksId && <AcksDialog orderId={acksId} onClose={() => setAcksId(null)} />}
      </Box>

      <FilterRail groups={filterGroups} storageKey="post-orders" />
    </Box>
  )
}

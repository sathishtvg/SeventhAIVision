/**
 * Smart Investigation — one investigation: why it was opened, and everything
 * in it, in the order it happened.
 *
 * Each record is read where it lives, now, as this reader may see it. One this
 * reader may not see is listed as such and nothing of it is shown; one that is
 * no longer held says so. An entry is never removed — it is set aside, with the
 * reason, and stays in the file marked.
 */
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import { Alert, Box, Button, Chip, Link, Skeleton, Typography } from '@mui/material'
import LockIcon from '@mui/icons-material/Lock'
import LockOpenIcon from '@mui/icons-material/LockOpen'
import NoteAddIcon from '@mui/icons-material/NoteAdd'
import SearchIcon from '@mui/icons-material/Search'
import Inventory2Icon from '@mui/icons-material/Inventory2'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import {
  addNote, closeInvestigation, getInvestigation, reopenInvestigation, setAside,
} from '@/api/investigations'
import type { Entry } from '@/api/investigations'
import { listPackages } from '@/api/evidencePackages'
import { usePermission } from '@/hooks/usePermission'
import { CreatePackageDialog } from '@/components/evidence/EvidenceDialogs'
import { TrailDialog, WhyDialog } from '@/components/investigations/InvestigationDialogs'
import { KIND_LABEL, fmt, followable, home, pretty, subject } from '@/components/investigations/investigationFormat'
import { InvestigationNav } from './InvestigationNav'
import { ErrorState } from '@/components/states'

type Asking = { what: 'note' | 'close' | 'reopen' } | { what: 'aside'; entry: Entry } | null

function Body({ entry, onFollow, onOpen }: {
  entry: Entry; onFollow: (s: { plate?: string; watchlist_entry_id?: string }) => void; onOpen: (path: string) => void
}) {
  if (entry.state === 'NOTE') return <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap' }}>{entry.note}</Typography>
  if (entry.state === 'NOT_PERMITTED') {
    return <Typography variant="body2" color="text.secondary">
      A record of a kind you may not read. It is in the investigation; nothing of it is shown to you.</Typography>
  }
  if (entry.state === 'NOT_AVAILABLE' || !entry.record) {
    return <Typography variant="body2" color="text.secondary">
      This record is no longer held, or is at a site you are not assigned to.</Typography>
  }
  const r = entry.record
  const about = subject(r)
  const follow = followable(r)
  const at = home(r)
  return (
    <>
      <Typography variant="body2" sx={{ fontWeight: 600 }}>{r.title}</Typography>
      {r.summary && <Typography variant="body2" color="text.secondary">{r.summary}</Typography>}
      <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
        {[r.site_name, r.camera_name,
          // A plate read is of the kind "lpr" and a face match "face": the chip already says so.
          r.event_type && r.kind !== 'PLATE_READ' && r.kind !== 'FACE_MATCH' && pretty(r.event_type),
          r.severity && `Severity ${r.severity}`,
          r.risk_level && `Risk ${pretty(r.risk_level)}`, r.status && pretty(r.status),
          r.confidence !== null && `Confidence ${Math.round(r.confidence * 100)}%`].filter(Boolean).join(' · ')}
      </Typography>
      {about && about !== r.title && (
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>About: {about}</Typography>)}
      <Stack direction="row" sx={{ gap: 1.5, mt: 0.5, alignItems: 'center' }}>
        <Link component="button" variant="caption" onClick={() => onOpen(at.path)}>
          {at.exact ? 'Open the record' : `Find it in ${at.screen}`}</Link>
        {follow && <Link component="button" variant="caption" onClick={() => onFollow(follow)}>Where it was seen</Link>}
      </Stack>
      {entry.note && (
        <Typography variant="body2" sx={{ mt: 0.75, pl: 1.5, borderLeft: '2px solid', borderColor: 'divider' }}>
          {entry.note}</Typography>)}
    </>
  )
}

export default function Investigation() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [asking, setAsking] = useState<Asking>(null)
  const [following, setFollowing] = useState<{ plate?: string; watchlist_entry_id?: string } | null>(null)
  const [packaging, setPackaging] = useState(false)
  const seesPackages = usePermission('evidence:package:read')
  const makesPackages = usePermission('evidence:package:manage')
  const { data: file, isLoading, error, refetch } = useQuery({
    queryKey: ['investigation', id], queryFn: () => getInvestigation(id), enabled: !!id })
  const { data: packages } = useQuery({
    queryKey: ['evidence-packages', 'of-investigation', id], queryFn: () => listPackages({ investigation_id: id }),
    enabled: !!id && seesPackages })
  const done = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['investigation', id] }), qc.invalidateQueries({ queryKey: ['investigations'] })])

  if (isLoading) return <Box sx={{ p: 3 }}><Skeleton height={320} /></Box>
  if (error || !file) {
    return (
      <Box sx={{ p: 3 }}>
        <PageHeader title="Investigation" />
        <InvestigationNav />
        {error
          ? <ErrorState error={error} onRetry={refetch} title="Could not open the investigation" />
          : <Alert severity="error">Investigation not found.</Alert>}
      </Box>
    )
  }
  const open = file.status === 'OPEN'
  const may = file.can_manage
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title={file.title} subtitle={`${file.investigation_number} · ${file.site_name ?? 'More than one site'}`}
                  action={may ? (
                    <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
                      {open && <Button startIcon={<SearchIcon />} onClick={() => navigate('/investigate')}>
                        Search for records</Button>}
                      {open && <Button variant="outlined" startIcon={<NoteAddIcon />}
                                       onClick={() => setAsking({ what: 'note' })}>Add a note</Button>}
                      {open ? (
                        <Button variant="contained" startIcon={<LockIcon />} onClick={() => setAsking({ what: 'close' })}>
                          Close</Button>
                      ) : (
                        <Button variant="outlined" startIcon={<LockOpenIcon />}
                                onClick={() => setAsking({ what: 'reopen' })}>Reopen</Button>
                      )}
                    </Stack>) : undefined} />
      <InvestigationNav />

      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', alignItems: 'center', mb: 1 }}>
          <Chip size="small" color={open ? 'primary' : 'default'} variant={open ? 'filled' : 'outlined'}
                label={open ? 'Open' : 'Closed'} />
          <Chip size="small" variant="outlined" label={`${file.counts.records} record${file.counts.records === 1 ? '' : 's'}`} />
          <Chip size="small" variant="outlined" label={`${file.counts.notes} note${file.counts.notes === 1 ? '' : 's'}`} />
          {file.counts.set_aside > 0 && <Chip size="small" variant="outlined" label={`${file.counts.set_aside} set aside`} />}
          {file.incident_id && <Chip size="small" variant="outlined" label="Opened from an incident" />}
          {file.situation_id && <Chip size="small" variant="outlined" label="Opened from a situation"
                                      onClick={() => navigate(`/situations/${file.situation_id}`)} />}
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          Why it was opened</Typography>
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mb: 1 }}>{file.reason}</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          Opened {fmt(file.opened_at)} by {file.opened_by_name ?? 'somebody no longer on the system'}</Typography>
        {!open && (
          <Alert severity="info" icon={<LockIcon fontSize="inherit" />} sx={{ mt: 1.5 }}>
            Closed {fmt(file.closed_at)} by {file.closed_by_name ?? '—'}: {file.closing_note}</Alert>)}
        {file.counts.not_shown > 0 && (
          <Alert severity="warning" sx={{ mt: 1.5 }}>
            {file.counts.not_shown} record{file.counts.not_shown === 1 ? ' is' : 's are'} in this investigation and
            not shown to you — a kind you may not read, a site you are not assigned to, or no longer held.</Alert>)}
      </GlassCard>

      {seesPackages && (
        <GlassCard sx={{ p: 2, mb: 2 }} data-testid="evidence-of">
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', flexWrap: 'wrap' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Evidence</Typography>
            <Box sx={{ flex: 1 }} />
            {makesPackages && (
              <Button size="small" variant="outlined" startIcon={<Inventory2Icon />}
                      disabled={!file.counts.records} onClick={() => setPackaging(true)}>
                Put the evidence together</Button>)}
          </Stack>
          {!(packages?.items.length) ? (
            <Typography variant="body2" color="text.secondary">
              No evidence package has been made for this investigation. A package gathers the frames, clips and
              recordings that belong to the records filed here, seals them and keeps them past retention.
            </Typography>
          ) : packages.items.map((p) => (
            <Stack key={p.id} direction="row" sx={{ gap: 1, alignItems: 'center', mt: 0.75 }}>
              <Chip size="small" color={p.status === 'SEALED' ? 'primary' : 'default'}
                    variant={p.status === 'SEALED' ? 'filled' : 'outlined'}
                    label={p.status === 'SEALED' ? 'Sealed' : 'Draft'} />
              <Link component="button" variant="body2" onClick={() => navigate(`/evidence-packages/${p.id}`)}>
                {p.package_number} — {p.title}</Link>
              <Typography variant="caption" color="text.secondary">
                {p.items} item{p.items === 1 ? '' : 's'}</Typography>
            </Stack>
          ))}
        </GlassCard>
      )}

      <GlassCard sx={{ p: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>In the order it happened</Typography>
        {!file.items.length ? (
          <Alert severity="info">Nothing is in this investigation yet. Search for records and file them here, or add
            a note.</Alert>
        ) : file.items.map((entry) => {
          const aside = !!entry.set_aside_at
          return (
            <Box key={entry.id} data-testid="file-entry"
                 sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '190px 1fr auto' }, gap: 1.5, py: 1.25,
                       borderTop: '1px solid', borderColor: 'divider', opacity: aside ? 0.55 : 1 }}>
              <Box>
                <Typography variant="body2">{fmt(entry.occurred_at)}</Typography>
                <Chip size="small" sx={{ mt: 0.5 }} variant={entry.kind === 'NOTE' ? 'outlined' : 'filled'}
                      label={KIND_LABEL[entry.kind]} />
              </Box>
              <Box sx={{ minWidth: 0 }}>
                <Body entry={entry} onFollow={setFollowing} onOpen={(path) => navigate(path)} />
                <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mt: 0.5 }}>
                  {entry.kind === 'NOTE' ? 'Written' : 'Filed'} {fmt(entry.added_at)} by {entry.added_by_name ?? '—'}
                </Typography>
                {aside && (
                  <Typography variant="caption" sx={{ display: 'block', mt: 0.25 }}>
                    Set aside {fmt(entry.set_aside_at)} by {entry.set_aside_by_name ?? '—'}: {entry.set_aside_reason}
                  </Typography>)}
              </Box>
              <Box>
                {may && open && !aside && (
                  <Button size="small" onClick={() => setAsking({ what: 'aside', entry })}>Set aside</Button>)}
              </Box>
            </Box>
          )
        })}
      </GlassCard>

      <WhyDialog open={asking?.what === 'note'} title="Add a note" label="Note" confirm="Add" min={2}
                 hint="A note is part of the investigation and is not edited afterwards."
                 onClose={() => setAsking(null)} onConfirm={(text) => addNote(id, text).then(done)} />
      <WhyDialog open={asking?.what === 'close'} title="Close this investigation" label="What was found" confirm="Close"
                 min={5} hint="Say what was found, or why it is being left. A closed investigation cannot be changed until it is reopened."
                 onClose={() => setAsking(null)} onConfirm={(text) => closeInvestigation(id, text).then(done)} />
      <WhyDialog open={asking?.what === 'reopen'} title="Reopen this investigation" label="Why it is being reopened"
                 confirm="Reopen" hint="How it was closed stays in the investigation as a note."
                 onClose={() => setAsking(null)} onConfirm={(text) => reopenInvestigation(id, text).then(done)} />
      <WhyDialog open={asking?.what === 'aside'} title="Set this entry aside" label="Why it is being set aside"
                 confirm="Set aside" hint="It stays in the investigation, marked as set aside with your reason."
                 onClose={() => setAsking(null)}
                 onConfirm={(text) => asking?.what === 'aside' ? setAside(id, asking.entry.id, text).then(done)
                   : Promise.resolve()} />
      <TrailDialog subject={following} onClose={() => setFollowing(null)} />
      <CreatePackageDialog open={packaging} onClose={() => setPackaging(false)}
                           investigation={{ id, label: `${file.investigation_number} — ${file.title}` }}
                           onCreated={(made) => { setPackaging(false); navigate(`/evidence-packages/${made}`) }} />
    </Box>
  )
}

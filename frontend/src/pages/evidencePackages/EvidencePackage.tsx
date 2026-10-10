/**
 * Evidence packages — one package: what it is for, what is in it, whether it
 * is sealed and intact, and its chain of custody.
 *
 * A draft is put together from what the platform kept about the records of its
 * investigation or incident — offered here, chosen by a person. Sealing fixes
 * it: after that this page offers nothing that would change what is in it,
 * because the server would refuse it.
 *
 * Each item is read where it lives, now. One that is no longer held, or that
 * this reader may not open, says so.
 */
import { useState } from 'react'
import { useNavigate, useParams } from 'react-router-dom'
import {
  Alert, Box, Button, Checkbox, Chip, Link, Skeleton, Tooltip, Typography,
} from '@mui/material'
import LockIcon from '@mui/icons-material/Lock'
import DownloadIcon from '@mui/icons-material/Download'
import VerifiedIcon from '@mui/icons-material/Verified'
import ShieldIcon from '@mui/icons-material/Shield'
import SendIcon from '@mui/icons-material/Send'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import Stack from '@/components/common/Stack'
import { GlassCard } from '@/components/common/GlassCard'
import { PageHeader } from '@/components/common/PageHeader'
import { EvidenceThumb } from '@/components/common/EvidenceThumb'
import {
  addItems, apiError, downloadOriginal, getCandidates, getCustody, getPackage, releasePackageHolds, removeItem,
  saveFile,
} from '@/api/evidencePackages'
import type { ExportResult, PackageItem, Thing } from '@/api/evidencePackages'
import { WhyDialog } from '@/components/investigations/InvestigationDialogs'
import { DisclosureDialog, ExportDialog, SealDialog } from '@/components/evidence/EvidenceDialogs'
import {
  KIND_LABEL, STEP_LABEL, WEIGHTY, fmt, length, roleName, said, short, size,
} from '@/components/evidence/evidenceFormat'
import { InvestigationNav } from '@/pages/investigations/InvestigationNav'
import { ErrorState } from '@/components/states'

const keyOf = (t: Pick<Thing, 'kind' | 'id'>) => `${t.kind}:${t.id}`

function About({ thing }: { thing: Thing }) {
  return (
    <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
      {[fmt(thing.captured_at), thing.camera_name, thing.site_name, size(thing.size_bytes),
        length(thing.duration_seconds),
        thing.kept && thing.kept !== 'central' ? 'held at the site, not uploaded' : null].filter(Boolean).join(' · ')}
    </Typography>
  )
}

export default function EvidencePackage() {
  const { id = '' } = useParams()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [picked, setPicked] = useState<Record<string, Thing>>({})
  const [asking, setAsking] = useState<'seal' | 'export' | 'disclose' | 'lift' | null>(null)
  const [exported, setExported] = useState<ExportResult | null>(null)
  const { data: file, isLoading, error, refetch } = useQuery({
    queryKey: ['evidence-package', id], queryFn: () => getPackage(id), enabled: !!id })
  const draft = file?.status === 'DRAFT'
  const { data: offered } = useQuery({
    queryKey: ['evidence-package', id, 'candidates'], queryFn: () => getCandidates(id),
    enabled: !!file && draft && file.can.manage })
  const { data: custody } = useQuery({
    queryKey: ['evidence-package', id, 'custody'], queryFn: () => getCustody(id), enabled: !!file && file.can.custody })
  const done = () => Promise.all([
    qc.invalidateQueries({ queryKey: ['evidence-package', id] }),
    qc.invalidateQueries({ queryKey: ['evidence-packages'] })])
  const add = useMutation({
    mutationFn: () => addItems(id, Object.values(picked)).then(done), onSuccess: () => setPicked({}) })
  const remove = useMutation({ mutationFn: (item: PackageItem) => removeItem(id, item.id).then(done) })
  const original = useMutation({
    mutationFn: (item: PackageItem) => downloadOriginal(id, item.id).then((blob) => {
      saveFile(blob, `${file?.package_number}-${item.kind.toLowerCase()}-${item.ref_id}`)
      return done()
    }) })

  if (isLoading) return <Box sx={{ p: 3 }}><Skeleton height={320} /></Box>
  if (error || !file) {
    return (
      <Box sx={{ p: 3 }}>
        <PageHeader title="Evidence Package" />
        <InvestigationNav />
        {error
          ? <ErrorState error={error} onRetry={refetch} title="Could not open the evidence package" />
          : <Alert severity="error">Evidence package not found.</Alert>}
      </Box>
    )
  }
  const sealed = !draft
  const pickedList = Object.values(picked)
  const failure = add.error ?? remove.error ?? original.error
  return (
    <Box sx={{ p: 3 }}>
      <PageHeader title={file.title} subtitle={`${file.package_number} · ${file.site_name ?? 'More than one site'}`}
                  action={(
                    <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap' }}>
                      {draft && file.can.manage && (
                        <Button variant="contained" color="warning" startIcon={<LockIcon />}
                                disabled={!file.counts.items} onClick={() => setAsking('seal')}>Seal</Button>)}
                      {sealed && file.can.export && (
                        <Button variant="contained" startIcon={<DownloadIcon />} onClick={() => setAsking('export')}>
                          Export</Button>)}
                      {sealed && file.can.export && (
                        <Button variant="outlined" startIcon={<SendIcon />} onClick={() => setAsking('disclose')}>
                          Record who it went to</Button>)}
                      {sealed && file.can.hold && file.counts.held > 0 && (
                        <Button onClick={() => setAsking('lift')}>Lift the holds</Button>)}
                    </Stack>)} />
      <InvestigationNav />

      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Stack direction="row" sx={{ gap: 1, flexWrap: 'wrap', alignItems: 'center', mb: 1 }}>
          {sealed ? <Chip size="small" color="primary" icon={<LockIcon />} label="Sealed" />
            : <Chip size="small" variant="outlined" label="Draft" />}
          <Chip size="small" variant="outlined" label={`${file.counts.items} item${file.counts.items === 1 ? '' : 's'}`} />
          {file.counts.held > 0 && (
            <Tooltip title="Kept past the retention period until the hold is lifted">
              <Chip size="small" variant="outlined" icon={<ShieldIcon />} label={`${file.counts.held} held`} /></Tooltip>)}
          {file.investigation_id && (
            <Chip size="small" variant="outlined" label={`Evidence of ${file.investigation_number}`}
                  onClick={() => navigate(`/investigations/${file.investigation_id}`)} />)}
          {file.incident_id && <Chip size="small" variant="outlined" label="Evidence of an incident" />}
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: '0.06em' }}>
          What it is for</Typography>
        <Typography variant="body2" sx={{ whiteSpace: 'pre-wrap', mb: 1 }}>{file.purpose}</Typography>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
          Created {fmt(file.created_at)} by {file.created_by_name ?? 'somebody no longer on the system'}
          {sealed && ` · sealed ${fmt(file.sealed_at)} by ${file.sealed_by_name ?? 'somebody no longer on the system'}`}
        </Typography>
        {sealed && (
          <Box sx={{ mt: 1.5 }} data-testid="seal">
            {file.intact ? (
              <Alert severity="success" icon={<VerifiedIcon fontSize="inherit" />}>
                The manifest has the hash it was sealed with.
                <Typography component="span" variant="body2" sx={{ display: 'block', fontFamily: 'monospace', wordBreak: 'break-all' }}>
                  SHA-256 {file.manifest_sha256}</Typography></Alert>
            ) : (
              <Alert severity="error">The manifest does NOT have the hash it was sealed with. This package is not
                exported as it is.</Alert>)}
          </Box>)}
        {draft && (
          <Alert severity="info" sx={{ mt: 1.5 }}>
            A draft. It holds references to evidence, not copies, and can still be added to and taken from. Seal it
            before any of it leaves the platform.</Alert>)}
        {file.counts.without_checksum > 0 && (
          <Alert severity="warning" sx={{ mt: 1.5 }}>
            {file.counts.without_checksum} item{file.counts.without_checksum === 1 ? ' has' : 's have'} no checksum
            recorded. {file.counts.without_checksum === 1 ? 'It' : 'They'} can be exported and cannot be verified.</Alert>)}
        {file.counts.not_shown > 0 && (
          <Alert severity="warning" sx={{ mt: 1.5 }}>
            {file.counts.not_shown} item{file.counts.not_shown === 1 ? ' is' : 's are'} not shown to you — no longer
            held by the platform, at a site you are not assigned to, or of a kind you may not open.</Alert>)}
        {exported && (
          <Alert severity={exported.mismatched ? 'error' : 'success'} sx={{ mt: 1.5 }} onClose={() => setExported(null)}
                 data-testid="export-result">
            Exported {exported.included} file{exported.included === 1 ? '' : 's'}: {exported.verified} matched
            {exported.verified === 1 ? ' its' : ' their'} checksum
            {exported.mismatched > 0 && <b>, {exported.mismatched} DID NOT MATCH</b>}
            {exported.unverifiable > 0 && `, ${exported.unverifiable} had no checksum to compare`}
            {exported.left_out > 0 && `, ${exported.left_out} left out`}. export.json in the archive says which.</Alert>)}
        {failure && <Alert severity="error" sx={{ mt: 1.5 }}>{apiError(failure)}</Alert>}
      </GlassCard>

      {draft && file.can.manage && offered && (
        <GlassCard sx={{ p: 2, mb: 2 }}>
          <Stack direction="row" sx={{ gap: 1, alignItems: 'center', mb: 1, flexWrap: 'wrap' }}>
            <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>What belongs to it and is not in it yet</Typography>
            <Box sx={{ flex: 1 }} />
            <Button size="small" disabled={!offered.items.length}
                    onClick={() => setPicked(Object.fromEntries(offered.items.map((t) => [keyOf(t), t])))}>
              Pick all</Button>
            <Button size="small" variant="contained" disabled={!pickedList.length || add.isPending}
                    onClick={() => add.mutate()}>Add {pickedList.length || ''} to the package</Button>
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>
            Found under the {offered.records} record{offered.records === 1 ? '' : 's'} of the
            {file.investigation_id ? ' investigation' : ' incident'}: the frame of that detection, the recording of
            that camera at that moment. To have something else offered, file its record in the investigation.
          </Typography>
          {offered.not_looked_for.map((why) => (
            <Alert key={why} severity="warning" sx={{ mb: 1 }}>Some kinds of evidence were not looked for. {why}</Alert>))}
          {!offered.items.length ? (
            <Alert severity="info">{offered.already_in ? 'Everything that belongs is already in the package.'
              : 'The platform kept nothing that belongs to these records.'}</Alert>
          ) : offered.items.map((t) => (
            <Stack key={keyOf(t)} direction="row" data-testid="candidate"
                   sx={{ gap: 1.5, alignItems: 'center', py: 0.75, borderTop: '1px solid', borderColor: 'divider' }}>
              <Checkbox size="small" checked={!!picked[keyOf(t)]}
                        slotProps={{ input: { 'aria-label': `Pick ${t.what}` } }}
                        onChange={(_, on) => setPicked((p) => {
                          const next = { ...p }
                          if (on) next[keyOf(t)] = t; else delete next[keyOf(t)]
                          return next
                        })} />
              {t.kind === 'SNAPSHOT' && t.may_open ? <EvidenceThumb frameEvidenceId={t.id} /> : <Box sx={{ width: 40 }} />}
              <Box sx={{ minWidth: 0, flex: 1 }}>
                <Typography variant="body2" sx={{ fontWeight: 600 }}>{t.what}</Typography>
                <About thing={t} />
                {t.offset_seconds !== undefined && (
                  <Typography variant="caption" color="text.secondary">
                    The record is {Math.round(t.offset_seconds / 60)} min into it</Typography>)}
              </Box>
              <Chip size="small" label={KIND_LABEL[t.kind]} />
            </Stack>
          ))}
        </GlassCard>
      )}

      <GlassCard sx={{ p: 2, mb: 2 }}>
        <Typography variant="subtitle1" sx={{ fontWeight: 600, mb: 1 }}>In the package</Typography>
        {!file.items.length ? <Alert severity="info">Nothing is in this package yet.</Alert> : file.items.map((item) => (
          <Stack key={item.id} direction="row" data-testid="package-item"
                 sx={{ gap: 1.5, alignItems: 'center', py: 1, borderTop: '1px solid', borderColor: 'divider' }}>
            {item.kind === 'SNAPSHOT' && item.thing?.may_open
              ? <EvidenceThumb frameEvidenceId={item.ref_id} /> : <Box sx={{ width: 40 }} />}
            <Box sx={{ minWidth: 0, flex: 1 }}>
              {item.thing ? (
                <>
                  <Typography variant="body2" sx={{ fontWeight: 600 }}>{item.thing.what}</Typography>
                  <About thing={item.thing} />
                </>
              ) : (
                <Typography variant="body2" color="text.secondary">
                  {item.state === 'NOT_PERMITTED' ? 'Evidence of a kind you may not open. It is in the package; nothing of it is shown to you.'
                    : `No longer held by the platform, or at a site you are not assigned to. It was captured ${fmt(item.captured_at)}.`}
                </Typography>
              )}
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block', fontFamily: 'monospace' }}>
                SHA-256 {short(item.checksum_sha256)}</Typography>
              {item.checksum_changed && (
                <Typography variant="caption" color="error" sx={{ display: 'block' }}>
                  The platform now records a different checksum for this than when it was added.</Typography>)}
              {item.note && <Typography variant="body2" sx={{ mt: 0.5 }}>{item.note}</Typography>}
            </Box>
            <Stack sx={{ alignItems: 'flex-end', gap: 0.5 }}>
              <Chip size="small" label={KIND_LABEL[item.kind]} />
              {item.held && <Chip size="small" variant="outlined" icon={<ShieldIcon />} label="Held" />}
              {draft && file.can.manage && (
                <Link component="button" variant="caption" onClick={() => remove.mutate(item)}>Take out</Link>)}
              {sealed && file.can.export && item.thing && (
                <Link component="button" variant="caption" onClick={() => original.mutate(item)}>
                  Download the original</Link>)}
            </Stack>
          </Stack>
        ))}
      </GlassCard>

      {file.can.custody && custody && (
        <GlassCard sx={{ p: 2 }}>
          <Typography variant="subtitle1" sx={{ fontWeight: 600 }}>Chain of custody</Typography>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 1 }}>{custody.note}</Typography>
          {custody.chain.map((step, i) => {
            const about = step.ref_id ? file.items.find((it) => it.ref_id === step.ref_id) : null
            const more = said(step)
            return (
              <Box key={`${step.step}:${step.at}:${step.ref_id ?? ''}:${i}`} data-testid="custody-step"
                   sx={{ display: 'grid', gridTemplateColumns: { xs: '1fr', md: '200px 1fr' }, gap: 1.5, py: 0.75,
                         borderTop: '1px solid', borderColor: 'divider' }}>
                <Typography variant="body2">{fmt(step.at)}</Typography>
                <Box>
                  <Typography variant="body2" sx={{ fontWeight: WEIGHTY.includes(step.step) ? 700 : 500 }}>
                    {STEP_LABEL[step.step]}
                    {step.kind && ` — ${about?.thing?.what ?? KIND_LABEL[step.kind]}`}</Typography>
                  <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                    {step.actor_name ? `${step.actor_name}${roleName(step.actor_role) ? `, ${roleName(step.actor_role)}` : ''}`
                      : step.step === 'CAPTURED' ? 'The platform' : 'Somebody no longer on the system'}
                    {more && ` · ${more}`}</Typography>
                  {step.reason && <Typography variant="body2">{step.reason}</Typography>}
                </Box>
              </Box>
            )
          })}
        </GlassCard>
      )}

      <SealDialog open={asking === 'seal'} file={file} onClose={() => setAsking(null)} onSealed={done} />
      <ExportDialog open={asking === 'export'} file={file} onClose={() => setAsking(null)}
                    onExported={(result) => { setExported(result); return done() }} />
      <DisclosureDialog open={asking === 'disclose'} file={file} onClose={() => setAsking(null)} onRecorded={done} />
      <WhyDialog open={asking === 'lift'} title="Lift the holds this package placed" label="Why they are being lifted"
                 confirm="Lift the holds" min={5}
                 hint="The package stays sealed and its manifest stays. Its evidence is from then on kept only as long as the retention rules keep anything, and may be deleted."
                 onClose={() => setAsking(null)} onConfirm={(text) => releasePackageHolds(id, text).then(done)} />
    </Box>
  )
}

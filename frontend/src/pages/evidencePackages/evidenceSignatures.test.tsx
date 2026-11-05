/**
 * Evidence signatures on the evidence screens (Phase 7 of the known limits, 5 November 2026): a sealed package says
 * whether it was signed and by which key; an export is checked by uploading it, and the answer is the server's, line
 * by line; this installation's keys are listed to compare.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { ThemeProvider } from '@mui/material'
import { render, screen, fireEvent, waitFor, within } from '@/test/utils'
import { theme } from '@/theme/glassmorphism'
import * as api from '@/api/evidenceSignatures'
import type { CheckResult, SigningKeys } from '@/api/evidenceSignatures'
import { CheckExportButton, SealSignatureLine } from '@/components/evidence/EvidenceSignatures'

vi.mock('@/api/evidenceSignatures', async (orig) => {
  const real = await orig<typeof import('@/api/evidenceSignatures')>()
  return Object.fromEntries(Object.entries(real).map(([k, v]) => [k, typeof v === 'function' ? vi.fn() : v]))
})

function show(el: React.ReactNode) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } } })
  return render(<QueryClientProvider client={qc}><ThemeProvider theme={theme}>{el}</ThemeProvider></QueryClientProvider>,
                { wrapper: ({ children }) => <>{children}</> })
}

const FP = 'ab12cd34'.repeat(8)
const SPACED = FP.match(/.{4}/g)!.join(' ')
const KEYS: SigningKeys = {
  keys: [{ fingerprint: FP, spaced: SPACED, source: 'FILE', first_used_at: '2026-11-05T03:00:00Z', retired_at: null, in_use: true }],
  in_use: FP, not_signing_because: null, note: 'Anybody can make a key.',
}
const GENUINE: CheckResult = {
  genuine: true, signed: true, key_fingerprint: FP, key_spaced: SPACED, package_number: 'EVP-20261105-0001',
  exported_at: '2026-11-05T03:12:00Z', exported_by: 'Siti Rahman', seal: 'SIGNED', note: '', kept: false,
  checks: [{ what: 'The signature was made by the key in public_key.pem', ok: true, detail: `Key ${SPACED}.` },
           { what: 'Every file is the one that was exported', ok: true, detail: '7 file(s) compared.' }],
  platform: [{ what: "The key is this installation's", ok: true, detail: 'in use since 2026-11-05, and still in use' }],
}

beforeEach(() => {
  vi.clearAllMocks()
  vi.mocked(api.getSigningKeys).mockResolvedValue(KEYS)
  vi.mocked(api.checkExport).mockResolvedValue(GENUINE)
})

describe('a sealed package', () => {
  it('says it was signed and by which key, or why not, or that it was sealed before signing began', () => {
    const { rerender } = show(<SealSignatureLine signature={{ state: 'SIGNED', words: 'Sealed and signed',
      key_fingerprint: FP, key_spaced: SPACED, signed_at: '2026-11-05T03:00:00Z' }} />)
    expect(screen.getByTestId('seal-signature')).toHaveTextContent(`Sealed and signed`)
    expect(screen.getByTestId('seal-signature')).toHaveTextContent(`Key ${SPACED}`)
    rerender(<QueryClientProvider client={new QueryClient()}><ThemeProvider theme={theme}>
      <SealSignatureLine signature={{ state: 'NOT_SIGNED', words: 'Sealed, not signed: the signing key could not be read',
        key_fingerprint: null, key_spaced: null, signed_at: '2026-11-05T03:00:00Z' }} /></ThemeProvider></QueryClientProvider>)
    expect(screen.getByTestId('seal-signature')).toHaveTextContent('not signed: the signing key could not be read')
    expect(screen.getByTestId('seal-signature')).not.toHaveTextContent('Key')
  })

  it('says nothing for a draft', () => {
    show(<SealSignatureLine signature={null} />)
    expect(screen.queryByTestId('seal-signature')).not.toBeInTheDocument()
  })
})

describe('checking an export', () => {
  it('uploads the chosen ZIP and gives the answer line by line, with the keys to compare', async () => {
    show(<CheckExportButton />)
    fireEvent.click(screen.getByRole('button', { name: 'Check an export' }))
    const dialog = await screen.findByRole('dialog')
    expect(await within(dialog).findByText(SPACED)).toBeInTheDocument()
    expect(within(dialog).getByRole('button', { name: 'Check it' })).toBeDisabled()
    const zip = new File([new Uint8Array([80, 75, 3, 4])], 'EVP-20261105-0001.zip', { type: 'application/zip' })
    fireEvent.change(within(dialog).getByTestId('export-file'), { target: { files: [zip] } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check it' }))
    await waitFor(() => expect(api.checkExport).toHaveBeenCalledWith(zip))
    const result = await within(dialog).findByTestId('check-result')
    expect(result).toHaveTextContent('Genuine: the platform signed this export')
    expect(result).toHaveTextContent('EVP-20261105-0001')
    expect(within(result).getAllByTestId('check-line').map((l) => l.textContent)).toEqual([
      expect.stringContaining('The signature was made by the key in public_key.pem'),
      expect.stringContaining('Every file is the one that was exported'),
      expect.stringContaining("The key is this installation's")])
  })

  it('says plainly when an export is not shown to be genuine, and when it is not signed at all', async () => {
    vi.mocked(api.checkExport).mockResolvedValueOnce({
      ...GENUINE, genuine: false,
      checks: [{ what: 'Every file is the one that was exported', ok: false, detail: 'Not as exported: originals/001-snapshot-x.jpg' }],
      platform: [] }).mockResolvedValueOnce({ ...GENUINE, genuine: false, signed: false, key_spaced: null,
      checks: [{ what: 'The export carries a signature', ok: false, detail: 'There is no signature.json.' }], platform: [] })
    show(<CheckExportButton />)
    fireEvent.click(screen.getByRole('button', { name: 'Check an export' }))
    const dialog = await screen.findByRole('dialog')
    const zip = new File([new Uint8Array([80, 75])], 'e.zip')
    fireEvent.change(within(dialog).getByTestId('export-file'), { target: { files: [zip] } })
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check it' }))
    expect(await within(dialog).findByTestId('check-result')).toHaveTextContent('Not shown to be genuine')
    expect(within(dialog).getByTestId('check-result')).toHaveTextContent('Not as exported: originals/001-snapshot-x.jpg')
    fireEvent.click(within(dialog).getByRole('button', { name: 'Check it' }))
    await waitFor(() => expect(within(dialog).getByTestId('check-result')).toHaveTextContent('This export is not signed'))
  })
})

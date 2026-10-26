import { render, screen, fireEvent, waitFor } from '@/test/utils'
import * as assistant from '@/api/aiAssistant'
import IcScanDialog from './IcScanDialog'
import { changed } from './icScanFormat'

vi.mock('@/api/aiAssistant', async (orig) => {
  const real = await orig<typeof import('@/api/aiAssistant')>()
  return { ...real, readIc: vi.fn(), correctIc: vi.fn() }
})

const PATIENT = { timeout: 8000 }
const READ = {
  draft_id: 'd1',
  fields: { nric_fin: 'S1234567D', full_name: 'TANAHKOW', date_of_birth: '1990-05-02', sex: 'M' },
  check_letter_ok: true,
  unread: ['race', 'nationality', 'country_of_birth', 'address'] as const,
  message: 'I scanned the IC of a new staff member. NRIC/FIN: *****567D; full name: TANAHKOW.',
}
const photo = (name: string) => new File([new Uint8Array([0xff, 0xd8])], name, { type: 'image/jpeg' })

beforeEach(() => vi.clearAllMocks())

describe('what counts as a correction', () => {
  it('ignores spaces at the ends and notices a real change', () => {
    expect(changed({ full_name: 'TAN' }, { full_name: ' TAN ' })).toBe(false)
    expect(changed({ full_name: 'TAN' }, { full_name: 'TAN AH KOW' })).toBe(true)
    expect(changed({}, { race: 'CHINESE' })).toBe(true)
  })
})

describe('scanning an IC', () => {
  it('reads the photos, lets the person correct a field, and gives the assistant the corrected details', async () => {
    vi.mocked(assistant.readIc).mockResolvedValue({ ...READ, unread: [...READ.unread] })
    vi.mocked(assistant.correctIc).mockResolvedValue({ ...READ, unread: [...READ.unread], message: 'corrected message' })
    const onUse = vi.fn()
    render(<IcScanDialog open onClose={() => {}} onUse={onUse} />)
    expect(screen.getByRole('button', { name: 'Read the card' })).toBeDisabled()
    const front = photo('front.jpg')
    fireEvent.change(screen.getByLabelText('Front of the card'), { target: { files: [front] } })
    fireEvent.click(screen.getByRole('button', { name: 'Read the card' }))
    await waitFor(() => expect(assistant.readIc).toHaveBeenCalledWith(front, null), PATIENT)
    expect(await screen.findByText("The number's check letter is correct", {}, PATIENT)).toBeInTheDocument()
    expect(screen.getByText(/Not read from the card: Race, Nationality, Country of birth, Address/)).toBeInTheDocument()
    const name = screen.getByLabelText('Full name')
    expect(name).toHaveValue('TANAHKOW')
    fireEvent.change(name, { target: { value: 'TAN AH KOW' } })
    fireEvent.click(screen.getByRole('button', { name: 'Use these details' }))
    await waitFor(() => expect(onUse).toHaveBeenCalledWith('corrected message'), PATIENT)
    expect(assistant.correctIc).toHaveBeenCalledWith('d1', expect.objectContaining({ full_name: 'TAN AH KOW', nric_fin: 'S1234567D' }))
  })

  it('sends nothing more when nothing was corrected, and warns when the check letter is wrong', async () => {
    vi.mocked(assistant.readIc).mockResolvedValue({ ...READ, unread: [], check_letter_ok: false })
    const onUse = vi.fn()
    render(<IcScanDialog open onClose={() => {}} onUse={onUse} />)
    fireEvent.change(screen.getByLabelText('Front of the card'), { target: { files: [photo('f.jpg')] } })
    fireEvent.change(screen.getByLabelText('Back of the card'), { target: { files: [photo('b.jpg')] } })
    fireEvent.click(screen.getByRole('button', { name: 'Read the card' }))
    expect(await screen.findByText(/check letter does not match/, {}, PATIENT)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'Use these details' }))
    await waitFor(() => expect(onUse).toHaveBeenCalledWith(READ.message), PATIENT)
    expect(assistant.correctIc).not.toHaveBeenCalled()
  })

  it('says why when the card cannot be read', async () => {
    vi.mocked(assistant.readIc).mockRejectedValue({ isAxiosError: true, response: { status: 403, data: { detail: 'Reading an IC is for people who may create staff.' } } })
    render(<IcScanDialog open onClose={() => {}} onUse={() => {}} />)
    fireEvent.change(screen.getByLabelText('Front of the card'), { target: { files: [photo('f.jpg')] } })
    fireEvent.click(screen.getByRole('button', { name: 'Read the card' }))
    expect(await screen.findByText('Reading an IC is for people who may create staff.', {}, PATIENT)).toBeInTheDocument()
  })
})

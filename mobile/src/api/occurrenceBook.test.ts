/**
 * What one shift hands the next: what goes on the wire.
 *
 * WHY THIS MATTERS
 *   The server's bodies forbid anything they have no field for. A summary is
 *   corrected as `final_text` and drafted for a `shift_id`; sent under any
 *   other name the request is refused whole, and a guard at the end of a
 *   twelve-hour shift is told the summary could not be saved.
 */
import {
  confirmShiftSummary, draftShiftSummary, editShiftSummary, getInstructions, getShiftSummary, markInstructionRead,
} from './occurrenceBook'
import { ENTRY_TYPES } from './dob'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: { get: jest.fn(), post: jest.fn(() => Promise.resolve({ data: { id: 'm1' } })),
               patch: jest.fn(() => Promise.resolve({ data: { id: 'm1' } })) },
}))

const mockGet = apiClient.get as jest.Mock
const mockPost = apiClient.post as jest.Mock
const mockPatch = apiClient.patch as jest.Mock

beforeEach(() => { mockGet.mockReset(); mockPost.mockClear(); mockPatch.mockClear() })

test('asks for the instructions in force, and gives the list', async () => {
  mockGet.mockResolvedValue({ data: { items: [{ id: 'n1' }], can_issue: false } })
  await expect(getInstructions()).resolves.toEqual([{ id: 'n1' }])
  expect(mockGet).toHaveBeenCalledWith('/api/v1/occurrence-book/instructions', { params: { state: 'in_force' } })
})

test('says an instruction has been read, with no body', async () => {
  await markInstructionRead('n1')
  expect(mockPost).toHaveBeenCalledWith('/api/v1/occurrence-book/instructions/n1/read')
})

test('asks for the summary of one shift, and gives none when there is none', async () => {
  mockGet.mockResolvedValue({ data: { items: [] } })
  await expect(getShiftSummary('sh1')).resolves.toBeNull()
  expect(mockGet).toHaveBeenCalledWith('/api/v1/occurrence-book/shift-summaries', { params: { shift_id: 'sh1' } })
  mockGet.mockResolvedValue({ data: { items: [{ id: 'm1' }, { id: 'm0' }] } })
  await expect(getShiftSummary('sh1')).resolves.toEqual({ id: 'm1' })
})

test('drafts for a shift, corrects under the field the server reads, and confirms with no body', async () => {
  await draftShiftSummary('sh1')
  await editShiftSummary('m1', 'Corrected words.')
  await confirmShiftSummary('m1')
  expect(mockPost.mock.calls[0]).toEqual(['/api/v1/occurrence-book/shift-summaries', { shift_id: 'sh1' }])
  expect(mockPatch).toHaveBeenCalledWith('/api/v1/occurrence-book/shift-summaries/m1', { final_text: 'Corrected words.' })
  expect(mockPost.mock.calls[1]).toEqual(['/api/v1/occurrence-book/shift-summaries/m1/confirm'])
})

test('the phone offers a delivery and something unusual as kinds of entry', () => {
  expect(ENTRY_TYPES).toContain('delivery')
  expect(ENTRY_TYPES).toContain('unusual_activity')
  expect(ENTRY_TYPES.slice(0, 4)).toEqual(['general', 'incident', 'unusual_activity', 'delivery'])
})

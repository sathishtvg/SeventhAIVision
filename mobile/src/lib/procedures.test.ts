/**
 * The rules of the procedure card, and what it puts on the wire.
 *
 * What a guard is shown beside an incident has to be recognisable as the
 * procedure — which one, which version, approved by whom — and never as the
 * app's own advice. That line is what these tests hold.
 */
import { NOT_AN_ANSWER, passageSource, procedureSource, worthAsking } from './procedures'
import { askProcedures, getProceduresForIncident } from '@/api/sop'
import { apiClient } from '@/api/client'

jest.mock('@/api/client', () => ({
  apiClient: { get: jest.fn(() => Promise.resolve({ data: { procedures: [], incident_types: [], why_none: 'None.' } })),
               post: jest.fn(() => Promise.resolve({ data: { words: ['fire'], passages: [], note: 'x' } })) },
}))

const mockGet = apiClient.get as jest.Mock
const mockPost = apiClient.post as jest.Mock

beforeEach(() => { mockGet.mockClear(); mockPost.mockClear() })

test('a procedure says which it is, which version, who approved it and where it is for', () => {
  const version = { version_no: 3, approved_at: '2026-10-01T00:00:00Z', approved_by_name: 'Lim Mei Ling' }
  expect(procedureSource({ code: 'SOP-0001', version, site_name: 'Factory A' }))
    .toBe('SOP-0001 · version 3, approved by Lim Mei Ling · Factory A')
  expect(procedureSource({ code: 'SOP-0002', version: { ...version, approved_by_name: null }, site_name: null }))
    .toBe('SOP-0002 · version 3, approved · every site')
})

test('a passage that was found says where it is from', () => {
  expect(passageSource({ procedure: { id: 'd1', code: 'SOP-0001', title: 'Fire alarm' },
                         version: { version_no: 2, approved_by_name: 'Lim Mei Ling' } }))
    .toBe('SOP-0001 Fire alarm · version 2')
})

test('a question is sent only when it has something to look for', () => {
  expect([worthAsking(''), worthAsking(' a '), worthAsking('  '), worthAsking('fire'), worthAsking(' ok ')])
    .toEqual([false, false, false, true, true])
})

test('what is found is called a procedure’s words, not an answer', () => {
  expect(NOT_AN_ANSWER).toMatch(/words of approved procedures, as approved/)
  expect(NOT_AN_ANSWER).toMatch(/Nothing was written in answer/)
})

test('asks for the procedure for one incident', async () => {
  await expect(getProceduresForIncident('i1')).resolves.toEqual({ procedures: [], incident_types: [], why_none: 'None.' })
  expect(mockGet).toHaveBeenCalledWith('/api/v1/sop/for-incident/i1')
})

test('asks the library with the question and nothing the server has no field for', async () => {
  await askProcedures('fire alarm')
  expect(mockPost).toHaveBeenCalledWith('/api/v1/sop/ask', { question: 'fire alarm' })
})

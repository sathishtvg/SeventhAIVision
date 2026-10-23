/**
 * My Face: registering, and what a guard is told after a check-in
 * (docs/plans/2026-10-23-staff-face-registration-design.md).
 */
import { faceCheckMessage, FACE_UPLOAD_TIMEOUT_MS, getMyFaceChecks, myFacePhotoUrl, registerMyFace, type FaceCheck } from './faces'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: {
    defaults: { baseURL: 'http://192.168.1.6:8000/' },
    post: jest.fn(() => Promise.resolve({ data: { registered: true } })),
    get: jest.fn(() => Promise.resolve({ data: [] })),
  },
}))

const mockPost = apiClient.post as jest.Mock
const mockGet = apiClient.get as jest.Mock

beforeEach(() => { mockPost.mockClear(); mockGet.mockClear() })

const partsOf = (form: FormData) => ((form as any)._parts ?? []) as [string, unknown][]

test('registering sends the selfie and the agreement, and waits for the face check', async () => {
  await registerMyFace('file:///selfie.jpg', true)
  const [url, body, config] = mockPost.mock.calls[0]
  expect(url).toBe('/api/v1/faces/me')
  expect(config.headers['Content-Type']).toBe('multipart/form-data')
  expect(config.timeout).toBe(FACE_UPLOAD_TIMEOUT_MS)
  expect(FACE_UPLOAD_TIMEOUT_MS).toBeGreaterThanOrEqual(60_000)
  const parts = partsOf(body)
  expect(parts.find(([k]) => k === 'photo')?.[1]).toMatchObject({ uri: 'file:///selfie.jpg', type: 'image/jpeg' })
  expect(parts.find(([k]) => k === 'consent')?.[1]).toBe('true')
})

test('the check of one shift is asked for by its id', async () => {
  await getMyFaceChecks('shift-9')
  expect(mockGet).toHaveBeenCalledWith('/api/v1/faces/me/checks', { params: { shift_id: 'shift-9' } })
})

test('the photo on file carries the token in its address, for an Image', () => {
  expect(myFacePhotoUrl('u1', 'a.b.c')).toBe('http://192.168.1.6:8000/api/v1/faces/u1/photo?token=a.b.c')
  expect(myFacePhotoUrl('u1', null)).toBeNull()
})

const check = (outcome: FaceCheck['outcome']): FaceCheck => ({
  which: 'check_in', outcome, similarity: null, min_similarity: 0.4, reason: null, created_at: '2026-10-23T08:00:00Z',
})

test('a guard is told when the face did not match, and when none is registered - and not otherwise', () => {
  expect(faceCheckMessage(check('MISMATCH'))?.body).toContain('supervisor will review')
  expect(faceCheckMessage(check('NO_FACE'))?.body).toContain('My Face')
  expect(faceCheckMessage(check('MATCHED'))).toBeNull()
  expect(faceCheckMessage(check('NOT_CHECKED'))).toBeNull()
  expect(faceCheckMessage(undefined)).toBeNull()
})

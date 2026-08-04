/**
 * Training client.
 *
 * The load-bearing behaviour here is answer persistence. Answers are PUT one
 * at a time so a guard who gets pulled away mid-quiz — which is the normal
 * case, not the edge case — does not lose the work when the OS kills the
 * backgrounded app. If the payload shape drifted, the server would merge
 * nothing and "Resume" would silently show a blank quiz.
 */
import {
  answerQuestion, getAttempt, listCourses, listMyAttempts, startAttempt, submitAttempt,
} from './training'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: {
    get: jest.fn(() => Promise.resolve({ data: [] })),
    post: jest.fn(() => Promise.resolve({ data: {} })),
    put: jest.fn(() => Promise.resolve({ data: {} })),
  },
}))

const mockGet = apiClient.get as jest.Mock
const mockPost = apiClient.post as jest.Mock
const mockPut = apiClient.put as jest.Mock

beforeEach(() => {
  mockGet.mockClear().mockResolvedValue({ data: [] })
  mockPost.mockClear().mockResolvedValue({ data: {} })
  mockPut.mockClear().mockResolvedValue({ data: {} })
})

test('lists only active courses', async () => {
  // An inactive course must not be offered — a guard could otherwise complete
  // training that has been retired and believe they are current.
  await listCourses()
  expect(mockGet.mock.calls[0][0]).toBe('/api/v1/training/courses')
  expect(mockGet.mock.calls[0][1].params).toEqual({ is_active: true })
})

test('starting an attempt posts to the course attempts sub-resource', async () => {
  mockPost.mockResolvedValueOnce({ data: { attempt_id: 'a-1', questions: [] } })
  const r = await startAttempt('course-9')
  expect(mockPost.mock.calls[0][0]).toBe('/api/v1/training/courses/course-9/attempts')
  expect(r.attempt_id).toBe('a-1')
})

test('resuming fetches the attempt with its saved answers', async () => {
  mockGet.mockResolvedValueOnce({
    data: { attempt_id: 'a-1', status: 'in_progress', answers: { q1: 2 }, questions: [] },
  })
  const r = await getAttempt('a-1')
  expect(mockGet.mock.calls[0][0]).toBe('/api/v1/training/attempts/a-1')
  expect(r.answers).toEqual({ q1: 2 })
})

test('answering sends question_id and selected_index in the body', async () => {
  // Field names must match the server's merge exactly; a rename here fails
  // silently because the endpoint returns 200 either way.
  await answerQuestion('a-1', 'q-7', 3)
  const [url, body] = mockPut.mock.calls[0]
  expect(url).toBe('/api/v1/training/attempts/a-1/answer')
  expect(body).toEqual({ question_id: 'q-7', selected_index: 3 })
})

test('index 0 is sent as 0, not dropped as falsy', async () => {
  // The first option is a legitimate answer. A truthiness check anywhere in
  // this path would turn "picked option A" into "unanswered".
  await answerQuestion('a-1', 'q-7', 0)
  expect(mockPut.mock.calls[0][1]).toEqual({ question_id: 'q-7', selected_index: 0 })
})

test('submitting posts to the submit sub-resource and returns the score', async () => {
  mockPost.mockResolvedValueOnce({
    data: { score: 67, passed: false, correct_count: 2, total_count: 3 },
  })
  const r = await submitAttempt('a-1')
  expect(mockPost.mock.calls[0][0]).toBe('/api/v1/training/attempts/a-1/submit')
  expect(r).toMatchObject({ score: 67, passed: false, correct_count: 2, total_count: 3 })
})

test('listing own attempts passes the status filter through', async () => {
  await listMyAttempts({ status: 'in_progress' })
  expect(mockGet.mock.calls[0][0]).toBe('/api/v1/training/attempts')
  expect(mockGet.mock.calls[0][1].params).toEqual({ status: 'in_progress' })
})

test('a failed answer save rejects so the UI can surface it', async () => {
  mockPut.mockRejectedValueOnce(new Error('offline'))
  await expect(answerQuestion('a-1', 'q-7', 1)).rejects.toThrow('offline')
})

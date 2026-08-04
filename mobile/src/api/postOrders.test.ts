/**
 * Post-order client.
 *
 * The acknowledgement POST is a compliance record — it evidences that this
 * guard read the current version of the site's standing instructions. If it
 * targeted the wrong id, or silently swallowed a failure, the record would
 * claim something untrue about a person. Small surface, high consequence.
 */
import { acknowledgePostOrder, getPostOrder, listPostOrders } from './postOrders'
import { apiClient } from './client'

jest.mock('./client', () => ({
  apiClient: {
    get: jest.fn(() => Promise.resolve({ data: [] })),
    post: jest.fn(() => Promise.resolve({ data: { ok: true } })),
  },
}))

const mockGet = apiClient.get as jest.Mock
const mockPost = apiClient.post as jest.Mock

beforeEach(() => {
  mockGet.mockClear().mockResolvedValue({ data: [] })
  mockPost.mockClear().mockResolvedValue({ data: { ok: true } })
})

test('lists post orders without a site filter by default', async () => {
  await listPostOrders()
  const [url, config] = mockGet.mock.calls[0]
  expect(url).toBe('/api/v1/post-orders')
  expect(config.params).toBeUndefined()
})

test('passes site_id through when filtering', async () => {
  await listPostOrders('site-7')
  expect(mockGet.mock.calls[0][1].params).toEqual({ site_id: 'site-7' })
})

test('fetches a single order by id', async () => {
  mockGet.mockResolvedValueOnce({ data: { id: 'po-1' } })
  const r = await getPostOrder('po-1')
  expect(mockGet.mock.calls[0][0]).toBe('/api/v1/post-orders/po-1')
  expect(r).toEqual({ id: 'po-1' })
})

test('acknowledge posts to the acknowledge sub-resource for that order', async () => {
  await acknowledgePostOrder('po-42')
  expect(mockPost).toHaveBeenCalledTimes(1)
  expect(mockPost.mock.calls[0][0]).toBe('/api/v1/post-orders/po-42/acknowledge')
})

test('a failed acknowledgement rejects rather than resolving quietly', async () => {
  // The screen shows an error and leaves the order unacknowledged. Swallowing
  // this would tell the guard they are compliant when the server never
  // recorded it.
  mockPost.mockRejectedValueOnce(new Error('offline'))
  await expect(acknowledgePostOrder('po-42')).rejects.toThrow('offline')
})

test('unwraps the response body rather than returning the axios envelope', async () => {
  mockGet.mockResolvedValueOnce({ data: [{ id: 'po-1', title: 'Fire procedure' }] })
  const r = await listPostOrders()
  expect(Array.isArray(r)).toBe(true)
  expect(r[0].title).toBe('Fire procedure')
})

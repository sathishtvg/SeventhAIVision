/**
 * Where an evidence picture is asked for.
 *
 * WHY THIS MATTERS
 *   An <Image> needs an absolute address, so this module builds one. It built
 *   it from a constant read once from the build's environment, defaulting to
 *   http://localhost:8000 — and on a phone, localhost is the phone. Whatever
 *   server a guard signed in to, every evidence thumbnail was requested from a
 *   server that was not there, and the screen showed a grid of blanks with no
 *   error. Every other module here already asked the client where it was
 *   pointed.
 */
import { apiClient, setApiBaseUrl } from './client'
import { evidenceFileUrl, evidenceImageUrl, getEvidenceFileHeaders } from './evidence'

test('an evidence picture is asked of the server the app is signed in to', () => {
  setApiBaseUrl('https://vision.example.sg')
  expect(evidenceFileUrl('e1')).toBe('https://vision.example.sg/api/v1/evidence/e1/file')
})

test('it follows the server when a different one is chosen at sign-in', () => {
  setApiBaseUrl('https://vision.example.sg')
  setApiBaseUrl('http://192.168.1.20:8000/')
  expect(evidenceFileUrl('e2')).toBe('http://192.168.1.20:8000/api/v1/evidence/e2/file')
  expect(evidenceFileUrl('e2')).not.toContain('localhost')
})

test('the request carries the signed-in token, which an Image cannot add by itself', () => {
  apiClient.defaults.headers.common['Authorization'] = 'Bearer t'
  expect(getEvidenceFileHeaders()).toEqual({ Authorization: 'Bearer t' })
  delete apiClient.defaults.headers.common['Authorization']
  expect(getEvidenceFileHeaders()).toEqual({})
})

test('a picture the Evidence screen shows carries the token in its address, and a thumbnail asks for a width', () => {
  // An <Image> on the phone did not get the pictures with a header; the web's /image endpoint takes the token
  // in the address instead, and downscales when given w.
  setApiBaseUrl('http://192.168.1.6:8000')
  expect(evidenceImageUrl('e3', 'a.b.c', 336)).toBe('http://192.168.1.6:8000/api/v1/evidence/e3/image?token=a.b.c&w=336')
  expect(evidenceImageUrl('e3', 'a.b.c')).toBe('http://192.168.1.6:8000/api/v1/evidence/e3/image?token=a.b.c')
  expect(evidenceImageUrl('e3', null, 336)).toBeNull()
})

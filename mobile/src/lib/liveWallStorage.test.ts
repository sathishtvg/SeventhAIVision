/**
 * Live-wall camera persistence.
 *
 * Both functions swallow their errors by design — a failed read or write must
 * never crash the wall, it should just fall back to an empty selection. That
 * defensive shape is easy to get subtly wrong (returning undefined instead of
 * [], or letting a JSON.parse throw escape), so it is pinned here.
 */
import AsyncStorage from '@react-native-async-storage/async-storage'

import { loadLiveWallCameras, saveLiveWallCameras, type LiveWallEntry } from './liveWallStorage'

const entries: LiveWallEntry[] = [
  { cameraId: 'cam-1', streamId: 'st-1', cameraName: 'Lobby' },
  { cameraId: 'cam-2', streamId: 'st-2', cameraName: 'Car Park' },
]

beforeEach(async () => {
  await AsyncStorage.clear()
  jest.restoreAllMocks()
})

test('round-trips a saved selection', async () => {
  await saveLiveWallCameras(entries)
  expect(await loadLiveWallCameras()).toEqual(entries)
})

test('returns [] when nothing has been saved', async () => {
  expect(await loadLiveWallCameras()).toEqual([])
})

test('returns [] on corrupt JSON instead of throwing', async () => {
  await AsyncStorage.setItem('seventh_ai_live_wall_cameras', '{not json')
  expect(await loadLiveWallCameras()).toEqual([])
})

test('returns [] when the stored value is valid JSON but not an array', async () => {
  // A schema change or a bad write could leave an object here. Returning it
  // as-is would hand the wall a non-iterable and crash the render.
  await AsyncStorage.setItem('seventh_ai_live_wall_cameras', '{"cameraId":"x"}')
  expect(await loadLiveWallCameras()).toEqual([])
})

test('a storage read failure degrades to [] rather than propagating', async () => {
  jest.spyOn(AsyncStorage, 'getItem').mockRejectedValueOnce(new Error('storage full'))
  await expect(loadLiveWallCameras()).resolves.toEqual([])
})

test('a storage write failure is swallowed — the wall still works this session', async () => {
  jest.spyOn(AsyncStorage, 'setItem').mockRejectedValueOnce(new Error('quota exceeded'))
  await expect(saveLiveWallCameras(entries)).resolves.toBeUndefined()
})

test('saving an empty selection clears the wall', async () => {
  await saveLiveWallCameras(entries)
  await saveLiveWallCameras([])
  expect(await loadLiveWallCameras()).toEqual([])
})

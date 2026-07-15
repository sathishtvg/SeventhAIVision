import AsyncStorage from '@react-native-async-storage/async-storage'

const STORAGE_KEY = 'seventh_ai_live_wall_cameras'

export interface LiveWallEntry {
  cameraId: string
  streamId: string
  cameraName: string
}

export async function loadLiveWallCameras(): Promise<LiveWallEntry[]> {
  try {
    const raw = await AsyncStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed = JSON.parse(raw)
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export async function saveLiveWallCameras(entries: LiveWallEntry[]): Promise<void> {
  try {
    await AsyncStorage.setItem(STORAGE_KEY, JSON.stringify(entries))
  } catch {
    // Non-fatal — the wall just won't persist across app restarts this time.
  }
}

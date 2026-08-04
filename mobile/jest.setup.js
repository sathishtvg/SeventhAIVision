/**
 * Global test setup.
 *
 * Everything mocked here is a native module with no JS implementation in the
 * jest environment — importing the real one throws at require time, so these
 * are needed for a test to even load, not for convenience. Behavioural
 * stubbing belongs in the individual tests.
 */

// AsyncStorage: the offline outbox, live-wall persistence and the query
// persister all write through it. The community package ships an official
// in-memory mock for exactly this.
jest.mock('@react-native-async-storage/async-storage', () =>
  require('@react-native-async-storage/async-storage/jest/async-storage-mock'),
)

// expo-location: guard check-in reads coords and the `mocked` flag (the
// anti-fake-GPS control). Default to a real-looking, non-mocked fix; tests
// that care override it per case.
jest.mock('expo-location', () => ({
  requestForegroundPermissionsAsync: jest.fn(async () => ({ status: 'granted' })),
  getCurrentPositionAsync: jest.fn(async () => ({
    coords: { latitude: 1.3521, longitude: 103.8198, accuracy: 5 },
    mocked: false,
    timestamp: Date.now(),
  })),
}))

// expo-camera: used by the checkpoint scanner and the check-in selfie screen.
jest.mock('expo-camera', () => ({
  CameraView: 'CameraView',
  useCameraPermissions: jest.fn(() => [{ granted: true }, jest.fn()]),
}))

// Silence the RN animation helper warning that fires in every component test.
jest.mock('react-native/Libraries/Animated/NativeAnimatedHelper')

/**
 * FormData that behaves like React Native's, not the browser's.
 *
 * jsdom supplies the web-standard FormData, which coerces any non-Blob value
 * to a string — so the `{uri, name, type}` file descriptor the app appends for
 * a check-in selfie arrives as the literal "[object Object]". React Native's
 * FormData instead keeps the descriptor intact in `_parts`, and that is what
 * axios's RN adapter serializes.
 *
 * Asserting against the jsdom version would be verifying the wrong runtime:
 * a test could pass while the real upload sent a useless string. This minimal
 * stand-in mirrors RN's actual shape so upload tests check production
 * behaviour.
 */
class RNFormData {
  constructor() {
    this._parts = []
  }
  append(key, value) {
    this._parts.push([key, value])
  }
  getAll(key) {
    return this._parts.filter(([k]) => k === key).map(([, v]) => v)
  }
  entries() {
    return this._parts[Symbol.iterator]()
  }
}
global.FormData = RNFormData

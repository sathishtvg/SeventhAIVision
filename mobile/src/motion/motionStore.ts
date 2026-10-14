import AsyncStorage from '@react-native-async-storage/async-storage'
import { AccessibilityInfo } from 'react-native'
import { create } from 'zustand'

/**
 * Whether the phone plays each screen's scene.
 *
 * Two things decide it, and both must be known before anything moves:
 *   reduced  the phone's own "remove animations" setting. Followed, always.
 *   wanted   this person's own switch in Settings ("Screen animations"), kept
 *            on this phone. On unless they turn it off - somebody who moves
 *            between screens all shift may want them simply to appear.
 *
 * Until `init` has read both, neither is known and no scene is played: a
 * screen that opens before the answer is in opens the way it always has.
 */
const KEY = 'seventh_ai_screen_scenes'

interface MotionState {
  /** `null` until it has been read. */
  reduced: boolean | null
  wanted: boolean | null
  init: () => Promise<void>
  setWanted: (on: boolean) => Promise<void>
}

let listening = false

export const useMotionStore = create<MotionState>((set) => ({
  reduced: null,
  wanted: null,

  init: async () => {
    const [reduced, stored] = await Promise.all([
      // If the phone cannot say, it is taken to have asked for less: nothing is lost by not moving.
      AccessibilityInfo.isReduceMotionEnabled().catch(() => true),
      AsyncStorage.getItem(KEY).catch(() => null),
    ])
    set({ reduced, wanted: stored !== 'off' })
    if (!listening) {
      listening = true
      AccessibilityInfo.addEventListener('reduceMotionChanged', (now) => set({ reduced: now }))
    }
  },

  setWanted: async (on) => {
    set({ wanted: on })
    try {
      if (on) await AsyncStorage.removeItem(KEY)
      else await AsyncStorage.setItem(KEY, 'off')
    } catch {
      // The choice holds until the app is closed; it is not worth an error on screen.
    }
  },
}))

/** Scenes are played: the phone has not asked for less motion, and this person has not switched them off. */
export const scenesAllowed = (s: Pick<MotionState, 'reduced' | 'wanted'>) => s.reduced === false && s.wanted === true

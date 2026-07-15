import { create } from 'zustand'

interface FocusModeState {
  isFocusMode: boolean
  setFocusMode: (v: boolean) => void
}

/** When true, AppShell hides its own sidebar/topbar so a page (e.g. Live
 * Wall) can use the full viewport for control-room-style monitoring. */
export const useFocusModeStore = create<FocusModeState>((set) => ({
  isFocusMode: false,
  setFocusMode: (v) => set({ isFocusMode: v }),
}))

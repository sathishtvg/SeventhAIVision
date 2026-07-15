import * as SecureStore from 'expo-secure-store'
import { create } from 'zustand'

const SERVER_URL_KEY = 'seventh_ai_server_url'
export const DEFAULT_SERVER_URL = process.env.EXPO_PUBLIC_API_URL ?? 'http://10.0.2.2:8000'

interface ServerState {
  serverUrl: string
  setServerUrl: (url: string) => Promise<void>
  loadServerUrl: () => Promise<string>
}

export const useServerStore = create<ServerState>((set, get) => ({
  serverUrl: DEFAULT_SERVER_URL,

  setServerUrl: async (url: string) => {
    const trimmed = url.replace(/\/+$/, '')
    await SecureStore.setItemAsync(SERVER_URL_KEY, trimmed)
    set({ serverUrl: trimmed })
  },

  loadServerUrl: async () => {
    const stored = await SecureStore.getItemAsync(SERVER_URL_KEY)
    const url = stored ?? DEFAULT_SERVER_URL
    set({ serverUrl: url })
    return url
  },
}))

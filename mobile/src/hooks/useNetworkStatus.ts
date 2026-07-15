import { useEffect, useState } from 'react'
import { AppState, type AppStateStatus } from 'react-native'
import * as Network from 'expo-network'

async function checkNetwork(setIsOnline: (v: boolean) => void) {
  const state = await Network.getNetworkStateAsync()
  setIsOnline(state.isConnected ?? true)
}

export function useNetworkStatus() {
  const [isOnline, setIsOnline] = useState(true)

  useEffect(() => {
    checkNetwork(setIsOnline)

    // Re-check every 5 seconds
    const timer = setInterval(() => checkNetwork(setIsOnline), 5_000)

    // Also re-check when app comes back to foreground
    const handleAppState = (next: AppStateStatus) => {
      if (next === 'active') checkNetwork(setIsOnline)
    }
    const sub = AppState.addEventListener('change', handleAppState)

    return () => {
      clearInterval(timer)
      sub.remove()
    }
  }, [])

  return { isOnline }
}

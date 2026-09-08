import React, { useEffect } from 'react'
import { StatusBar } from 'expo-status-bar'
import { QueryClient } from '@tanstack/react-query'
import { PersistQueryClientProvider } from '@tanstack/react-query-persist-client'
import { NavigationContainer } from '@react-navigation/native'
import { registerRefreshFn, setApiBaseUrl } from '@/api/client'
import { useAuthStore } from '@/store/auth'
import { useServerStore } from '@/store/server'
import { usePushNotifications } from '@/hooks/usePushNotifications'
import { queryPersister } from '@/lib/queryPersister'
import { OfflineBanner } from '@/components/OfflineBanner'
import { ManDownGuard } from '@/components/ManDownGuard'
import { RootNavigator } from '@/navigation'

const CACHE_MAX_AGE_MS = 24 * 60 * 60 * 1000  // 24 hours

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 30_000,
      gcTime: CACHE_MAX_AGE_MS,
      networkMode: 'offlineFirst',
    },
  },
})

function AppInner() {
  usePushNotifications()
  return (
    <>
      <OfflineBanner />
      <RootNavigator />
      {/* Mounted at the root, not on a screen: a fall does not wait for the
          guard to open the right tab. Renders nothing until it fires. */}
      <ManDownGuard />
    </>
  )
}

export default function App() {
  const restoreSession = useAuthStore((s) => s.restoreSession)
  const refresh = useAuthStore((s) => s.refresh)
  const loadServerUrl = useServerStore((s) => s.loadServerUrl)

  useEffect(() => {
    registerRefreshFn(refresh)
    ;(async () => {
      const url = await loadServerUrl()
      setApiBaseUrl(url)
      await restoreSession()
    })()
  }, [])

  return (
    <PersistQueryClientProvider
      client={queryClient}
      persistOptions={{
        persister: queryPersister,
        maxAge: CACHE_MAX_AGE_MS,
      }}
    >
      <NavigationContainer>
        <StatusBar style="light" />
        <AppInner />
      </NavigationContainer>
    </PersistQueryClientProvider>
  )
}

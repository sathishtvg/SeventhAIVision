import { useEffect, useRef } from 'react'
import { Platform } from 'react-native'
import { useAuthStore } from '@/store/auth'
import { apiClient } from '@/api/client'

let _notificationsModule: any = null

async function loadNotifications() {
  if (_notificationsModule) return _notificationsModule
  try {
    _notificationsModule = await import('expo-notifications')
    return _notificationsModule
  } catch {
    return null
  }
}

async function registerForPushNotificationsAsync(): Promise<string | null> {
  const Notifications = await loadNotifications()
  if (!Notifications) return null

  if (Platform.OS === 'android') {
    await Notifications.setNotificationChannelAsync('alerts', {
      name: 'Security Alerts',
      importance: Notifications.AndroidImportance.MAX,
      vibrationPattern: [0, 250, 250, 250],
      lightColor: '#FF231F7C',
    })
  }

  const { status: existingStatus } = await Notifications.getPermissionsAsync()
  let finalStatus = existingStatus
  if (existingStatus !== 'granted') {
    const { status } = await Notifications.requestPermissionsAsync()
    finalStatus = status
  }
  if (finalStatus !== 'granted') return null

  try {
    const tokenData = await Notifications.getExpoPushTokenAsync()
    return tokenData.data
  } catch {
    return null
  }
}

export function usePushNotifications() {
  const accessToken = useAuthStore((s) => s.accessToken)
  const registeredRef = useRef(false)

  useEffect(() => {
    if (!accessToken || registeredRef.current) return

    registerForPushNotificationsAsync().then(async (expoPushToken) => {
      if (!expoPushToken) return
      try {
        await apiClient.post('/api/v1/users/me/push-token', { token: expoPushToken })
        registeredRef.current = true
      } catch {
        // Registration failure is non-critical — app still works without push
      }
    })
  }, [accessToken])
}

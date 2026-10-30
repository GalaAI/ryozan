/**
 * Push notification utilities: Web Push registration and management.
 *
 * Uses VAPID (no FCM/GCM). The service worker at /sw.js handles
 * display and click events. This module handles:
 * - Feature detection
 * - Service worker registration
 * - Push subscription lifecycle
 * - Backend sync (subscribe/unsubscribe)
 */

import { API_BASE } from './api'

/**
 * Check if the browser supports push notifications.
 */
export function isPushSupported(): boolean {
  return (
    'serviceWorker' in navigator &&
    'PushManager' in window &&
    'Notification' in window
  )
}

/**
 * Get the current notification permission state.
 */
export function getPushPermissionState(): NotificationPermission | 'unsupported' {
  if (!isPushSupported()) return 'unsupported'
  return Notification.permission
}

/**
 * Convert a base64url string to a Uint8Array for applicationServerKey.
 */
function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4)
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/')
  const rawData = window.atob(base64)
  const outputArray = new Uint8Array(rawData.length)
  for (let i = 0; i < rawData.length; i++) {
    outputArray[i] = rawData.charCodeAt(i)
  }
  return outputArray
}

/**
 * Fetch the VAPID public key from the backend.
 */
async function fetchVapidKey(): Promise<string> {
  const res = await fetch(`${API_BASE}/push/vapid-key`)
  if (!res.ok) throw new Error('Failed to fetch VAPID key')
  const data = await res.json()
  return data.public_key
}

/**
 * Get the auth token from localStorage for authenticated requests.
 */
function getAuthToken(): string | null {
  return localStorage.getItem('ryozan_token')
}

/**
 * Register for push notifications.
 *
 * 1. Registers the service worker
 * 2. Requests notification permission
 * 3. Subscribes to PushManager with VAPID key
 * 4. Sends subscription to backend
 *
 * Throws on failure (permission denied, network error, etc.)
 */
export async function registerPushNotifications(): Promise<void> {
  if (!isPushSupported()) {
    throw new Error('Push notifications are not supported in this browser')
  }

  // Register service worker
  const registration = await navigator.serviceWorker.register('/sw.js', {
    scope: '/',
  })

  // Wait for the service worker to be ready
  await navigator.serviceWorker.ready

  // Request permission
  const permission = await Notification.requestPermission()
  if (permission !== 'granted') {
    throw new Error('Notification permission denied')
  }

  // Get VAPID public key
  const vapidKey = await fetchVapidKey()
  const applicationServerKey = urlBase64ToUint8Array(vapidKey)

  // Subscribe to push
  const subscription = await registration.pushManager.subscribe({
    userVisibleOnly: true,
    applicationServerKey: applicationServerKey.buffer as ArrayBuffer,
  })

  // Extract keys
  const subscriptionJson = subscription.toJSON()
  if (!subscriptionJson.endpoint || !subscriptionJson.keys) {
    throw new Error('Invalid push subscription: missing endpoint or keys')
  }

  // Send to backend
  const token = getAuthToken()
  const res = await fetch(`${API_BASE}/push/subscribe`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    },
    body: JSON.stringify({
      endpoint: subscriptionJson.endpoint,
      keys: {
        p256dh: subscriptionJson.keys.p256dh,
        auth: subscriptionJson.keys.auth,
      },
    }),
  })

  if (!res.ok) {
    throw new Error('Failed to save push subscription to server')
  }
}

/**
 * Unregister from push notifications.
 *
 * 1. Unsubscribes from PushManager
 * 2. Tells backend to remove the subscription
 */
export async function unregisterPushNotifications(): Promise<void> {
  if (!isPushSupported()) return

  const registration = await navigator.serviceWorker.getRegistration('/')
  if (registration) {
    const subscription = await registration.pushManager.getSubscription()
    if (subscription) {
      await subscription.unsubscribe()
    }
  }

  // Tell backend to remove all subscriptions for this user
  const token = getAuthToken()
  if (token) {
    await fetch(`${API_BASE}/push/subscribe`, {
      method: 'DELETE',
      headers: {
        Authorization: `Bearer ${token}`,
      },
    })
  }
}

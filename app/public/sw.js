/**
 * BTTCO Service Worker — handles push notifications.
 *
 * - Displays push notifications with title, body, icon from payload
 * - Navigates to data.url on notification click
 * - Self-activates immediately (no waiting for old tabs to close)
 */

/* eslint-disable no-restricted-globals */

self.addEventListener('install', (event) => {
  // Activate immediately — don't wait for old SW to release
  self.skipWaiting()
})

self.addEventListener('activate', (event) => {
  // Claim all open tabs immediately
  event.waitUntil(self.clients.claim())
})

self.addEventListener('push', (event) => {
  if (!event.data) return

  let payload
  try {
    payload = event.data.json()
  } catch {
    payload = {
      title: 'Ryozan Studio',
      body: event.data.text(),
    }
  }

  const options = {
    body: payload.body || '',
    icon: payload.icon || '/assets/images/logo-icon.png',
    badge: '/assets/images/logo-icon.png',
    data: payload.data || {},
    tag: payload.tag || 'bttco-notification',
    renotify: true,
    vibrate: [200, 100, 200],
  }

  event.waitUntil(
    self.registration.showNotification(payload.title || 'Ryozan Studio', options)
  )
})

self.addEventListener('notificationclick', (event) => {
  event.notification.close()

  const url = (event.notification.data && event.notification.data.url) || '/'

  event.waitUntil(
    self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
      // If a matching tab is already open, focus it
      for (const client of clientList) {
        if (client.url.includes(self.location.origin) && 'focus' in client) {
          client.navigate(url)
          return client.focus()
        }
      }
      // Otherwise open a new tab
      return self.clients.openWindow(url)
    })
  )
})

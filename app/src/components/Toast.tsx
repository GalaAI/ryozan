/**
 * Toast: Lightweight notification toast system for the dashboard.
 *
 * Usage:
 *   import { useToast, ToastContainer } from '../components/Toast'
 *
 *   // In component:
 *   const toast = useToast()
 *   toast.success('Saved!')
 *   toast.error('Something went wrong')
 *   toast.info('Message sent')
 *
 *   // In App root:
 *   <ToastContainer />
 */

import { useState, useCallback, createContext, useContext, useRef, type ReactNode } from 'react'

type ToastType = 'success' | 'error' | 'info'

interface Toast {
  id: number
  message: string
  type: ToastType
}

interface ToastContextValue {
  success: (msg: string) => void
  error: (msg: string) => void
  info: (msg: string) => void
}

const ToastContext = createContext<ToastContextValue | null>(null)

let _id = 0

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([])
  const timers = useRef(new Map<number, ReturnType<typeof setTimeout>>())

  const addToast = useCallback((message: string, type: ToastType) => {
    const id = ++_id
    setToasts((prev) => {
      const deduped = prev.filter((toast) => {
        const isDuplicate = toast.message === message && toast.type === type
        if (isDuplicate) {
          const timer = timers.current.get(toast.id)
          if (timer) clearTimeout(timer)
          timers.current.delete(toast.id)
        }
        return !isDuplicate
      })
      return [...deduped.slice(-3), { id, message, type }]
    })
    const timer = setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
      timers.current.delete(id)
    }, 4000)
    timers.current.set(id, timer)
  }, [])

  const value: ToastContextValue = {
    success: useCallback((msg: string) => addToast(msg, 'success'), [addToast]),
    error: useCallback((msg: string) => addToast(msg, 'error'), [addToast]),
    info: useCallback((msg: string) => addToast(msg, 'info'), [addToast]),
  }

  return (
    <ToastContext.Provider value={value}>
      {children}
      {/* Toast container */}
      <div style={{
        position: 'fixed',
        // Clear the iPhone status bar and notch. Installed to the home screen
        // the app is full-bleed, so a flat 1.25rem put every toast underneath
        // the system clock and made errors literally unreadable.
        top: 'calc(env(safe-area-inset-top, 0px) + 1.25rem)',
        right: 'calc(env(safe-area-inset-right, 0px) + 1.5rem)',
        left: 'calc(env(safe-area-inset-left, 0px) + 1.5rem)',
        zIndex: 9999,
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'flex-end',
        gap: '0.5rem',
        pointerEvents: 'none',
      }}>
        {toasts.map((t) => (
          <div
            key={t.id}
            style={{
              pointerEvents: 'auto',
              padding: '0.75rem 1.25rem',
              borderRadius: '10px',
              fontSize: '0.85rem',
              fontWeight: 500,
              color: '#fff',
              background:
                t.type === 'success' ? 'rgba(76, 175, 80, 0.92)' :
                t.type === 'error' ? 'rgba(217, 83, 79, 0.92)' :
                'rgba(250, 174, 21, 0.92)',
              backdropFilter: 'blur(12px)',
              border: `1px solid ${
                t.type === 'success' ? 'rgba(76, 175, 80, 0.5)' :
                t.type === 'error' ? 'rgba(217, 83, 79, 0.5)' :
                'rgba(250, 174, 21, 0.5)'
              }`,
              boxShadow: '0 4px 20px rgba(0, 0, 0, 0.4)',
              animation: 'toast-slide-in 0.25s ease-out',
              maxWidth: '360px',
            }}
            onClick={() => {
              const timer = timers.current.get(t.id)
              if (timer) clearTimeout(timer)
              timers.current.delete(t.id)
              setToasts((prev) => prev.filter((x) => x.id !== t.id))
            }}
          >
            {t.message}
          </div>
        ))}
      </div>

      {/* Keyframe animation */}
      <style>{`
        @keyframes toast-slide-in {
          from { opacity: 0; transform: translateY(-12px) scale(0.95); }
          to { opacity: 1; transform: translateY(0) scale(1); }
        }
      `}</style>
    </ToastContext.Provider>
  )
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast(): ToastContextValue {
  const ctx = useContext(ToastContext)
  if (!ctx) throw new Error('useToast must be used within ToastProvider')
  return ctx
}

import { useState, useCallback } from 'react'

export function useLightbox(total: number) {
  const [open, setOpen] = useState(false)
  const [index, setIndex] = useState(0)

  const openAt = useCallback((i: number) => {
    setIndex(i)
    setOpen(true)
  }, [])

  const close = useCallback(() => setOpen(false), [])

  const next = useCallback(() => setIndex(i => (i + 1) % total), [total])
  const prev = useCallback(() => setIndex(i => (i - 1 + total) % total), [total])

  return { open, index, openAt, close, next, prev }
}

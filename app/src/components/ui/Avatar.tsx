/**
 * Avatar component with built-in error handling.
 *
 * Per Ryozan design system rules:
 * - display: block on img to prevent alt text inline rendering
 * - font-size: 0; color: transparent to suppress alt text visibility
 * - object-fit: cover on img, overflow: hidden on container
 * - Fallback initial (first letter of name) when avatar_url is null or fails to load
 */

import { useState } from 'react'

interface AvatarProps {
  url: string | null | undefined
  name: string
  size?: number
  className?: string
}

export default function Avatar({ url, name, size = 34, className = '' }: AvatarProps) {
  const [broken, setBroken] = useState(false)
  const initial = (name || '?').charAt(0).toUpperCase()
  const showImg = url && !broken

  return (
    <div
      className={`avatar-safe ${className}`}
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: 'var(--gold)',
        color: 'var(--bg)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        fontFamily: 'var(--font-heading)',
        fontSize: size * 0.4,
        fontWeight: 600,
        overflow: 'hidden',
        flexShrink: 0,
      }}
    >
      {showImg ? (
        <img
          src={url}
          alt={name}
          onError={() => setBroken(true)}
          style={{
            display: 'block',
            width: '100%',
            height: '100%',
            objectFit: 'cover',
            fontSize: 0,
            color: 'transparent',
          }}
        />
      ) : (
        initial
      )}
    </div>
  )
}

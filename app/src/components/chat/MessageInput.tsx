/**
 * MessageInput: Text input + send button for chat UIs.
 *
 * Accepts an optional `renderLeft` slot for additional controls
 * like the template button used in Courier.
 */

import { useEffect, useRef, type ReactNode } from 'react'

interface MessageInputProps {
  value: string
  onChange: (value: string) => void
  onSend: () => void
  disabled?: boolean
  placeholder?: string
  renderTop?: ReactNode
  renderLeft?: ReactNode
  className?: string
}

export default function MessageInput({
  value,
  onChange,
  onSend,
  disabled,
  placeholder = 'Type a message...',
  renderTop,
  renderLeft,
  className,
}: MessageInputProps) {
  const inputRef = useRef<HTMLTextAreaElement | null>(null)

  useEffect(() => {
    const element = inputRef.current
    if (!element) return
    element.style.height = '0px'
    element.style.height = `${Math.min(element.scrollHeight, 180)}px`
  }, [value])

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      onSend()
    }
  }

  return (
    <div className={className || 'courier-input-bar'}>
      {(renderTop || renderLeft) && (
        <div className="courier-input-secondary">
          {renderTop}
          {renderLeft}
        </div>
      )}
      <div className="courier-input-primary">
        <div className="courier-input-stack">
          <textarea
            ref={inputRef}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder={placeholder}
            className="courier-input courier-input--textarea"
            disabled={disabled}
            rows={1}
            aria-label="Message text"
          />
        </div>
        <button
          type="button"
          onClick={onSend}
          className="courier-send-btn"
          disabled={disabled || !value.trim()}
          aria-label="Send message"
        >
          <svg viewBox="0 0 24 24" fill="currentColor" width="16" height="16">
            <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
          </svg>
        </button>
      </div>
    </div>
  )
}

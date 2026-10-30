/**
 * GalaPersonalitySlider: controls how much of Gala's dark-fantasy-wizard
 * personality bleeds into portal messages, notifications, and quest text.
 *
 * 0   = "Professional": clean, neutral copy. No lore.
 * 50  = "Balanced": subtle wit, light references.
 * 75  = "Classic Gala": wizard mode with dry humor.
 * 100 = "Full Chaos": maximum snark, deep lore, brain rot.
 *
 * Persists to the user's profile via PATCH /users/me.
 */

import { useState, useRef, useCallback } from 'react'
import { users } from '../lib/api'
import { useAuthStore } from '../stores/auth'

const LABELS: Record<number, { name: string; desc: string }> = {
  0:   { name: 'Professional', desc: 'Clean and neutral. Just the facts.' },
  25:  { name: 'Subtle',       desc: 'Hints of personality. A raised eyebrow.' },
  50:  { name: 'Balanced',     desc: 'Wit meets wisdom. The sweet spot for most.' },
  75:  { name: 'Classic Gala', desc: 'Wizard mode. Dry humor, arcane references.' },
  100: { name: 'Full Chaos',   desc: 'Maximum snark. Deep lore. No filter.' },
}

function getLabel(value: number) {
  // Snap to nearest defined label
  const keys = Object.keys(LABELS).map(Number).sort((a, b) => a - b)
  let closest = keys[0]
  for (const k of keys) {
    if (Math.abs(k - value) < Math.abs(closest - value)) closest = k
  }
  return LABELS[closest]
}

export default function GalaPersonalitySlider() {
  const { user, fetchMe } = useAuthStore()
  const [value, setValue] = useState(user?.gala_personality ?? 75)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const save = useCallback(async (newVal: number) => {
    setSaving(true)
    setSaved(false)
    try {
      await users.updateProfile({ gala_personality: newVal })
      await fetchMe()
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch {
      // Silently fail. Slider still reflects local state.
    } finally {
      setSaving(false)
    }
  }, [fetchMe])

  const handleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const newVal = Number(e.target.value)
    setValue(newVal)
    // Debounce the API call
    if (debounceRef.current) clearTimeout(debounceRef.current)
    debounceRef.current = setTimeout(() => save(newVal), 600)
  }

  const label = getLabel(value)
  const pct = value / 100

  return (
    <div style={styles.wrapper}>
      <div style={styles.labelRow}>
        <span style={styles.labelName}>{label.name}</span>
        <span style={{
          ...styles.valueTag,
          opacity: saving ? 0.5 : 1,
        }}>
          {saved ? 'Saved' : `${value}%`}
        </span>
      </div>

      <input
        type="range"
        min={0}
        max={100}
        step={5}
        value={value}
        onChange={handleChange}
        style={{
          ...styles.slider,
          background: `linear-gradient(to right, #a67d43 ${pct * 100}%, rgba(166, 125, 67, 0.15) ${pct * 100}%)`,
        }}
      />

      <div style={styles.scaleRow}>
        <span style={styles.scaleLabel}>Professional</span>
        <span style={styles.scaleLabel}>Full Chaos</span>
      </div>

      <p style={styles.desc}>{label.desc}</p>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  wrapper: {
    display: 'flex',
    flexDirection: 'column',
    gap: '8px',
  },
  labelRow: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
  },
  labelName: {
    fontSize: '14px',
    fontWeight: 600,
    color: '#e8dfcf',
  },
  valueTag: {
    fontSize: '12px',
    fontWeight: 500,
    color: '#a67d43',
    transition: 'opacity 0.2s',
  },
  slider: {
    width: '100%',
    height: '6px',
    borderRadius: '3px',
    outline: 'none',
    cursor: 'pointer',
    WebkitAppearance: 'none' as never,
    appearance: 'none' as never,
  },
  scaleRow: {
    display: 'flex',
    justifyContent: 'space-between',
  },
  scaleLabel: {
    fontSize: '11px',
    color: '#7a7060',
  },
  desc: {
    fontSize: '12px',
    color: '#b9ad96',
    lineHeight: '1.4',
    margin: 0,
    marginTop: '4px',
  },
}

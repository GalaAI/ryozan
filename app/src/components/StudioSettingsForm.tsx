/**
 * StudioSettingsForm: the owner edits the studio's identity, booking days and
 * public site from the Hub. Saves apply at once, with no redeploy.
 *
 * The server owns validation and founder-only access; secrets and provider keys
 * stay in the environment and never appear here.
 */

import { useEffect, useMemo, useState, type FormEvent } from 'react'
import { Button, Chip, ChipGroup, SectionLabel, TextField } from '@ryozan/ui'
import { studio, type StudioSettingsEdit } from '../lib/api'
import { refreshStudioSettings } from '../hooks/useStudioSettings'
import { STUDIO_WEEKDAYS, formatHoursSetting, hourLabel, parseHoursSetting, studioTimezoneOptions, weekdayLongName } from '../lib/studio-settings'
import type { StudioWeekday } from '../lib/api'

type HoursKey = 'BOOKING_WORK_HOURS' | 'BOOKING_CONSULTATION_HOURS'
type TextKey = Exclude<keyof StudioSettingsEdit, 'BOOKING_CLOSED_WEEKDAYS' | 'BOOKING_REQUEST_ONLY_WEEKDAYS' | 'PUBLIC_SITE_MODE' | HoursKey>

const STUDIO_FIELDS: Array<{ key: TextKey; label: string; type?: string; hint?: string; rows?: number }> = [
  { key: 'STUDIO_NAME', label: 'Studio name', hint: 'On client email, texts, PDFs and your legal pages.' },
  { key: 'STUDIO_OWNER_NAME', label: 'Owner name' },
  { key: 'SHOP_EMAIL', label: 'Shop email', type: 'email', hint: 'Public contact address, and the reply-to on client email.' },
  { key: 'STUDIO_PHONE', label: 'Shop phone', type: 'tel' },
  { key: 'STUDIO_ADDRESS', label: 'Shop address', hint: 'Printed on email footers and PDFs.' },
  { key: 'STUDIO_ARTIST_ROSTER', label: 'Artist roster', rows: 4, hint: 'One artist per line. The AI agents read this.' },
]

const SITE_FIELDS: Array<{ key: TextKey; label: string; hint?: string; rows?: number }> = [
  { key: 'STUDIO_TAGLINE', label: 'Tagline' },
  { key: 'STUDIO_STORY', label: 'Our story', rows: 6, hint: 'Separate paragraphs with a blank line.' },
  { key: 'STUDIO_HOURS', label: 'Opening hours', rows: 4, hint: 'What the site shows, one row per line. Booking times follow Booking hours above.' },
  { key: 'STUDIO_HERO_IMAGE_URL', label: 'Hero image', hint: 'An https:// link or a site path.' },
  { key: 'STUDIO_GALLERY_IMAGE_URLS', label: 'Gallery images', hint: 'Comma-separated links.' },
  { key: 'STUDIO_SOCIAL_LINKS', label: 'Social links', hint: 'Comma-separated, like Instagram=https://instagram.com/your-studio' },
]

const csvDays = (value: string | undefined) => (value ?? '').split(',').map((day) => day.trim()).filter(Boolean)
const NEW_DAY_HOURS: [number, number] = [11, 18]
const fieldStack = { display: 'grid', gap: 'var(--space-3)' }
// @ryozan/ui has no multi-line field or select yet; these match TextField token for token.
const multilineStyle = {
  width: '100%',
  boxSizing: 'border-box',
  padding: 'var(--space-2) var(--space-3)',
  border: 'var(--hair) solid var(--panel-border)',
  borderRadius: 'var(--radius-md)',
  background: 'var(--bg-dark)',
  color: 'var(--text)',
  fontFamily: 'inherit',
  fontSize: 'var(--text-sm)',
  resize: 'vertical',
} as const
const selectStyle = { ...multilineStyle, width: 'auto', resize: undefined, colorScheme: 'dark' } as const

export default function StudioSettingsForm() {
  const zones = useMemo(() => studioTimezoneOptions(), [])
  const [values, setValues] = useState<StudioSettingsEdit | null>(null)
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState('')
  const [saved, setSaved] = useState(false)

  useEffect(() => {
    let active = true
    void studio.getEditableSettings()
      .then((next) => { if (active) setValues(next) })
      .catch((err: unknown) => { if (active) setError(err instanceof Error ? err.message : 'Could not load the settings.') })
    return () => { active = false }
  }, [])

  if (!values) return error ? <p role="alert">{error}</p> : <p role="status">Loading studio settings…</p>

  const set = (key: keyof StudioSettingsEdit, value: string) => {
    setSaved(false)
    setValues((current) => (current ? { ...current, [key]: value } : current))
  }

  const toggleDay = (key: 'BOOKING_CLOSED_WEEKDAYS' | 'BOOKING_REQUEST_ONLY_WEEKDAYS', day: StudioWeekday) => {
    const days = csvDays(values[key])
    set(key, (days.includes(day) ? days.filter((item) => item !== day) : [...days, day]).join(','))
  }

  const handleSubmit = async (event: FormEvent) => {
    event.preventDefault()
    setSaving(true)
    setError('')
    try {
      setValues(await studio.saveEditableSettings(values))
      await refreshStudioSettings()
      setSaved(true)
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : 'That did not save. Try again.')
    } finally {
      setSaving(false)
    }
  }

  const textField = ({ key, label, type, hint, rows }: { key: TextKey; label: string; type?: string; hint?: string; rows?: number }) => (
    rows ? (
      <label key={key} style={{ display: 'grid', gap: 'var(--space-2)' }}>
        <SectionLabel>{label}</SectionLabel>
        <textarea rows={rows} style={multilineStyle} value={values[key] ?? ''} onChange={(e) => set(key, e.target.value)} />
        {hint && <SectionLabel>{hint}</SectionLabel>}
      </label>
    ) : (
      <TextField key={key} label={label} hint={hint} type={type ?? 'text'} value={values[key] ?? ''} onChange={(e) => set(key, e.target.value)} />
    )
  )

  const dayChips = (key: 'BOOKING_CLOSED_WEEKDAYS' | 'BOOKING_REQUEST_ONLY_WEEKDAYS', label: string) => (
    <ChipGroup label={label}>
      <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
        {STUDIO_WEEKDAYS.map((day) => (
          <Chip key={day} type="button" selected={csvDays(values[key]).includes(day)} onClick={() => toggleDay(key, day)}>
            {weekdayLongName(day)}
          </Chip>
        ))}
      </div>
    </ChipGroup>
  )

  const hoursEditor = (key: HoursKey, label: string) => {
    const hours = parseHoursSetting(values[key])
    const setDay = (day: StudioWeekday, span: [number, number] | null) => {
      const next = { ...hours }
      if (span) next[day] = span
      else delete next[day]
      set(key, formatHoursSetting(next))
    }
    const hourSelect = (day: StudioWeekday, span: [number, number], end: 0 | 1, name: string) => (
      <select
        style={selectStyle}
        aria-label={`${weekdayLongName(day)} ${name}, ${label}`}
        value={span[end]}
        onChange={(e) => {
          const hour = Number(e.target.value)
          setDay(day, end === 0 ? [hour, span[1]] : [span[0], hour])
        }}
      >
        {Array.from({ length: 24 }, (_, hour) => <option key={hour} value={hour}>{hourLabel(hour)}</option>)}
      </select>
    )
    return (
      <ChipGroup label={label}>
        <div style={fieldStack}>
          {STUDIO_WEEKDAYS.map((day) => {
            const span = hours[day]
            return (
              <div key={day} style={{ display: 'flex', flexWrap: 'wrap', alignItems: 'center', gap: 'var(--space-2)' }}>
                <div style={{ minWidth: '6rem' }}>
                  <Chip type="button" selected={Boolean(span)} onClick={() => setDay(day, span ? null : NEW_DAY_HOURS)}>
                    {weekdayLongName(day)}
                  </Chip>
                </div>
                {span ? (
                  <>
                    {hourSelect(day, span, 0, 'opens')}
                    <SectionLabel>to</SectionLabel>
                    {hourSelect(day, span, 1, 'closes')}
                  </>
                ) : <SectionLabel>Closed</SectionLabel>}
              </div>
            )
          })}
        </div>
      </ChipGroup>
    )
  }

  return (
    <form aria-label="Studio settings" onSubmit={handleSubmit} style={{ display: 'grid', gap: 'var(--space-6)' }}>
      <section style={fieldStack}>
        <SectionLabel>Your studio</SectionLabel>
        {textField(STUDIO_FIELDS[0])}
        <TextField
          label="Time zone"
          list="studio-timezones"
          hint="Booking times, reminders and which day a payment lands on all follow this."
          value={values.STUDIO_TIMEZONE ?? ''}
          onChange={(e) => set('STUDIO_TIMEZONE', e.target.value)}
        />
        <datalist id="studio-timezones">{zones.map((zone) => <option key={zone} value={zone} />)}</datalist>
        {STUDIO_FIELDS.slice(1).map(textField)}
      </section>

      <section style={fieldStack}>
        <SectionLabel>Booking hours</SectionLabel>
        {hoursEditor('BOOKING_WORK_HOURS', 'Tattoo appointments')}
        {hoursEditor('BOOKING_CONSULTATION_HOURS', 'Consultations')}
        <SectionLabel>Whole hours, closing by 11 PM. An artist who sets their own working hours replaces the tattoo hours for themselves.</SectionLabel>
      </section>

      <section style={fieldStack}>
        <SectionLabel>Booking days</SectionLabel>
        {dayChips('BOOKING_CLOSED_WEEKDAYS', 'Closed for everyone')}
        {dayChips('BOOKING_REQUEST_ONLY_WEEKDAYS', 'By request only')}
      </section>

      <section style={fieldStack}>
        <SectionLabel>Public website</SectionLabel>
        <ChipGroup label="What your home page shows">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 'var(--space-2)' }}>
            <Chip type="button" selected={values.PUBLIC_SITE_MODE === 'studio'} onClick={() => set('PUBLIC_SITE_MODE', 'studio')}>Your studio site</Chip>
            <Chip type="button" selected={values.PUBLIC_SITE_MODE !== 'studio'} onClick={() => set('PUBLIC_SITE_MODE', 'product')}>The Ryozan page</Chip>
          </div>
        </ChipGroup>
        {SITE_FIELDS.map(textField)}
      </section>

      {error && <p role="alert">{error}</p>}
      {saved && <p role="status">Saved. It is live now.</p>}
      <div><Button type="submit" disabled={saving}>{saving ? 'Saving…' : 'Save settings'}</Button></div>
    </form>
  )
}

/**
 * Day + Time preference picker for the public booking form.
 *
 * Side-by-side layout: calendar left (using existing .cal-* CSS from
 * dashboard.css), time slots right. Reuses the Ryozan calendar system
 * instead of inventing new styles.
 */
import { useEffect, useState, useCallback, useRef } from 'react'
import { calendar as calendarApi, sessionTypes as sessionTypesApi, type BookingKind, type SessionType } from '../../lib/api'
import { formatCustomerPrice } from '../../lib/format'
import { isClosedDay, isRequestOnlyDay, timezoneLabel, todayInStudioTimezone, weekdayLongName, weekdayOf } from '../../lib/studio-settings'
import { useStudioSettings } from '../../hooks/useStudioSettings'
import '../../styles/dashboard.css'
// The .day-pref-* layout/slot styles live in booking.css. Import them here so
// this component renders correctly wherever it is used (public booking flow AND
// the admin proxy form), not only on pages that happen to load booking.css.
import '../../styles/booking.css'

interface DayPreferenceProps {
  artistSlug: string | null
  availabilitySource: string
  selectedSize: string | null
  selectedPlacement?: string | null
  selectedSessionType?: SessionType | null
  selectedDurationMinutes?: number | null
  selectedBufferMinutes?: number | null
  inquiryId: string
  guestEmail: string
  value: DayPreferenceValue | null
  onChange: (value: DayPreferenceValue | null) => void
  bookingKind?: BookingKind
  consultation?: boolean
}

interface BusyBlock { start: string; end: string }

interface SlotOption {
  time: string
  label: string
  source?: string
  confidence?: string
}

interface BlockedSlot {
  date: string
  time: string
  reasons: string[]
}

interface SlotDiagnostics {
  slots: SlotOption[]
  blocked: BlockedSlot[]
}

export interface DayPreferenceValue {
  date: string | null
  time?: string | null
  holdToken?: string | null
  flexible?: boolean
}

function fmt12h(h: number, m: number): string {
  return `${h % 12 || 12}:${String(m).padStart(2, '0')} ${h >= 12 ? 'PM' : 'AM'}`
}

export function DayPreference({
  artistSlug,
  availabilitySource,
  selectedSize,
  selectedPlacement,
  selectedSessionType,
  selectedDurationMinutes,
  selectedBufferMinutes,
  inquiryId,
  guestEmail,
  value,
  onChange,
  bookingKind = 'tattoo_session',
  consultation = false,
}: DayPreferenceProps) {
  const { settings: studioSettings } = useStudioSettings()
  const [month, setMonth] = useState(() => { const d = new Date(); d.setDate(1); return d })
  const [busy, setBusy] = useState<BusyBlock[]>([])
  const [connected, setConnected] = useState(() => !consultation)
  const [sessionType, setSessionType] = useState<SessionType | null>(null)
  const [pickedDay, setPickedDay] = useState<string | null>(null)
  const [serverSlots, setServerSlots] = useState<SlotOption[] | null>(null)
  const [slotsLoading, setSlotsLoading] = useState(false)
  const [slotRefresh, setSlotRefresh] = useState(0)
  const [, setBlockedSlots] = useState<BlockedSlot[]>([])
  const [holdingTime, setHoldingTime] = useState<string | null>(null)
  const [holdError, setHoldError] = useState<string | null>(null)
  const [firstOpenSearching, setFirstOpenSearching] = useState(false)
  const [firstOpenError, setFirstOpenError] = useState<string | null>(null)
  const slotCacheRef = useRef<Map<string, SlotDiagnostics>>(new Map())

  const selTime = value?.time ?? null

  const releaseCurrentHold = useCallback(() => {
    const token = value?.holdToken
    if (token) {
      void calendarApi.releaseArtistSlotHold(token).catch(() => undefined)
    }
  }, [value?.holdToken])

  useEffect(() => {
    if (!artistSlug || availabilitySource === 'venue') {
      setBusy([]); setConnected(false); return
    }
    // Always try the calendar endpoint even when availabilitySource
    // is 'none' — the API field might not have propagated yet but
    // the artist may have Google Calendar connected. The endpoint
    // returns calendar_connected:false gracefully if not linked.
    const s = new Date(month), e = new Date(month)
    e.setMonth(e.getMonth() + 1)
    calendarApi.getArtistAvailability(artistSlug, s.toISOString().slice(0, 10), e.toISOString().slice(0, 10))
      .then(r => { setConnected(r.calendar_connected !== false); setBusy(r.busy as BusyBlock[]) })
      .catch(() => { setBusy([]); setConnected(false) })
  }, [month, artistSlug, availabilitySource])

  useEffect(() => {
    if (consultation) {
      setSessionType(null)
      return
    }
    if (selectedSessionType) {
      setSessionType(selectedSessionType)
      return
    }
    if (!artistSlug || !selectedSize) {
      setSessionType(null)
      return
    }
    sessionTypesApi.lookup(artistSlug, selectedSize, selectedPlacement || undefined).then(setSessionType).catch(() => setSessionType(null))
  }, [artistSlug, selectedSize, selectedPlacement, selectedSessionType, consultation])

  const dur = consultation ? 30 : sessionType?.duration_minutes ?? selectedDurationMinutes ?? 120
  const bufferMinutes = consultation ? 0 : sessionType?.buffer_minutes ?? selectedBufferMinutes ?? undefined
  const effectiveSessionTypeId = consultation ? undefined : selectedSessionType?.id ?? sessionType?.id
  const hasSessionType = !consultation && Boolean(sessionType)
  const today = todayInStudioTimezone(studioSettings)
  const dim = new Date(month.getFullYear(), month.getMonth() + 1, 0).getDate()
  const fdow = new Date(month.getFullYear(), month.getMonth(), 1).getDay()
  const label = month.toLocaleDateString('en-US', { month: 'long', year: 'numeric' })
  const toSlotOption = useCallback((slot: {
    time: string
    label: string
    source?: string
    confidence?: string
  }): SlotOption => ({
    time: slot.time,
    label: slot.time ? fmt12h(Number(slot.time.slice(0, 2)), Number(slot.time.slice(3, 5))) : slot.label,
    source: slot.source,
    confidence: slot.confidence,
  }), [])

  const avail = useCallback((n: number) => {
    const ds = `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, '0')}-${String(n).padStart(2, '0')}`
    if (ds < today) return false
    if (consultation && !connected) return false
    if (availabilitySource === 'none' || !connected) return true
    let hrs = 0
    for (const b of busy) { if (b.start.slice(0, 10) === ds) hrs += (new Date(b.end).getTime() - new Date(b.start).getTime()) / 3600000 }
    return hrs < 8
  }, [month, today, availabilitySource, connected, busy, consultation])

  useEffect(() => {
    if (!artistSlug || !pickedDay) {
      setSlotsLoading(false)
      return
    }

    const cacheKey = `${artistSlug}:${pickedDay}:${selectedSize || ''}:${selectedPlacement || ''}:${effectiveSessionTypeId || ''}:${dur}:${bufferMinutes ?? ''}:${consultation}`
    const cached = slotCacheRef.current.get(cacheKey)
    if (cached) {
      queueMicrotask(() => {
        setServerSlots(cached.slots)
        setBlockedSlots(cached.blocked)
        setSlotsLoading(false)
      })
      return
    }

    let cancelled = false
    setSlotsLoading(true)
    calendarApi.getArtistSlots(artistSlug, {
      start: pickedDay,
      end: pickedDay,
      sessionTypeId: effectiveSessionTypeId,
      size: selectedSize || undefined,
      placement: selectedPlacement || undefined,
      durationMinutes: hasSessionType ? undefined : dur,
      bufferMinutes,
      limit: 80,
      includeBlocked: true,
      blockedLimit: 120,
      consultation,
    })
      .then((response) => {
        if (cancelled) return
        setConnected(response.calendar_connected !== false)
        const nextSlots = response.slots.map(toSlotOption)
        const nextBlocked = (response.blocked_slots || []).filter((slot) => slot.date === pickedDay)
        slotCacheRef.current.set(cacheKey, { slots: nextSlots, blocked: nextBlocked })
        setServerSlots(nextSlots)
        setBlockedSlots(nextBlocked)
      })
      .catch(() => {
        if (!cancelled) {
          setServerSlots([])
          setBlockedSlots([])
        }
      })
      .finally(() => {
        if (!cancelled) setSlotsLoading(false)
      })

    return () => { cancelled = true }
  }, [artistSlug, pickedDay, selectedSize, selectedPlacement, effectiveSessionTypeId, hasSessionType, dur, bufferMinutes, toSlotOption, slotRefresh, consultation])

  // Never fabricate interim times while the live scheduling API is loading.
  // Locally generated slots can conflict with Google Calendar and fail to hold.
  const slots = serverSlots ?? []

  useEffect(() => {
    const nextDate = value?.date ?? null
    if (nextDate !== pickedDay) {
      setPickedDay(nextDate)
      setServerSlots(null)
      setBlockedSlots([])
    }
  }, [pickedDay, value?.date])

  const isSpecialRequestDay = useCallback((ds: string) => (
    !consultation && isRequestOnlyDay(studioSettings, ds, artistSlug)
  ), [artistSlug, consultation, studioSettings])

  const pickDay = (ds: string) => {
    if (isClosedDay(studioSettings, ds)) return
    setFirstOpenError(null)
    if (pickedDay === ds) {
      releaseCurrentHold()
      setPickedDay(null)
      setServerSlots(null)
      setSlotsLoading(false)
      setBlockedSlots([])
      onChange(null)
    } else {
      releaseCurrentHold()
      setPickedDay(ds)
      setServerSlots(null)
      setSlotsLoading(true)
      setBlockedSlots([])
      onChange({ date: ds, time: null })
    }
  }

  const jumpToFirstOpening = async () => {
    if (!artistSlug || firstOpenSearching) return

    setFirstOpenSearching(true)
    setFirstOpenError(null)
    setHoldError(null)

    const start = new Date(`${today}T12:00:00`)
    const maxDays = 180

    try {
      for (let offset = 0; offset < maxDays; offset += 30) {
        const rangeStart = new Date(start)
        rangeStart.setDate(start.getDate() + offset)
        const rangeEnd = new Date(start)
        rangeEnd.setDate(start.getDate() + Math.min(offset + 29, maxDays))

        const response = await calendarApi.getArtistSlots(artistSlug, {
          start: rangeStart.toISOString().slice(0, 10),
          end: rangeEnd.toISOString().slice(0, 10),
          sessionTypeId: effectiveSessionTypeId,
          size: selectedSize || undefined,
          placement: selectedPlacement || undefined,
          durationMinutes: hasSessionType ? undefined : dur,
          bufferMinutes,
          limit: 1,
          consultation,
        })
        setConnected(response.calendar_connected !== false)

        const firstSlot = response.slots[0]
        if (firstSlot?.date) {
          const nextMonth = new Date(`${firstSlot.date}T12:00:00`)
          nextMonth.setDate(1)
          setMonth(nextMonth)
          setPickedDay(firstSlot.date)
          setServerSlots(null)
          setSlotsLoading(true)
          setBlockedSlots([])
          releaseCurrentHold()
          onChange({ date: firstSlot.date, time: null })
          return
        }
      }

      setFirstOpenError('No openings found in the next 180 days.')
    } catch {
      setFirstOpenError('Could not check the next opening. Try again.')
    } finally {
      setFirstOpenSearching(false)
    }
  }

  const pickSlot = async (t: string) => {
    if (!pickedDay || !artistSlug) return
    if (value?.date === pickedDay && value?.time === t) {
      releaseCurrentHold()
      onChange({ date: pickedDay, time: null, holdToken: null })
      return
    }
    setHoldingTime(t)
    setHoldError(null)
    try {
      const hold = await calendarApi.holdArtistSlot({
        artist_slug: artistSlug,
        date: pickedDay,
        time: t,
        session_type_id: effectiveSessionTypeId,
        size: selectedSize || undefined,
        placement: selectedPlacement || undefined,
        duration_minutes: hasSessionType ? undefined : dur,
        buffer_minutes: bufferMinutes,
        inquiry_id: inquiryId,
        guest_email: guestEmail || undefined,
        booking_kind: bookingKind,
        consultation,
      })
      onChange({ date: pickedDay, time: hold.time, holdToken: hold.hold_token })
    } catch {
      setHoldError('That time could not be held. Availability has been refreshed.')
      slotCacheRef.current.clear()
      setServerSlots(null)
      setSlotsLoading(true)
      setBlockedSlots([])
      setSlotRefresh((current) => current + 1)
      onChange({ date: pickedDay, time: null, holdToken: null })
    } finally {
      setHoldingTime(null)
    }
  }

  const dayLabel = pickedDay ? new Date(pickedDay + 'T12:00:00').toLocaleDateString('en-US', { weekday: 'long', month: 'short', day: 'numeric' }) : null
  const selectedSpecialRequestDay = pickedDay ? isSpecialRequestDay(pickedDay) : false
  const pickedWeekdayName = pickedDay ? weekdayLongName(weekdayOf(pickedDay)) : ''

  // Always show the slot panel for native Ryozan booking. The API returns
  // Google-backed slots when connected and shop-hours fallback slots when not.
  const showSlots = availabilitySource !== 'venue'

  return (
    <div className={`day-pref${consultation ? ' day-pref--consultation' : ''}`}>
      {consultation && (
        <div className="day-pref-huddl-header">
          <div>
            <span className="day-pref-huddl-kicker"><i aria-hidden="true" /> LIVE CONSULTATION</span>
            <strong>Choose a verified time</strong>
          </div>
          <span className="day-pref-huddl-meta">{['30 MIN', 'TUE-SAT', timezoneLabel(studioSettings)].filter(Boolean).join(' / ')}</span>
        </div>
      )}
      <div className="day-pref-layout">
        {/* Calendar tile — uses .cal-* classes from dashboard.css */}
        <div className="day-pref-tile day-pref-tile-cal">
          <div className="cal-nav" style={{ justifyContent: 'center', gap: '12px', marginBottom: '12px' }}>
            <button type="button" className="cal-nav-btn" onClick={() => setMonth(d => { const n = new Date(d); n.setMonth(n.getMonth() - 1); return n })}>&#8249;</button>
            <span className="cal-heading" style={{ padding: 0, fontSize: '1rem' }}>{label}</span>
            <button type="button" className="cal-nav-btn" onClick={() => setMonth(d => { const n = new Date(d); n.setMonth(n.getMonth() + 1); return n })}>&#8250;</button>
          </div>

          <div className="cal-weekdays">
            {['S','M','T','W','T','F','S'].map((d, i) => <div key={i} className="cal-weekday">{d}</div>)}
          </div>

          <div className="cal-grid">
            {Array.from({ length: fdow }).map((_, i) => <div key={`b${i}`} className="cal-cell outside" />)}
            {Array.from({ length: dim }).map((_, i) => {
              const n = i + 1
              const ds = `${month.getFullYear()}-${String(month.getMonth() + 1).padStart(2, '0')}-${String(n).padStart(2, '0')}`
              const ok = avail(n)
              const sel = pickedDay === ds
              const past = ds < today
              const isToday = ds === today
              const closedDay = isClosedDay(studioSettings, ds)
              const specialRequestDay = isSpecialRequestDay(ds)
              const unavailable = past || closedDay
              return (
                <button
                  key={n} type="button" disabled={unavailable}
                  className={`cal-cell${sel ? ' selected' : ''}${isToday ? ' today' : ''}${past ? ' outside' : ''}${!ok && !past && !closedDay ? ' no-openings-day' : ''}${closedDay ? ' closed-day' : ''}${specialRequestDay ? ' special-request-day' : ''}`}
                  title={specialRequestDay ? `${weekdayLongName(weekdayOf(ds))} bookings by special request only` : closedDay ? 'Shop is closed this day' : undefined}
                  onClick={() => pickDay(ds)}
                >
                  <span className="cal-day-num">{n}</span>
                </button>
              )
            })}
          </div>
        </div>

        {/* Slots tile - Google-backed when connected, shop-hours fallback otherwise */}
        {showSlots && <div className="day-pref-tile day-pref-tile-slots">
          {!pickedDay ? (
            <div className="day-pref-slots-empty">
              <svg width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2" opacity="0.3">
                <circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>
              </svg>
              <span>{consultation && !connected ? 'Live calendar unavailable' : <>Select a day to see<br/>available times</>}</span>
              {consultation && !connected && <small>Booking stays closed until Google Calendar is verified.</small>}
              {firstOpenError && <small>{firstOpenError}</small>}
            </div>
          ) : (
            <>
              <div className="day-pref-slots-header">
                <span className="day-pref-slots-day">{dayLabel}</span>
                {sessionType && <span className="day-pref-slots-duration">{dur >= 60 ? `${dur / 60}h` : `${dur}m`}{bufferMinutes ? ` + ${bufferMinutes}m buffer` : ''} session</span>}
              </div>
              {selectedSpecialRequestDay ? (
                <div className="day-pref-slots-empty day-pref-slots-special">
                  <span>Special request day</span>
                  <small>{pickedWeekdayName}s are request-only. Send the request and the shop will confirm whether that day can work.</small>
                </div>
              ) : slotsLoading ? (
                <div className="day-pref-slots-empty" aria-live="polite">
                  <span>Checking live availability...</span>
                </div>
              ) : slots.length === 0 ? (
                <div className="day-pref-slots-empty">
                  <span>No openings on this day</span>
                  <small>please select a different date</small>
                  {!connected && <small>{consultation ? 'Live availability is temporarily unavailable. No unverified times are shown.' : 'Live availability is temporarily unavailable. Choose another date or flexible scheduling.'}</small>}
                </div>
              ) : (
                <div className="day-pref-slots-grid">
                  {slots.map(s => (
                    <button key={s.time} type="button"
                      className={`day-pref-slot${selTime === s.time ? ' selected' : ''}`}
                      disabled={holdingTime === s.time}
                      onClick={() => { void pickSlot(s.time) }}
                    >{holdingTime === s.time ? 'Holding...' : s.label}</button>
                  ))}
                </div>
              )}
              {holdError && <div className="day-pref-slots-empty"><span>{holdError}</span></div>}
              {sessionType && (
                <div className="day-pref-session-info">
                  <span>{sessionType.label}</span>
                  {sessionType.deposit_amount > 0 && <span className="day-pref-session-deposit">{formatCustomerPrice(sessionType.deposit_amount)} booking price</span>}
                  {sessionType.hourly_rate != null && <span className="day-pref-session-deposit">{formatCustomerPrice(sessionType.hourly_rate)}/hr</span>}
                </div>
              )}
              {consultation && (
                <div className="day-pref-session-info">
                  <span>Verified 30 minute consultation</span>
                  <span className="day-pref-session-deposit">No deposit</span>
                </div>
              )}
            </>
          )}
        </div>}
      </div>

      {value && !value.flexible && value.date && (
        <div className="day-pref-selected">
          Preferred: {dayLabel}{selTime ? ` at ${fmt12h(parseInt(selTime), parseInt(selTime.slice(3)))}` : ''}
          <button type="button" className="day-pref-clear" onClick={() => { releaseCurrentHold(); setPickedDay(null); setServerSlots(null); setSlotsLoading(false); setBlockedSlots([]); onChange(null) }}>&times;</button>
        </div>
      )}

      <div className="day-pref-actions">
        <button
          type="button"
          className="day-pref-first"
          disabled={!artistSlug || firstOpenSearching}
          onClick={() => { void jumpToFirstOpening() }}
        >{firstOpenSearching ? 'Checking openings...' : 'Jump to first opening'}</button>
        {!consultation && <button type="button" className={`day-pref-flexible${value?.flexible ? ' active' : ''}`}
          onClick={() => { releaseCurrentHold(); setPickedDay(null); setServerSlots(null); setSlotsLoading(false); setBlockedSlots([]); setFirstOpenError(null); onChange(value?.flexible ? null : { date: null, time: null, flexible: true }) }}
        >{value?.flexible ? 'Selected: ' : ''}I'm flexible - artist can suggest</button>}
      </div>
    </div>
  )
}

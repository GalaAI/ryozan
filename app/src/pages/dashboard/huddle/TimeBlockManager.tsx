/**
 * TimeBlockManager - staff control for personal time-blocking.
 *
 * Blocks make an artist's window un-bookable: the backend treats them as a
 * busy source in slot generation (blocked reason "personal_block"), so the
 * public booking flow and the staff proxy form both stop offering that time.
 *
 * Self-contained to limit blast radius in the 2400+ line BookingsManager.
 * Admin/founder gates this (same rank as proxy booking); the backend lets an
 * admin target any artist via artist_id while a plain artist could only block
 * their own time. Artist-self blocking from their own view is a follow-up.
 */
import { useCallback, useEffect, useMemo, useState } from 'react'
import { calendar as calendarApi, type ArtistSummary, type TimeBlock } from '../../../lib/api'
import { useToast } from '../../../components/Toast'

interface TimeBlockManagerProps {
  /** Roster shown in the picker when the viewer may target other artists. */
  artists: ArtistSummary[]
  /**
   * True for admin/founder: show the artist picker and send the chosen
   * artist_id. False for a plain artist: no picker, artist_id is omitted so
   * the backend blocks the current user's own calendar (and only that one).
   */
  canTargetOthers: boolean
  onClose: () => void
}

function todayIso(): string {
  // Local calendar date (not UTC) so "today" matches the artist's day.
  const now = new Date()
  const y = now.getFullYear()
  const m = String(now.getMonth() + 1).padStart(2, '0')
  const d = String(now.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function addDaysIso(iso: string, days: number): string {
  const base = new Date(`${iso}T00:00:00`)
  base.setDate(base.getDate() + days)
  const y = base.getFullYear()
  const m = String(base.getMonth() + 1).padStart(2, '0')
  const d = String(base.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function formatBlockDate(iso: string): string {
  const dt = new Date(`${iso}T00:00:00`)
  return dt.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' })
}

export default function TimeBlockManager({ artists, canTargetOthers, onClose }: TimeBlockManagerProps) {
  const toast = useToast()

  // Only meaningful when canTargetOthers; in self mode artist_id is omitted.
  const [artistId, setArtistId] = useState<string>(() => (canTargetOthers ? artists[0]?.id ?? '' : ''))
  const [blockDate, setBlockDate] = useState<string>(todayIso)
  const [allDay, setAllDay] = useState(false)
  const [startTime, setStartTime] = useState('09:00')
  const [endTime, setEndTime] = useState('12:00')
  const [reason, setReason] = useState('')

  const [blocks, setBlocks] = useState<TimeBlock[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const [removingId, setRemovingId] = useState<string | null>(null)

  const selectedArtist = useMemo(
    () => artists.find((a) => a.id === artistId) ?? null,
    [artists, artistId],
  )

  const loadBlocks = useCallback(async () => {
    // Target mode needs a chosen artist; self mode omits artist_id and the
    // backend defaults to the current user's own calendar.
    if (canTargetOthers && !artistId) return
    setLoading(true)
    try {
      const start = todayIso()
      const end = addDaysIso(start, 90)
      const rows = await calendarApi.timeBlocks.list({
        start,
        end,
        artist_id: canTargetOthers ? artistId : undefined,
      })
      setBlocks(rows)
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to load time blocks')
    } finally {
      setLoading(false)
    }
  }, [canTargetOthers, artistId, toast])

  useEffect(() => {
    void loadBlocks()
  }, [loadBlocks])

  const handleCreate = useCallback(async () => {
    if (canTargetOthers && !artistId) {
      toast.error('Pick an artist to block time for')
      return
    }
    if (!blockDate) {
      toast.error('Pick a date')
      return
    }
    if (!allDay && (!startTime || !endTime)) {
      toast.error('Set a start and end time, or choose all-day')
      return
    }
    if (!allDay && startTime >= endTime) {
      toast.error('End time must be after start time')
      return
    }
    setSubmitting(true)
    try {
      await calendarApi.timeBlocks.create({
        artist_id: canTargetOthers ? artistId : undefined,
        block_date: blockDate,
        all_day: allDay,
        start_time: allDay ? undefined : startTime,
        end_time: allDay ? undefined : endTime,
        reason: reason.trim() || undefined,
      })
      toast.success('Time blocked.')
      setReason('')
      await loadBlocks()
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to block time')
    } finally {
      setSubmitting(false)
    }
  }, [canTargetOthers, artistId, blockDate, allDay, startTime, endTime, reason, loadBlocks, toast])

  const handleRemove = useCallback(async (id: string) => {
    setRemovingId(id)
    try {
      await calendarApi.timeBlocks.remove(id)
      setBlocks((prev) => prev.filter((b) => b.id !== id))
      toast.success('Block removed.')
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to remove block')
    } finally {
      setRemovingId(null)
    }
  }, [toast])

  return (
    <div className="bkm-proxy-form">
      <div className="bkm-proxy-head">
        <div>
          <h3>Block time</h3>
          <p>Reserve personal time so it cannot be booked. Blocks apply to the public booking flow and staff bookings alike.</p>
        </div>
        <button type="button" onClick={onClose}>Close</button>
      </div>

      {canTargetOthers && artists.length === 0 ? (
        <p className="bkm-proxy-empty">
          No Ryozan-scheduling artists available; venue artists manage time in venue.ink.
        </p>
      ) : (
        <div className="bkm-block-body">
          <section className="bkm-proxy-card">
            <div className="bkm-proxy-card-head">
              <span>New block</span>
              <small>
                {canTargetOthers
                  ? (selectedArtist?.availability_source === 'google_calendar' ? 'Calendar linked' : 'Shop hours')
                  : 'Your calendar'}
              </small>
            </div>

            {canTargetOthers && (
              <div className="bkm-artist-picker">
                {artists.map((artist) => (
                  <button
                    type="button"
                    key={artist.id}
                    className={artistId === artist.id ? 'is-active' : ''}
                    onClick={() => setArtistId(artist.id)}
                  >
                    <strong>{artist.name}</strong>
                  </button>
                ))}
              </div>
            )}

            <div className="bkm-block-fields">
              <label className="bkm-proxy-field"><span>Date</span>
                <input
                  type="date"
                  value={blockDate}
                  min={todayIso()}
                  onChange={(e) => setBlockDate(e.target.value)}
                />
              </label>

              <label className="bkm-block-allday">
                <input
                  type="checkbox"
                  checked={allDay}
                  onChange={(e) => setAllDay(e.target.checked)}
                />
                <span>All day</span>
              </label>

              {!allDay && (
                <>
                  <label className="bkm-proxy-field"><span>Start</span>
                    <input type="time" value={startTime} onChange={(e) => setStartTime(e.target.value)} />
                  </label>
                  <label className="bkm-proxy-field"><span>End</span>
                    <input type="time" value={endTime} onChange={(e) => setEndTime(e.target.value)} />
                  </label>
                </>
              )}

              <label className="bkm-proxy-field bkm-proxy-field-full"><span>Reason (optional)</span>
                <input
                  value={reason}
                  maxLength={255}
                  placeholder="e.g. Lunch, appointment, personal"
                  onChange={(e) => setReason(e.target.value)}
                />
              </label>
            </div>

            <button
              type="button"
              className="bkm-add-booking-btn"
              disabled={submitting}
              onClick={handleCreate}
            >
              {submitting ? 'Blocking...' : 'Block this time'}
            </button>
          </section>

          <section className="bkm-proxy-card">
            <div className="bkm-proxy-card-head">
              <span>Upcoming blocks</span>
              <small>{loading ? 'Loading...' : `${blocks.length} in next 90 days`}</small>
            </div>

            {!loading && blocks.length === 0 && (
              <p className="bkm-block-empty">No time blocked for this artist.</p>
            )}

            <ul className="bkm-block-list">
              {blocks.map((block) => (
                <li key={block.id} className="bkm-block-item">
                  <div className="bkm-block-item-main">
                    <strong>{formatBlockDate(block.block_date)}</strong>
                    <span>
                      {block.all_day
                        ? 'All day'
                        : `${block.start_time ?? '?'} - ${block.end_time ?? '?'}`}
                    </span>
                    {block.reason && <em>{block.reason}</em>}
                  </div>
                  <button
                    type="button"
                    className="bkm-block-remove"
                    disabled={removingId === block.id}
                    onClick={() => handleRemove(block.id)}
                  >
                    {removingId === block.id ? '...' : 'Remove'}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        </div>
      )}
    </div>
  )
}

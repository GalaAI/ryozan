/**
 * Portfolio: Dashboard page for artists to manage their full public portfolio.
 *
 * Account and identity settings for the signed-in staff account.
 * App-specific configuration belongs inside each product surface.
 *
 * Fetches from GET /api/v1/artists/{slug}, saves via PATCH /api/v1/artists/me.
 * Gallery, flash, and shop workflows now live in Hustle.
 */

import { useEffect, useState, useRef, useCallback } from 'react'
import ReactDOM from 'react-dom'
import { useAuthStore } from '../../stores/auth'
import { auth as authApi, artists as artistsApi, flash as flashApi, users as usersApi, stripeConnect, emailConnect, googleAuth, pinterestAuth, sessionTypes as sessionTypesApi, bookingAddOns as bookingAddOnsApi, type ArtistDetail, type FlashDesign, type FlashStatus, type StripeConnectStatus, type EmailStatus, type SessionType, type BookingAddOn } from '../../lib/api'
import { useToast } from '../../components/Toast'

// ── Constants ──────────────────────────────────────

const TABS = [
  { key: 'gallery', label: 'Portfolio' },
  { key: 'settings', label: 'Account' },
] as const

type TabKey = (typeof TABS)[number]['key'] | 'profile' | 'contact' | 'integrations' | 'account' | 'gallery' | 'flash' | 'shop'

const SPECIALTY_OPTIONS = [
  'Neo-Traditional', 'Illustrative', 'Japanese', 'Black & Grey',
  'Realism', 'Traditional', 'Bold Line', 'Fine Line',
  'Geometric', 'Lettering', 'Tribal',
]

type SessionTypeDraft = {
  label: string
  size: string
  placements: string
  duration_minutes: number
  buffer_minutes: number | null
  deposit_amount: number
  hourly_rate: string
  price_min: string
  price_max: string
  sort_order: number
}

type BookingAddOnDraft = {
  label: string
  description: string
  price_amount: string
  duration_minutes: string
  sort_order: number
}

const DEFAULT_BOOKING_ADDONS: BookingAddOnDraft[] = [
  { label: 'Design Consult', description: 'Extra concept planning before the tattoo appointment.', price_amount: '', duration_minutes: '', sort_order: 10 },
  { label: 'Numbing Consult', description: 'Talk through numbing options and timing before booking.', price_amount: '', duration_minutes: '', sort_order: 20 },
  { label: 'Priority Review', description: 'Flag the inquiry for faster staff review when capacity allows.', price_amount: '', duration_minutes: '', sort_order: 30 },
  { label: 'Extended Session', description: 'Tell staff this may need more time than the base appointment.', price_amount: '', duration_minutes: '60', sort_order: 40 },
]

const EMPTY_BOOKING_ADDON_DRAFT: BookingAddOnDraft = {
  label: '',
  description: '',
  price_amount: '',
  duration_minutes: '',
  sort_order: 100,
}

const DEFAULT_SESSION_TYPES: SessionTypeDraft[] = [
  { label: '45 Minute Mini Tattoo', size: 'small', placements: '', duration_minutes: 45, buffer_minutes: 0, deposit_amount: 50, hourly_rate: '150', price_min: '100', price_max: '150', sort_order: 10 },
  { label: '3 Hour Flash, Palm Sized', size: 'small', placements: '', duration_minutes: 180, buffer_minutes: 0, deposit_amount: 100, hourly_rate: '175', price_min: '400', price_max: '650', sort_order: 20 },
  { label: 'Half Day Session', size: 'large', placements: '', duration_minutes: 240, buffer_minutes: 60, deposit_amount: 200, hourly_rate: '175', price_min: '700', price_max: '1000', sort_order: 30 },
  { label: 'Full Day Session', size: 'xlarge', placements: '', duration_minutes: 480, buffer_minutes: 0, deposit_amount: 300, hourly_rate: '175', price_min: '1200', price_max: '1600', sort_order: 40 },
]

const EMPTY_SESSION_TYPE_DRAFT: SessionTypeDraft = {
  label: '',
  size: 'small',
  placements: '',
  duration_minutes: 120,
  buffer_minutes: 0,
  deposit_amount: 100,
  hourly_rate: '',
  price_min: '',
  price_max: '',
  sort_order: 100,
}

function parsePlacementList(value: string): string[] | null {
  const placements = value
    .split(',')
    .map((part) => part.trim().toLowerCase())
    .filter(Boolean)
  return placements.length > 0 ? placements : null
}

function parseOptionalMoney(value: string): number | null {
  if (!value.trim()) return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function sessionTypeToDraft(type: SessionType): SessionTypeDraft {
  return {
    label: type.label,
    size: type.size,
    placements: (type.placements || []).join(', '),
    duration_minutes: type.duration_minutes,
    buffer_minutes: type.buffer_minutes ?? null,
    deposit_amount: Number(type.deposit_amount || 0),
    hourly_rate: type.hourly_rate == null ? '' : String(type.hourly_rate),
    price_min: type.price_min == null ? '' : String(type.price_min),
    price_max: type.price_max == null ? '' : String(type.price_max),
    sort_order: type.sort_order,
  }
}

function bookingAddOnToDraft(addOn: BookingAddOn): BookingAddOnDraft {
  return {
    label: addOn.label,
    description: addOn.description ?? '',
    price_amount: addOn.price_amount == null ? '' : String(addOn.price_amount),
    duration_minutes: addOn.duration_minutes == null ? '' : String(addOn.duration_minutes),
    sort_order: addOn.sort_order,
  }
}

// ── Component ──────────────────────────────────────

export default function ArtistProfileEditor() {
  const { user, fetchMe } = useAuthStore()
  const toast = useToast()

  const [profile, setProfile] = useState<ArtistDetail | null>(null)
  const [isLoading, setIsLoading] = useState(true)
  const [isSaving, setIsSaving] = useState(false)
  const [message, setMessage] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const [activeTab, setActiveTab] = useState<TabKey>('gallery')
  const appointmentTypesRef = useRef<HTMLDivElement>(null)
  const accountSettingsRef = useRef<HTMLDetailsElement>(null)
  const appSettingsRef = useRef<HTMLDetailsElement>(null)
  const integrationsSettingsRef = useRef<HTMLDetailsElement>(null)
  const urlSearch = new URLSearchParams(window.location.search)
  const requestedTab = urlSearch.get('tab')
  const requestedSection = requestedTab === 'integrations' ? 'integrations' : urlSearch.get('section')
  const googleCallback = urlSearch.get('google')
  const pinterestCallback = urlSearch.get('pinterest')
  const appointmentTypesRequested = requestedSection === 'appointment-types'

  useEffect(() => {
    // Flash and shop live in Hustle; portfolio (gallery) is managed here.
    if (user?.role === 'artist' && (
      requestedTab === 'integrations'
      || requestedSection === 'integrations'
      || googleCallback !== null
      || pinterestCallback !== null
    )) {
      const callback = googleCallback
        ? `&google=${encodeURIComponent(googleCallback)}`
        : pinterestCallback
          ? `&pinterest=${encodeURIComponent(pinterestCallback)}`
          : ''
      window.location.replace(`/dashboard?portal=hub&workflow=connections${callback}`)
      return
    }
    if (requestedTab && ['flash', 'shop'].includes(requestedTab)) {
      window.location.replace('/dashboard/hustle')
      return
    }
    if (requestedTab === 'booking') {
      window.location.replace('/dashboard/huddle?charm=bookings')
      return
    }
    if (requestedTab === 'gallery') {
      setActiveTab('gallery')
    } else if (requestedTab && ['settings', 'profile', 'contact', 'integrations', 'account'].includes(requestedTab)) {
      setActiveTab('settings')
    }
  }, [googleCallback, pinterestCallback, requestedSection, requestedTab, user?.role])

  useEffect(() => {
    if (activeTab !== 'settings' || !requestedSection) return

    const target = requestedSection === 'integrations'
      ? integrationsSettingsRef.current
      : requestedSection === 'app-settings' || requestedSection === 'appointment-types'
        ? appSettingsRef.current
        : requestedSection === 'account'
          ? accountSettingsRef.current
          : null

    if (!target) return

    const frame = window.requestAnimationFrame(() => {
      target.scrollIntoView({ behavior: 'smooth', block: 'start' })
      if (appointmentTypesRequested) appointmentTypesRef.current?.focus({ preventScroll: true })
    })

    return () => window.cancelAnimationFrame(frame)
  }, [activeTab, appointmentTypesRequested, requestedSection])

  useEffect(() => {
    if (new URLSearchParams(window.location.search).get('pinterest') === 'connected') {
      toast.success('Pinterest account connected')
      setPinterestConnected(true)
      const url = new URL(window.location.href)
      url.searchParams.delete('pinterest')
      window.history.replaceState({}, '', url.toString())
      pinterestAuth.getStatus()
        .then((res) => {
          setPinterestConnected(res.connected)
          setPinterestUsername(res.username || null)
        })
        .catch(() => { /* silent */ })
    }
  }, [])

  // Account tab state
  const [acctName, setAcctName] = useState(user?.name || '')
  const [acctMarketing, setAcctMarketing] = useState(user?.marketing_opt_in || false)
  const [acctSaving, setAcctSaving] = useState(false)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [passwordSaving, setPasswordSaving] = useState(false)

  // Modz tab state
  const [stripeStatus, setStripeStatus] = useState<StripeConnectStatus | null>(null)
  const [emailStatus, setEmailStatus] = useState<EmailStatus | null>(null)
  const [integrationsLoading, setIntegrationsLoading] = useState(false)
  const [googleConnected, setGoogleConnected] = useState(false)
  const [googleManaged, setGoogleManaged] = useState(false)
  const [pinterestConnected, setPinterestConnected] = useState(false)
  const [pinterestUsername, setPinterestUsername] = useState<string | null>(null)

  // Form fields: Profile & Bio
  const [title, setTitle] = useState('')
  const [specialty, setSpecialty] = useState<string[]>([])
  const [bio, setBio] = useState('')

  // Form fields: Contact & Socials
  const [phone, setPhone] = useState('')
  const [instagramUrl, setInstagramUrl] = useState('')
  const [facebookUrl, setFacebookUrl] = useState('')
  const [tiktokUrl, setTiktokUrl] = useState('')
  const [websiteUrl, setWebsiteUrl] = useState('')

  // Form fields: Booking
  const [bookingMode, setBookingMode] = useState('mailto')
  const [bookingSlotInterval, setBookingSlotInterval] = useState(30)
  const [bookingBuffer, setBookingBuffer] = useState(30)
  const [bookingMinNotice, setBookingMinNotice] = useState(24)
  const [bookingMaxAdvance, setBookingMaxAdvance] = useState(180)
  const [bookingAllowSameDay, setBookingAllowSameDay] = useState(false)
  const [flashDepositAmount, setFlashDepositAmount] = useState<number | ''>('')
  const [allowPrepayFull, setAllowPrepayFull] = useState(false)
  const [workingHours, setWorkingHours] = useState<Record<string, [number, number] | null>>({
    '0': null,
    '1': null,
    '2': [11, 18],
    '3': [11, 18],
    '4': [11, 20],
    '5': [11, 20],
    '6': null,
  })
  const [sessionTypeRows, setSessionTypeRows] = useState<SessionType[]>([])
  const [sessionTypesLoading, setSessionTypesLoading] = useState(false)
  const [sessionTypesSaving, setSessionTypesSaving] = useState(false)
  const [sessionTypeDraft, setSessionTypeDraft] = useState<SessionTypeDraft>(EMPTY_SESSION_TYPE_DRAFT)
  const [editingSessionTypes, setEditingSessionTypes] = useState<Record<string, SessionTypeDraft>>({})
  const [bookingAddOnRows, setBookingAddOnRows] = useState<BookingAddOn[]>([])
  const [bookingAddOnsLoading, setBookingAddOnsLoading] = useState(false)
  const [bookingAddOnsSaving, setBookingAddOnsSaving] = useState(false)
  const [bookingAddOnDraft, setBookingAddOnDraft] = useState<BookingAddOnDraft>(EMPTY_BOOKING_ADDON_DRAFT)
  const [editingBookingAddOns, setEditingBookingAddOns] = useState<Record<string, BookingAddOnDraft>>({})

  // Avatar
  const [avatarUrl, setAvatarUrl] = useState<string | null>(null)
  const [isUploadingAvatar, setIsUploadingAvatar] = useState(false)
  const avatarInputRef = useRef<HTMLInputElement>(null)

  // Gallery
  const [galleryImages, setGalleryImages] = useState<string[]>([])
  const [isUploading, setIsUploading] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Flash
  const [flashDesigns, setFlashDesigns] = useState<FlashDesign[]>([])
  const [flashLoading, setFlashLoading] = useState(false)
  const [flashEditId, setFlashEditId] = useState<string | null>(null)
  const [flashEditMeta, setFlashEditMeta] = useState<{
    title: string; description: string; price: string; size_estimate: string; style: string
  }>({ title: '', description: '', price: '', size_estimate: '', style: '' })
  const [showFlashModal, setShowFlashModal] = useState(false)

  // ── Load profile ──────────────────────────────

  useEffect(() => {
    if (!user) return

    artistsApi.list()
      .then((artists) => {
        const me = artists.find((a) => a.id === user.id)
        if (me) return artistsApi.get(me.slug)
        return null
      })
      .then((data) => {
        if (data) {
          setProfile(data)
          setAvatarUrl(data.avatar_url ?? null)
          setTitle(data.title ?? '')
          setSpecialty((data.specialty ?? []).filter((s) => SPECIALTY_OPTIONS.includes(s)))
          setBio(data.bio ?? '')
          setBookingMode(data.booking_mode ?? 'mailto')
          setBookingSlotInterval(data.booking_slot_interval_minutes ?? 30)
          setBookingBuffer(data.booking_buffer_minutes ?? 30)
          setBookingMinNotice(data.booking_min_notice_hours ?? 24)
          setBookingMaxAdvance(data.booking_max_advance_days ?? 180)
          setBookingAllowSameDay(data.booking_allow_same_day ?? false)
          setFlashDepositAmount(data.flash_deposit_amount ?? '')
          setAllowPrepayFull(data.allow_prepay_full ?? false)
          if (data.working_hours) {
            setWorkingHours((prev) => ({ ...prev, ...data.working_hours }))
          }
          setInstagramUrl(data.instagram_url ?? '')
          setPhone(data.phone ?? '')
          setFacebookUrl(data.facebook_url ?? '')
          setTiktokUrl(data.tiktok_url ?? '')
          setWebsiteUrl(data.website_url ?? '')
          setGalleryImages(data.portfolio_images ?? [])
        }
      })
      .catch(() => {
        setMessage({ type: 'err', text: 'Could not load artist profile.' })
      })
      .finally(() => setIsLoading(false))
  }, [user])

  // ── Helpers ───────────────────────────────────

  const toggleSpecialty = (s: string) => {
    setSpecialty((prev) =>
      prev.includes(s) ? prev.filter((x) => x !== s) : [...prev, s]
    )
  }

  const showMsg = (type: 'ok' | 'err', text: string) => {
    setMessage({ type, text })
    if (type === 'ok') setTimeout(() => setMessage(null), 3000)
  }

  const loadSessionTypes = useCallback(async () => {
    setSessionTypesLoading(true)
    try {
      const types = await sessionTypesApi.mine()
      setSessionTypeRows(types)
      setEditingSessionTypes(
        Object.fromEntries(types.map((type) => [type.id, sessionTypeToDraft(type)]))
      )
    } catch {
      setMessage({ type: 'err', text: 'Could not load appointment types.' })
    } finally {
      setSessionTypesLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) void loadSessionTypes()
  }, [user, loadSessionTypes])

  const sessionTypePayload = (draft: SessionTypeDraft) => ({
    label: draft.label.trim(),
    size: draft.size.trim().toLowerCase(),
    placements: parsePlacementList(draft.placements),
    duration_minutes: Number(draft.duration_minutes),
    buffer_minutes: draft.buffer_minutes == null ? null : Number(draft.buffer_minutes),
    deposit_amount: Number(draft.deposit_amount),
    hourly_rate: parseOptionalMoney(draft.hourly_rate),
    price_min: parseOptionalMoney(draft.price_min),
    price_max: parseOptionalMoney(draft.price_max),
    sort_order: Number(draft.sort_order),
  })

  const handleCreateSessionType = async () => {
    if (!sessionTypeDraft.label.trim()) {
      showMsg('err', 'Appointment type needs a label.')
      return
    }
    setSessionTypesSaving(true)
    try {
      await sessionTypesApi.create(sessionTypePayload(sessionTypeDraft))
      setSessionTypeDraft({ ...EMPTY_SESSION_TYPE_DRAFT, sort_order: sessionTypeRows.length * 10 + 100 })
      await loadSessionTypes()
      showMsg('ok', 'Appointment type created.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not create appointment type.')
    } finally {
      setSessionTypesSaving(false)
    }
  }

  const handleUpdateSessionType = async (typeId: string) => {
    const draft = editingSessionTypes[typeId]
    if (!draft?.label.trim()) {
      showMsg('err', 'Appointment type needs a label.')
      return
    }
    setSessionTypesSaving(true)
    try {
      await sessionTypesApi.update(typeId, sessionTypePayload(draft))
      await loadSessionTypes()
      showMsg('ok', 'Appointment type updated.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not update appointment type.')
    } finally {
      setSessionTypesSaving(false)
    }
  }

  const handleToggleSessionType = async (type: SessionType) => {
    setSessionTypesSaving(true)
    try {
      await sessionTypesApi.update(type.id, { is_active: !type.is_active })
      await loadSessionTypes()
      showMsg('ok', type.is_active ? 'Appointment type hidden.' : 'Appointment type active.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not update appointment type.')
    } finally {
      setSessionTypesSaving(false)
    }
  }

  const handleDeleteSessionType = async (typeId: string) => {
    setSessionTypesSaving(true)
    try {
      await sessionTypesApi.remove(typeId)
      await loadSessionTypes()
      showMsg('ok', 'Appointment type deleted.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not delete appointment type.')
    } finally {
      setSessionTypesSaving(false)
    }
  }

  const handleInstallDefaultSessionTypes = async () => {
    const existingLabels = new Set(sessionTypeRows.map((type) => type.label.toLowerCase()))
    const missing = DEFAULT_SESSION_TYPES.filter((type) => !existingLabels.has(type.label.toLowerCase()))
    if (missing.length === 0) {
      showMsg('ok', 'Default appointment types are already installed.')
      return
    }
    setSessionTypesSaving(true)
    try {
      for (const draft of missing) {
        await sessionTypesApi.create(sessionTypePayload(draft))
      }
      await loadSessionTypes()
      showMsg('ok', `Installed ${missing.length} appointment type${missing.length === 1 ? '' : 's'}.`)
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not install default appointment types.')
    } finally {
      setSessionTypesSaving(false)
    }
  }

  const loadBookingAddOns = useCallback(async () => {
    setBookingAddOnsLoading(true)
    try {
      const rows = await bookingAddOnsApi.mine()
      setBookingAddOnRows(rows)
      setEditingBookingAddOns(
        Object.fromEntries(rows.map((addOn) => [addOn.id, bookingAddOnToDraft(addOn)]))
      )
    } catch {
      setMessage({ type: 'err', text: 'Could not load booking add-ons.' })
    } finally {
      setBookingAddOnsLoading(false)
    }
  }, [])

  useEffect(() => {
    if (user) void loadBookingAddOns()
  }, [user, loadBookingAddOns])

  const bookingAddOnPayload = (draft: BookingAddOnDraft) => ({
    label: draft.label.trim(),
    description: draft.description.trim() || null,
    price_amount: parseOptionalMoney(draft.price_amount),
    duration_minutes: draft.duration_minutes.trim() ? Number(draft.duration_minutes) : null,
    sort_order: Number(draft.sort_order),
  })

  const handleCreateBookingAddOn = async () => {
    if (!bookingAddOnDraft.label.trim()) {
      showMsg('err', 'Add-on needs a label.')
      return
    }
    setBookingAddOnsSaving(true)
    try {
      await bookingAddOnsApi.create(bookingAddOnPayload(bookingAddOnDraft))
      setBookingAddOnDraft({ ...EMPTY_BOOKING_ADDON_DRAFT, sort_order: bookingAddOnRows.length * 10 + 100 })
      await loadBookingAddOns()
      showMsg('ok', 'Booking add-on created.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not create booking add-on.')
    } finally {
      setBookingAddOnsSaving(false)
    }
  }

  const handleUpdateBookingAddOn = async (addOnId: string) => {
    const draft = editingBookingAddOns[addOnId]
    if (!draft?.label.trim()) {
      showMsg('err', 'Add-on needs a label.')
      return
    }
    setBookingAddOnsSaving(true)
    try {
      await bookingAddOnsApi.update(addOnId, bookingAddOnPayload(draft))
      await loadBookingAddOns()
      showMsg('ok', 'Booking add-on updated.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not update booking add-on.')
    } finally {
      setBookingAddOnsSaving(false)
    }
  }

  const handleToggleBookingAddOn = async (addOn: BookingAddOn) => {
    setBookingAddOnsSaving(true)
    try {
      await bookingAddOnsApi.update(addOn.id, { is_active: !addOn.is_active })
      await loadBookingAddOns()
      showMsg('ok', addOn.is_active ? 'Booking add-on hidden.' : 'Booking add-on active.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not update booking add-on.')
    } finally {
      setBookingAddOnsSaving(false)
    }
  }

  const handleDeleteBookingAddOn = async (addOnId: string) => {
    setBookingAddOnsSaving(true)
    try {
      await bookingAddOnsApi.remove(addOnId)
      await loadBookingAddOns()
      showMsg('ok', 'Booking add-on deleted.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not delete booking add-on.')
    } finally {
      setBookingAddOnsSaving(false)
    }
  }

  const handleInstallDefaultBookingAddOns = async () => {
    const existingLabels = new Set(bookingAddOnRows.map((addOn) => addOn.label.toLowerCase()))
    const missing = DEFAULT_BOOKING_ADDONS.filter((addOn) => !existingLabels.has(addOn.label.toLowerCase()))
    if (missing.length === 0) {
      showMsg('ok', 'Default booking add-ons are already installed.')
      return
    }
    setBookingAddOnsSaving(true)
    try {
      for (const draft of missing) {
        await bookingAddOnsApi.create(bookingAddOnPayload(draft))
      }
      await loadBookingAddOns()
      showMsg('ok', `Installed ${missing.length} booking add-on${missing.length === 1 ? '' : 's'}.`)
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Could not install default booking add-ons.')
    } finally {
      setBookingAddOnsSaving(false)
    }
  }

  // ── Save handler (works for Profile, Contact, Booking tabs) ──

  const handleSave = async () => {
    setIsSaving(true)
    setMessage(null)
    try {
      const updated = await artistsApi.updateProfile({
        title: title || undefined,
        specialty,
        bio: bio || undefined,
        booking_mode: bookingMode,
        booking_slot_interval_minutes: bookingSlotInterval,
        booking_buffer_minutes: bookingBuffer,
        booking_min_notice_hours: bookingMinNotice,
        booking_max_advance_days: bookingMaxAdvance,
        booking_allow_same_day: bookingAllowSameDay,
        flash_deposit_amount: flashDepositAmount === '' ? null : Number(flashDepositAmount),
        allow_prepay_full: allowPrepayFull,
        working_hours: workingHours,
        instagram_url: instagramUrl || undefined,
        phone: phone || undefined,
        facebook_url: facebookUrl || undefined,
        tiktok_url: tiktokUrl || undefined,
        website_url: websiteUrl || undefined,
      })
      setProfile(updated)
      showMsg('ok', 'Profile saved.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Save failed.')
    } finally {
      setIsSaving(false)
    }
  }

  // ── Gallery handlers ──────────────────────────

  const handleGalleryUpload = async (files: FileList) => {
    setIsUploading(true)
    setMessage(null)
    let latest: ArtistDetail | null = null
    let uploadedCount = 0
    try {
      for (const file of Array.from(files)) {
        latest = await artistsApi.uploadGalleryImage(file)
        uploadedCount += 1
        setProfile(latest)
        setGalleryImages(latest.portfolio_images ?? [])
      }
      if (latest) {
        const projected = await artistsApi.get(latest.slug)
        setProfile(projected)
        setGalleryImages(projected.portfolio_images ?? [])
        showMsg('ok', `${files.length} image${files.length > 1 ? 's' : ''} uploaded and verified live.`)
      }
    } catch (err: unknown) {
      if (latest) {
        const fallbackProfile = latest
        const projected = await artistsApi.get(fallbackProfile.slug).catch(() => fallbackProfile)
        setProfile(projected)
        setGalleryImages(projected.portfolio_images ?? [])
      }
      const reason = err instanceof Error ? err.message : 'Upload failed.'
      showMsg('err', uploadedCount ? `${uploadedCount} of ${files.length} uploaded. ${reason}` : reason)
    } finally {
      setIsUploading(false)
    }
  }

  const handleGalleryRemove = async (url: string) => {
    try {
      const updated = await artistsApi.removeGalleryImage(url)
      const projected = await artistsApi.get(updated.slug)
      setProfile(projected)
      setGalleryImages(projected.portfolio_images ?? [])
      showMsg('ok', 'Image removed and verified live.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Remove failed.')
    }
  }

  // ── Flash handlers ─────────────────────────────

  const loadFlash = async () => {
    setFlashLoading(true)
    try {
      const designs = await flashApi.listOwn()
      setFlashDesigns(designs)
    } catch { /* silent — endpoint may not exist yet */ }
    finally { setFlashLoading(false) }
  }

  const handleFlashUpdate = async (id: string) => {
    try {
      const updated = await flashApi.update(id, {
        title: flashEditMeta.title || undefined,
        description: flashEditMeta.description || undefined,
        price: flashEditMeta.price ? parseFloat(flashEditMeta.price) : undefined,
        size_estimate: flashEditMeta.size_estimate || undefined,
        style: flashEditMeta.style || undefined,
      })
      setFlashDesigns(prev => prev.map(d => d.id === id ? updated : d))
      setFlashEditId(null)
      showMsg('ok', 'Design updated.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Update failed.')
    }
  }

  const handleFlashStatusToggle = async (design: FlashDesign) => {
    const next: FlashStatus = design.status === 'available' ? 'sold' : 'available'
    try {
      const updated = await flashApi.update(design.id, { status: next })
      setFlashDesigns(prev => prev.map(d => d.id === design.id ? updated : d))
    } catch { /* silent */ }
  }

  const handleFlashRemove = async (id: string) => {
    try {
      await flashApi.remove(id)
      setFlashDesigns(prev => prev.filter(d => d.id !== id))
      showMsg('ok', 'Design removed.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Remove failed.')
    }
  }

  // ── Avatar handler ──────────────────────────────

  const handleAvatarUpload = async (file: File) => {
    setIsUploadingAvatar(true)
    setMessage(null)
    try {
      const updated = await artistsApi.uploadAvatar(file)
      setProfile(updated)
      setAvatarUrl(updated.avatar_url ?? null)
      // Update auth store so topbar reflects the change immediately
      const authUser = useAuthStore.getState().user
      if (authUser) {
        useAuthStore.setState({ user: { ...authUser, avatar_url: updated.avatar_url ?? null } })
      }
      showMsg('ok', 'Profile picture updated.')
    } catch (err: unknown) {
      showMsg('err', err instanceof Error ? err.message : 'Avatar upload failed.')
    } finally {
      setIsUploadingAvatar(false)
    }
  }

  const handleAccountSave = async () => {
    setAcctSaving(true)
    try {
      await usersApi.updateProfile({ name: acctName, marketing_opt_in: acctMarketing })
      await fetchMe()
      toast.success('Account updated')
    } catch {
      toast.error('Failed to update account')
    } finally {
      setAcctSaving(false)
    }
  }

  const handlePasswordChange = async () => {
    if (newPassword.length < 8) {
      toast.error('New password must be at least 8 characters')
      return
    }
    if (newPassword !== confirmPassword) {
      toast.error('Passwords do not match')
      return
    }

    setPasswordSaving(true)
    try {
      await authApi.changePassword(currentPassword, newPassword)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      await fetchMe()
      toast.success('Password updated')
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : 'Failed to update password')
    } finally {
      setPasswordSaving(false)
    }
  }

  // ── Loading state ─────────────────────────────

  if (isLoading) {
    return (
      <div className="dashboard-panel home-panel nexus artist-profile-page">
        <p className="text-muted" style={{ padding: '2rem' }}>Loading...</p>
      </div>
    )
  }

  // ── Save button (reused across tabs) ──────────

  const SaveButton = () => (
    <div style={{ display: 'flex', gap: '1rem', marginTop: '1rem' }}>
      <button
        onClick={handleSave}
        disabled={isSaving}
        className="action-btn"
        style={{
          borderColor: 'var(--gold)',
          color: 'var(--gold)',
          opacity: isSaving ? 0.6 : 1,
        }}
      >
        {isSaving ? 'Saving...' : 'Save'}
      </button>
    </div>
  )

  // ── Render ────────────────────────────────────

  return (
    <div className="dashboard-panel home-panel nexus artist-profile-page">
      <header className="doc-hub-header">
        <div className="doc-tabs">
          {TABS.map((tab) => (
            <button
              key={tab.key}
              className={`doc-tab${activeTab === tab.key ? ' active' : ''}`}
              onClick={() => setActiveTab(tab.key)}
            >
              <span className="doc-tab-label">{tab.label}</span>
            </button>
          ))}
        </div>
      </header>

      {/* Tab content */}
      <div className="hub-overview artist-settings-scroll">
        {/* Status message */}
        {message && (
          <div
            style={{
              padding: '0.75rem 1rem',
              borderRadius: '8px',
              marginBottom: '1.5rem',
              fontSize: '0.85rem',
              background: message.type === 'ok'
                ? 'rgba(76, 175, 80, 0.15)'
                : 'rgba(217, 83, 79, 0.15)',
              color: message.type === 'ok' ? 'var(--ok)' : 'var(--danger)',
              border: `1px solid ${message.type === 'ok' ? 'rgba(76, 175, 80, 0.3)' : 'rgba(217, 83, 79, 0.3)'}`,
            }}
          >
            {message.text}
          </div>
        )}

        {/* ── Tab: Unified Settings ─────────── */}
        {activeTab === 'settings' && (
          <div className="artist-settings-compact">
            <details ref={accountSettingsRef} className="settings-accordion" open>
              <summary>
                <span>Account</span>
                <small>Name, role, marketing preferences</small>
              </summary>
              <div className="settings-accordion-body">
                <div className="settings-grid">
                  <div className="form-group">
                    <label className="form-label">Display Name</label>
                    <input type="text" value={acctName} onChange={(e) => setAcctName(e.target.value)} className="form-input" />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Role</label>
                    <input type="text" value={user?.role || ''} disabled className="form-input disabled" />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Member Since</label>
                    <input type="text" value={user?.created_at ? new Date(user.created_at).toLocaleDateString() : ''} disabled className="form-input disabled" />
                  </div>
                  <label className="form-checkbox-label settings-checkbox">
                    <input
                      type="checkbox"
                      checked={acctMarketing}
                      onChange={(e) => setAcctMarketing(e.target.checked)}
                    />
                    Receive marketing emails
                  </label>
                </div>
                <button type="button" className="action-btn" disabled={acctSaving} onClick={handleAccountSave}>
                  {acctSaving ? 'Saving...' : 'Update Account'}
                </button>
              </div>
            </details>

            <details className="settings-accordion">
              <summary>
                <span>Security</span>
                <small>Password and login protection</small>
              </summary>
              <div className="settings-accordion-body">
                <div className="settings-password-panel settings-password-panel--flat">
                  <div>
                    <p className="settings-section-title">Change Password</p>
                    <p className="settings-section-note">Updates the password for this portal login.</p>
                  </div>
                  <div className="settings-grid">
                    <input className="form-input" type="password" value={currentPassword} onChange={(e) => setCurrentPassword(e.target.value)} placeholder="Current password" />
                    <input className="form-input" type="password" value={newPassword} onChange={(e) => setNewPassword(e.target.value)} placeholder="New password" />
                    <input className="form-input" type="password" value={confirmPassword} onChange={(e) => setConfirmPassword(e.target.value)} placeholder="Confirm new password" />
                  </div>
                  <button type="button" className="action-btn" disabled={passwordSaving} onClick={handlePasswordChange}>
                    {passwordSaving ? 'Updating...' : 'Change Password'}
                  </button>
                </div>
              </div>
            </details>

            <details className="settings-accordion">
              <summary>
                <span>Profile</span>
                <small>Avatar, title, bio, specialties</small>
              </summary>
              <div className="settings-accordion-body">
                <div className="settings-profile-row">
                  <div
                    className="avatar-editor-preview"
                    onClick={() => avatarInputRef.current?.click()}
                  >
                    {avatarUrl ? (
                      <img src={avatarUrl} alt="Avatar" />
                    ) : (
                      <span className="avatar-editor-initial">
                        {(profile?.name || '?').charAt(0).toUpperCase()}
                      </span>
                    )}
                    <div className="avatar-editor-overlay">
                      {isUploadingAvatar ? '...' : 'Edit'}
                    </div>
                  </div>
                  <div>
                    <p className="settings-section-title">Profile Picture</p>
                    <p className="settings-section-note">JPG, PNG, or WebP.</p>
                  </div>
                  <input
                    ref={avatarInputRef}
                    type="file"
                    accept="image/jpeg,image/png,image/webp"
                    style={{ display: 'none' }}
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      if (f) handleAvatarUpload(f)
                      e.target.value = ''
                    }}
                  />
                </div>

                <div className="settings-grid">
                  <div className="form-group">
                    <label className="form-label">Title / Tagline</label>
                    <input
                      type="text"
                      className="form-input"
                      value={title}
                      onChange={(e) => setTitle(e.target.value)}
                      placeholder="e.g. Founder / 2nd Gen Tattoo Artist"
                    />
                  </div>
                  <div className="form-group settings-grid-wide">
                    <label className="form-label">Bio</label>
                    <textarea
                      className="form-input"
                      rows={4}
                      value={bio}
                      onChange={(e) => setBio(e.target.value)}
                      placeholder="Tell collectors about your style and experience..."
                    />
                  </div>
                  <div className="form-group settings-grid-wide">
                    <label className="form-label">Specialties</label>
                    <div className="settings-chip-row">
                      {SPECIALTY_OPTIONS.map((s) => (
                        <button
                          key={s}
                          type="button"
                          onClick={() => toggleSpecialty(s)}
                          className={`settings-chip${specialty.includes(s) ? ' active' : ''}`}
                        >
                          {s}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
                <SaveButton />
              </div>
            </details>

            <details className="settings-accordion">
              <summary>
                <span>Contact</span>
                <small>Phone, socials, website</small>
              </summary>
              <div className="settings-accordion-body">
                <div className="settings-grid">
                  <div className="form-group">
                    <label className="form-label">Email</label>
                    <input type="email" className="form-input disabled" value={user?.email ?? ''} disabled />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Phone</label>
                    <input type="tel" className="form-input" value={phone} onChange={(e) => setPhone(e.target.value)} placeholder="(555) 123-4567" />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Instagram</label>
                    <input type="url" className="form-input" value={instagramUrl} onChange={(e) => setInstagramUrl(e.target.value)} placeholder="https://instagram.com/yourhandle" />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Facebook</label>
                    <input type="url" className="form-input" value={facebookUrl} onChange={(e) => setFacebookUrl(e.target.value)} placeholder="https://facebook.com/yourpage" />
                  </div>
                  <div className="form-group">
                    <label className="form-label">TikTok</label>
                    <input type="url" className="form-input" value={tiktokUrl} onChange={(e) => setTiktokUrl(e.target.value)} placeholder="https://tiktok.com/@yourhandle" />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Website</label>
                    <input type="url" className="form-input" value={websiteUrl} onChange={(e) => setWebsiteUrl(e.target.value)} placeholder="https://yoursite.com" />
                  </div>
                </div>
                <SaveButton />
              </div>
            </details>

            <details ref={appSettingsRef} className="settings-accordion" open={requestedSection === 'app-settings' || appointmentTypesRequested || undefined}>
              <summary>
                <span>App Settings</span>
                <small>Configuration lives with each app</small>
              </summary>
              <div className="settings-accordion-body">
                <div className="settings-grid" style={{ marginBottom: '1rem' }}>
                  <div className="form-group">
                    <label className="form-label">Slot Interval</label>
                    <select className="form-input" value={bookingSlotInterval} onChange={(e) => setBookingSlotInterval(Number(e.target.value))}>
                      <option value={15}>15 minutes</option>
                      <option value={30}>30 minutes</option>
                      <option value={45}>45 minutes</option>
                      <option value={60}>60 minutes</option>
                    </select>
                  </div>
                  <div className="form-group">
                    <label className="form-label">Buffer</label>
                    <input
                      type="number"
                      min={0}
                      max={240}
                      step={15}
                      value={bookingBuffer}
                      onChange={(e) => setBookingBuffer(Number(e.target.value))}
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Minimum Notice</label>
                    <input
                      type="number"
                      min={0}
                      max={720}
                      step={1}
                      value={bookingMinNotice}
                      onChange={(e) => setBookingMinNotice(Number(e.target.value))}
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Max Advance</label>
                    <input
                      type="number"
                      min={1}
                      max={365}
                      step={1}
                      value={bookingMaxAdvance}
                      onChange={(e) => setBookingMaxAdvance(Number(e.target.value))}
                      className="form-input"
                    />
                  </div>
                  <div className="form-group">
                    <label className="form-label">Flash Deposit ($)</label>
                    <input
                      type="number"
                      min={0}
                      step={25}
                      placeholder="Default ($100)"
                      value={flashDepositAmount}
                      onChange={(e) => setFlashDepositAmount(e.target.value === '' ? '' : Number(e.target.value))}
                      className="form-input"
                    />
                  </div>
                  <label className="form-checkbox-label settings-checkbox">
                    <input
                      type="checkbox"
                      checked={bookingAllowSameDay}
                      onChange={(e) => setBookingAllowSameDay(e.target.checked)}
                    />
                    Allow same-day booking
                  </label>
                  <label className="form-checkbox-label settings-checkbox">
                    <input
                      type="checkbox"
                      checked={allowPrepayFull}
                      onChange={(e) => setAllowPrepayFull(e.target.checked)}
                    />
                    Allow prepayment in full
                  </label>
                </div>
                <div className="settings-session-types">
                  <div className="settings-session-types-head">
                    <div>
                      <p className="settings-section-title">Weekly Working Hours</p>
                      <p className="settings-section-note">Configure which days and hours clients can book on your calendar.</p>
                    </div>
                  </div>
                  <div className="settings-working-hours">
                    {[
                      { key: '0', label: 'Monday' },
                      { key: '1', label: 'Tuesday' },
                      { key: '2', label: 'Wednesday' },
                      { key: '3', label: 'Thursday' },
                      { key: '4', label: 'Friday' },
                      { key: '5', label: 'Saturday' },
                      { key: '6', label: 'Sunday' },
                    ].map((day) => {
                      const current = workingHours[day.key]
                      const isOpen = current !== null && current !== undefined
                      const startHour = isOpen && current ? current[0] : 11
                      const endHour = isOpen && current ? current[1] : 18
                      return (
                        <div key={day.key} className="settings-working-hours-row">
                          <label className="settings-working-hours-day">
                            <input
                              type="checkbox"
                              checked={isOpen}
                              onChange={(e) => {
                                setWorkingHours((prev) => ({
                                  ...prev,
                                  [day.key]: e.target.checked ? [11, 18] : null,
                                }))
                              }}
                            />
                            <span>{day.label}</span>
                          </label>
                          {isOpen ? (
                            <div className="settings-working-hours-inputs">
                              <select
                                className="form-input settings-working-hours-select"
                                value={startHour}
                                onChange={(e) => {
                                  const nextStart = Number(e.target.value)
                                  setWorkingHours((prev) => ({
                                    ...prev,
                                    [day.key]: [nextStart, endHour],
                                  }))
                                }}
                              >
                                {Array.from({ length: 24 }, (_, i) => (
                                  <option key={i} value={i}>
                                    {i === 0 ? '12 AM' : i < 12 ? `${i} AM` : i === 12 ? '12 PM' : `${i - 12} PM`}
                                  </option>
                                ))}
                              </select>
                              <span>to</span>
                              <select
                                className="form-input settings-working-hours-select"
                                value={endHour}
                                onChange={(e) => {
                                  const nextEnd = Number(e.target.value)
                                  setWorkingHours((prev) => ({
                                    ...prev,
                                    [day.key]: [startHour, nextEnd],
                                  }))
                                }}
                              >
                                {Array.from({ length: 24 }, (_, i) => (
                                  <option key={i} value={i}>
                                    {i === 0 ? '12 AM' : i < 12 ? `${i} AM` : i === 12 ? '12 PM' : `${i - 12} PM`}
                                  </option>
                                ))}
                              </select>
                            </div>
                          ) : (
                            <span className="settings-working-hours-closed">Closed / Off</span>
                          )}
                        </div>
                      )
                    })}
                  </div>
                </div>
                <SaveButton />
                <div
                  id="appointment-types"
                  ref={appointmentTypesRef}
                  className="settings-session-types"
                  tabIndex={-1}
                >
                  <div className="settings-session-types-head">
                    <div>
                      <p className="settings-section-title">Appointment Types</p>
                      <p className="settings-section-note">Public booking uses these service options for duration, deposits, pricing, and placement-aware scheduling.</p>
                    </div>
                    <button
                      type="button"
                      className="action-btn"
                      disabled={sessionTypesSaving}
                      onClick={handleInstallDefaultSessionTypes}
                    >
                      Install Defaults
                    </button>
                  </div>

                  {sessionTypesLoading ? (
                    <div className="settings-session-empty">Loading appointment types...</div>
                  ) : sessionTypeRows.length === 0 ? (
                    <div className="settings-session-empty">No artist-configured appointment types yet. Public booking is using fallback options.</div>
                  ) : (
                    <div className="settings-session-list">
                      {sessionTypeRows.map((type) => {
                        const draft = editingSessionTypes[type.id] || sessionTypeToDraft(type)
                        return (
                          <div key={type.id} className={`settings-session-row${type.is_active ? '' : ' inactive'}`}>
                            <div className="settings-session-fields">
                              <input
                                className="form-input"
                                value={draft.label}
                                onChange={(e) => setEditingSessionTypes((prev) => ({ ...prev, [type.id]: { ...draft, label: e.target.value } }))}
                                aria-label="Appointment type label"
                              />
                              <input
                                className="form-input"
                                value={draft.size}
                                onChange={(e) => setEditingSessionTypes((prev) => ({ ...prev, [type.id]: { ...draft, size: e.target.value } }))}
                                aria-label="Appointment type size"
                              />
                              <input
                                className="form-input"
                                value={draft.placements}
                                onChange={(e) => setEditingSessionTypes((prev) => ({ ...prev, [type.id]: { ...draft, placements: e.target.value } }))}
                                placeholder="placements"
                                aria-label="Allowed placements"
                              />
                              <input
                                className="form-input"
                                type="number"
                                min={15}
                                max={720}
                                step={15}
                                value={draft.duration_minutes}
                                onChange={(e) => setEditingSessionTypes((prev) => ({ ...prev, [type.id]: { ...draft, duration_minutes: Number(e.target.value) } }))}
                                aria-label="Duration minutes"
                              />
                              <input
                                className="form-input"
                                type="number"
                                min={0}
                                max={240}
                                step={15}
                                value={draft.buffer_minutes ?? 0}
                                onChange={(e) => setEditingSessionTypes((prev) => ({ ...prev, [type.id]: { ...draft, buffer_minutes: Number(e.target.value) } }))}
                                aria-label="Buffer minutes"
                              />
                              <div className="settings-deposit-control">
                                <label className="settings-deposit-toggle">
                                  <input
                                    type="checkbox"
                                    checked={draft.deposit_amount > 0}
                                    onChange={(e) => setEditingSessionTypes((prev) => ({
                                      ...prev,
                                      [type.id]: { ...draft, deposit_amount: e.target.checked ? (draft.deposit_amount || 100) : 0 },
                                    }))}
                                  />
                                  <span>Deposit</span>
                                </label>
                                <input
                                  className="form-input"
                                  type="number"
                                  min={1}
                                  step={25}
                                  value={draft.deposit_amount > 0 ? draft.deposit_amount : ''}
                                  disabled={draft.deposit_amount <= 0}
                                  placeholder="None"
                                  onChange={(e) => setEditingSessionTypes((prev) => ({ ...prev, [type.id]: { ...draft, deposit_amount: Number(e.target.value) } }))}
                                  aria-label="Deposit amount"
                                />
                              </div>
                              <input
                                className="form-input"
                                value={draft.hourly_rate}
                                onChange={(e) => setEditingSessionTypes((prev) => ({ ...prev, [type.id]: { ...draft, hourly_rate: e.target.value } }))}
                                placeholder="$/hr"
                                aria-label="Hourly rate"
                              />
                              <input
                                className="form-input"
                                value={draft.price_min}
                                onChange={(e) => setEditingSessionTypes((prev) => ({ ...prev, [type.id]: { ...draft, price_min: e.target.value } }))}
                                placeholder="min $"
                                aria-label="Minimum price"
                              />
                              <input
                                className="form-input"
                                value={draft.price_max}
                                onChange={(e) => setEditingSessionTypes((prev) => ({ ...prev, [type.id]: { ...draft, price_max: e.target.value } }))}
                                placeholder="max $"
                                aria-label="Maximum price"
                              />
                            </div>
                            <div className="settings-session-actions">
                              <button type="button" className="action-btn" disabled={sessionTypesSaving} onClick={() => handleUpdateSessionType(type.id)}>Save</button>
                              <button type="button" className="action-btn" disabled={sessionTypesSaving} onClick={() => handleToggleSessionType(type)}>
                                {type.is_active ? 'Hide' : 'Activate'}
                              </button>
                              <button type="button" className="action-btn settings-danger-btn" disabled={sessionTypesSaving} onClick={() => handleDeleteSessionType(type.id)}>Delete</button>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}

                  <div className="settings-session-new">
                    <div className="settings-session-fields">
                      <input
                        className="form-input"
                        value={sessionTypeDraft.label}
                        onChange={(e) => setSessionTypeDraft((prev) => ({ ...prev, label: e.target.value }))}
                        placeholder="Label"
                        aria-label="New appointment type label"
                      />
                      <input
                        className="form-input"
                        value={sessionTypeDraft.size}
                        onChange={(e) => setSessionTypeDraft((prev) => ({ ...prev, size: e.target.value }))}
                        placeholder="size"
                        aria-label="New appointment type size"
                      />
                      <input
                        className="form-input"
                        value={sessionTypeDraft.placements}
                        onChange={(e) => setSessionTypeDraft((prev) => ({ ...prev, placements: e.target.value }))}
                        placeholder="placements"
                        aria-label="New allowed placements"
                      />
                      <input
                        className="form-input"
                        type="number"
                        min={15}
                        max={720}
                        step={15}
                        value={sessionTypeDraft.duration_minutes}
                        onChange={(e) => setSessionTypeDraft((prev) => ({ ...prev, duration_minutes: Number(e.target.value) }))}
                        aria-label="New duration minutes"
                      />
                      <input
                        className="form-input"
                        type="number"
                        min={0}
                        max={240}
                        step={15}
                        value={sessionTypeDraft.buffer_minutes ?? 0}
                        onChange={(e) => setSessionTypeDraft((prev) => ({ ...prev, buffer_minutes: Number(e.target.value) }))}
                        aria-label="New buffer minutes"
                      />
                      <div className="settings-deposit-control">
                        <label className="settings-deposit-toggle">
                          <input
                            type="checkbox"
                            checked={sessionTypeDraft.deposit_amount > 0}
                            onChange={(e) => setSessionTypeDraft((prev) => ({
                              ...prev,
                              deposit_amount: e.target.checked ? (prev.deposit_amount || 100) : 0,
                            }))}
                          />
                          <span>Deposit</span>
                        </label>
                        <input
                          className="form-input"
                          type="number"
                          min={1}
                          step={25}
                          value={sessionTypeDraft.deposit_amount > 0 ? sessionTypeDraft.deposit_amount : ''}
                          disabled={sessionTypeDraft.deposit_amount <= 0}
                          placeholder="None"
                          onChange={(e) => setSessionTypeDraft((prev) => ({ ...prev, deposit_amount: Number(e.target.value) }))}
                          aria-label="New deposit amount"
                        />
                      </div>
                      <input
                        className="form-input"
                        value={sessionTypeDraft.hourly_rate}
                        onChange={(e) => setSessionTypeDraft((prev) => ({ ...prev, hourly_rate: e.target.value }))}
                        placeholder="$/hr"
                        aria-label="New hourly rate"
                      />
                      <input
                        className="form-input"
                        value={sessionTypeDraft.price_min}
                        onChange={(e) => setSessionTypeDraft((prev) => ({ ...prev, price_min: e.target.value }))}
                        placeholder="min $"
                        aria-label="New minimum price"
                      />
                      <input
                        className="form-input"
                        value={sessionTypeDraft.price_max}
                        onChange={(e) => setSessionTypeDraft((prev) => ({ ...prev, price_max: e.target.value }))}
                        placeholder="max $"
                        aria-label="New maximum price"
                      />
                    </div>
                    <button type="button" className="action-btn" disabled={sessionTypesSaving} onClick={handleCreateSessionType}>
                      Add Appointment Type
                    </button>
                  </div>
                </div>
                <div className="settings-session-types">
                  <div className="settings-session-types-head">
                    <div>
                      <p className="settings-section-title">Booking Add-ons</p>
                      <p className="settings-section-note">Public booking reads these optional extras directly. Hidden add-ons stay out of the collector form.</p>
                    </div>
                    <button
                      type="button"
                      className="action-btn"
                      disabled={bookingAddOnsSaving}
                      onClick={handleInstallDefaultBookingAddOns}
                    >
                      Install Defaults
                    </button>
                  </div>

                  {bookingAddOnsLoading ? (
                    <div className="settings-session-empty">Loading booking add-ons...</div>
                  ) : bookingAddOnRows.length === 0 ? (
                    <div className="settings-session-empty">No booking add-ons configured yet.</div>
                  ) : (
                    <div className="settings-session-list">
                      {bookingAddOnRows.map((addOn) => {
                        const draft = editingBookingAddOns[addOn.id] || bookingAddOnToDraft(addOn)
                        return (
                          <div key={addOn.id} className={`settings-session-row${addOn.is_active ? '' : ' inactive'}`}>
                            <div className="settings-session-fields">
                              <input
                                className="form-input"
                                value={draft.label}
                                onChange={(e) => setEditingBookingAddOns((prev) => ({ ...prev, [addOn.id]: { ...draft, label: e.target.value } }))}
                                aria-label="Booking add-on label"
                              />
                              <input
                                className="form-input"
                                value={draft.description}
                                onChange={(e) => setEditingBookingAddOns((prev) => ({ ...prev, [addOn.id]: { ...draft, description: e.target.value } }))}
                                placeholder="Description"
                                aria-label="Booking add-on description"
                              />
                              <input
                                className="form-input"
                                value={draft.price_amount}
                                onChange={(e) => setEditingBookingAddOns((prev) => ({ ...prev, [addOn.id]: { ...draft, price_amount: e.target.value } }))}
                                placeholder="Price"
                                aria-label="Booking add-on price"
                              />
                              <input
                                className="form-input"
                                value={draft.duration_minutes}
                                onChange={(e) => setEditingBookingAddOns((prev) => ({ ...prev, [addOn.id]: { ...draft, duration_minutes: e.target.value } }))}
                                placeholder="Extra min"
                                aria-label="Booking add-on duration"
                              />
                            </div>
                            <div className="settings-session-actions">
                              <button type="button" className="action-btn" disabled={bookingAddOnsSaving} onClick={() => handleUpdateBookingAddOn(addOn.id)}>Save</button>
                              <button type="button" className="action-btn" disabled={bookingAddOnsSaving} onClick={() => handleToggleBookingAddOn(addOn)}>
                                {addOn.is_active ? 'Hide' : 'Activate'}
                              </button>
                              <button type="button" className="action-btn settings-danger-btn" disabled={bookingAddOnsSaving} onClick={() => handleDeleteBookingAddOn(addOn.id)}>Delete</button>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}

                  <div className="settings-session-new">
                    <div className="settings-session-fields">
                      <input
                        className="form-input"
                        value={bookingAddOnDraft.label}
                        onChange={(e) => setBookingAddOnDraft((prev) => ({ ...prev, label: e.target.value }))}
                        placeholder="Label"
                        aria-label="New booking add-on label"
                      />
                      <input
                        className="form-input"
                        value={bookingAddOnDraft.description}
                        onChange={(e) => setBookingAddOnDraft((prev) => ({ ...prev, description: e.target.value }))}
                        placeholder="Description"
                        aria-label="New booking add-on description"
                      />
                      <input
                        className="form-input"
                        value={bookingAddOnDraft.price_amount}
                        onChange={(e) => setBookingAddOnDraft((prev) => ({ ...prev, price_amount: e.target.value }))}
                        placeholder="Price"
                        aria-label="New booking add-on price"
                      />
                      <input
                        className="form-input"
                        value={bookingAddOnDraft.duration_minutes}
                        onChange={(e) => setBookingAddOnDraft((prev) => ({ ...prev, duration_minutes: e.target.value }))}
                        placeholder="Extra min"
                        aria-label="New booking add-on duration"
                      />
                    </div>
                    <button type="button" className="action-btn" disabled={bookingAddOnsSaving} onClick={handleCreateBookingAddOn}>
                      Add Booking Add-on
                    </button>
                  </div>
                </div>
                <div className="app-settings-grid">
                  <a className="app-settings-card" href="/dashboard/huddle?charm=bookings">
                    <span>Huddl</span>
                    <strong>Booking and CRM settings</strong>
                    <small>Booking source, calendar behavior, pipeline intake, client workflow.</small>
                  </a>
                  <a className="app-settings-card" href="/dashboard/hustle">
                    <span>Hustle</span>
                    <strong>Portfolio, flash, and shop</strong>
                    <small>Gallery, flash inventory, storefront, commerce display.</small>
                  </a>
                  <a className="app-settings-card" href="/dashboard/courier?tab=channels">
                    <span>Courier</span>
                    <strong>Messaging and channels</strong>
                    <small>Email, social, SMS, archives, templates, routing.</small>
                  </a>
                  <a className="app-settings-card" href="/dashboard/vault">
                    <span>Vault</span>
                    <strong>Files and storage</strong>
                    <small>Asset storage, retention, permissions, document surfaces.</small>
                  </a>
                </div>
              </div>
            </details>

            {user?.role === 'artist' ? (
              <a className="app-settings-card" href="/dashboard?portal=hub&workflow=connections">
                <span>Connections</span>
                <strong>Manage your connected services in Hub</strong>
                <small>Stripe, Google Calendar, Pinterest, and your Ryozan email are managed in one place.</small>
              </a>
            ) : (
            <details ref={integrationsSettingsRef} className="settings-accordion" open={requestedSection === 'integrations' || undefined}>
              <summary>
                <span>Integrations</span>
                <small>Stripe, email, Google, and Pinterest connections</small>
              </summary>
              <div className="settings-accordion-body">
                <IntegrationsTab
                  stripeStatus={stripeStatus}
                  emailStatus={emailStatus}
                  loading={integrationsLoading}
                  stripeOnboarding={false}
                  user={user}
                  googleConnected={googleConnected}
                  googleManaged={googleManaged}
                  pinterestConnected={pinterestConnected}
                  pinterestUsername={pinterestUsername}
                  onLoadIntegrations={async () => {
                    setIntegrationsLoading(true)
                    try {
                      const [stripe, email, google, pinterest] = await Promise.allSettled([
                        stripeConnect.status(),
                        emailConnect.status(),
                        googleAuth.getStatus(),
                        pinterestAuth.getStatus(),
                      ])
                      if (stripe.status === 'fulfilled') setStripeStatus(stripe.value)
                      if (email.status === 'fulfilled') setEmailStatus(email.value)
                      if (google.status === 'fulfilled') {
                        setGoogleConnected(google.value.connected)
                        setGoogleManaged(google.value.managed)
                      }
                      if (pinterest.status === 'fulfilled') {
                        setPinterestConnected(pinterest.value.connected)
                        setPinterestUsername(pinterest.value.username || null)
                      }
                    } catch { /* silent */ }
                    finally { setIntegrationsLoading(false) }
                  }}
                  onStripeOnboard={async () => {
                    window.location.assign('/dashboard?portal=hub&workflow=connections')
                  }}
                  onGoogleDisconnect={async () => {
                    try {
                      await googleAuth.disconnect()
                      setGoogleConnected(false)
                      toast.success('Google account disconnected')
                    } catch { toast.error('Failed to disconnect Google') }
                  }}
                  onGoogleConnect={async () => {
                    try {
                      const { authorization_url } = await googleAuth.getConnectUrl()
                      window.location.href = authorization_url
                    } catch { toast.error('Failed to start Google connection') }
                  }}
                  onPinterestDisconnect={async () => {
                    try {
                      await pinterestAuth.disconnect()
                      setPinterestConnected(false)
                      setPinterestUsername(null)
                      toast.success('Pinterest account disconnected')
                    } catch { toast.error('Failed to disconnect Pinterest') }
                  }}
                  onPinterestConnect={async () => {
                    try {
                      const { authorize_url } = await pinterestAuth.getConnectUrl()
                      window.location.href = authorize_url
                    } catch { toast.error('Failed to start Pinterest connection') }
                  }}
                  toast={toast}
                />
              </div>
            </details>
            )}
          </div>
        )}

        {/* ── Tab: Profile & Bio ─────────────── */}
        {activeTab === 'profile' && (
          <div className="profile-form">
            {/* Avatar upload */}
            <div className="form-group" style={{ display: 'flex', alignItems: 'center', gap: '1.25rem', marginBottom: '1.5rem' }}>
              <div
                className="avatar-editor-preview"
                onClick={() => avatarInputRef.current?.click()}
              >
                {avatarUrl ? (
                  <img src={avatarUrl} alt="Avatar" />
                ) : (
                  <span className="avatar-editor-initial">
                    {(profile?.name || '?').charAt(0).toUpperCase()}
                  </span>
                )}
                <div className="avatar-editor-overlay">
                  {isUploadingAvatar ? '...' : 'Edit'}
                </div>
              </div>
              <div>
                <p style={{ color: 'var(--text)', fontSize: '0.9rem', fontWeight: 600 }}>Profile Picture</p>
                <p style={{ color: 'var(--muted)', fontSize: '0.75rem' }}>
                  Click to upload. JPG, PNG, or WebP.
                </p>
              </div>
              <input
                ref={avatarInputRef}
                type="file"
                accept="image/jpeg,image/png,image/webp"
                style={{ display: 'none' }}
                onChange={(e) => {
                  const f = e.target.files?.[0]
                  if (f) handleAvatarUpload(f)
                  e.target.value = ''
                }}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Title / Tagline</label>
              <input
                type="text"
                className="form-input"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g. Founder / 2nd Gen Tattoo Artist"
              />
              <span className="form-hint" style={{ fontSize: '0.75rem', color: 'var(--muted)', marginTop: '0.25rem', display: 'block' }}>
                Shown below your name on your public artist page
              </span>
            </div>

            <div className="form-group">
              <label className="form-label">Bio</label>
              <textarea
                className="form-input"
                rows={5}
                value={bio}
                onChange={(e) => setBio(e.target.value)}
                placeholder="Tell collectors about your style and experience..."
                style={{ resize: 'none' }}
              />
            </div>

            <div className="form-group">
              <label className="form-label">Specialties</label>
              <div style={{ display: 'flex', flexWrap: 'wrap', gap: '0.5rem' }}>
                {SPECIALTY_OPTIONS.map((s) => (
                  <button
                    key={s}
                    type="button"
                    onClick={() => toggleSpecialty(s)}
                    style={{
                      padding: '0.4rem 0.85rem',
                      borderRadius: '999px',
                      fontSize: '0.8rem',
                      cursor: 'pointer',
                      border: specialty.includes(s)
                        ? '1px solid var(--gold)'
                        : '1px solid var(--panel-border)',
                      background: specialty.includes(s)
                        ? 'rgba(250, 174, 21, 0.15)'
                        : 'transparent',
                      color: specialty.includes(s) ? 'var(--gold)' : 'var(--text)',
                      transition: 'all 0.2s',
                    }}
                  >
                    {s}
                  </button>
                ))}
              </div>
              {specialty.length > 0 && (
                <p style={{ fontSize: '0.75rem', color: 'var(--muted)', marginTop: '0.5rem' }}>
                  Selected: {specialty.join(', ')}
                </p>
              )}
            </div>

            <SaveButton />
          </div>
        )}

        {/* ── Tab: Contact & Socials ─────────── */}
        {activeTab === 'contact' && (
          <div className="profile-form">
            <div className="form-group">
              <label className="form-label">Email</label>
              <input
                type="email"
                className="form-input"
                value={user?.email ?? ''}
                disabled
                style={{ opacity: 0.6 }}
              />
              <p style={{ fontSize: '0.7rem', color: 'var(--muted)', marginTop: '0.3rem' }}>
                Managed in Profile settings
              </p>
            </div>

            <div className="form-group">
              <label className="form-label">Phone</label>
              <input
                type="tel"
                className="form-input"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="(555) 123-4567"
              />
            </div>

            <div className="form-group">
              <label className="form-label">Instagram</label>
              <input
                type="url"
                className="form-input"
                value={instagramUrl}
                onChange={(e) => setInstagramUrl(e.target.value)}
                placeholder="https://instagram.com/yourhandle"
              />
            </div>

            <div className="form-group">
              <label className="form-label">Facebook</label>
              <input
                type="url"
                className="form-input"
                value={facebookUrl}
                onChange={(e) => setFacebookUrl(e.target.value)}
                placeholder="https://facebook.com/yourpage"
              />
            </div>

            <div className="form-group">
              <label className="form-label">TikTok</label>
              <input
                type="url"
                className="form-input"
                value={tiktokUrl}
                onChange={(e) => setTiktokUrl(e.target.value)}
                placeholder="https://tiktok.com/@yourhandle"
              />
            </div>

            <div className="form-group">
              <label className="form-label">Website</label>
              <input
                type="url"
                className="form-input"
                value={websiteUrl}
                onChange={(e) => setWebsiteUrl(e.target.value)}
                placeholder="https://yoursite.com"
              />
            </div>

            <SaveButton />
          </div>
        )}

        {/* ── Tab: Gallery ───────────────────── */}
        {activeTab === 'gallery' && (
          <div>
            <p style={{ fontSize: '0.85rem', color: 'var(--muted)', marginBottom: '1rem' }}>
              Manage your portfolio gallery. These images appear on your public artist page.
            </p>

            <div className="portfolio-gallery-grid">
              {galleryImages.map((url) => (
                <div key={url} className="portfolio-gallery-item">
                  <img src={url} alt="Portfolio" referrerPolicy="no-referrer" />
                  <button
                    className="portfolio-gallery-remove"
                    onClick={() => handleGalleryRemove(url)}
                  >
                    &times;
                  </button>
                </div>
              ))}

              {/* Upload zone */}
              <div
                className="portfolio-upload-zone"
                onClick={() => fileInputRef.current?.click()}
              >
                {isUploading ? (
                  <span style={{ color: 'var(--muted)' }}>Uploading...</span>
                ) : (
                  <>
                    <span style={{ fontSize: '2rem', lineHeight: 1 }}>+</span>
                    <span style={{ fontSize: '0.75rem', color: 'var(--muted)' }}>Upload</span>
                  </>
                )}
              </div>
            </div>

            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: 'none' }}
              onChange={(e) => {
                if (e.target.files?.length) {
                  handleGalleryUpload(e.target.files)
                  e.target.value = '' // reset so same file can be re-selected
                }
              }}
            />
          </div>
        )}


        {activeTab === 'flash' && (
          <div className="flash-mgr">
            {/* Header — only shown when designs exist */}
            {flashDesigns.length > 0 && (
              <div className="flash-mgr-header">
                <p className="flash-mgr-desc">
                  Ready-to-ink designs collectors can browse and claim.
                </p>
                <button
                  className="flash-mgr-add-btn"
                  onClick={() => setShowFlashModal(true)}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                  Add Flash
                </button>
              </div>
            )}

            {/* Empty state */}
            {!flashLoading && flashDesigns.length === 0 && (
              <div className="flash-empty-state">
                <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                </svg>
                <h3 className="flash-empty-title">No flash designs yet</h3>
                <p className="flash-empty-sub">Upload your ready-to-ink designs. Collectors can browse and claim them to start a booking.</p>
                <button
                  className="flash-empty-cta"
                  onClick={() => {
                    if (!flashLoading) loadFlash()
                    setShowFlashModal(true)
                  }}
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                  Add Flash
                </button>
              </div>
            )}

            {/* Flash grid */}
            {!flashLoading && flashDesigns.length > 0 && (
              <div className="flash-grid">
                {flashDesigns.map(design => (
                  <div key={design.id} className={`flash-card flash-card--${design.status}`}>
                    <div className="flash-card-img">
                      <img src={design.image_url} alt={design.title || 'Flash design'} />
                      <span className={`flash-card-badge flash-card-badge--${design.status}`}>
                        {design.status === 'available' ? 'Available' : design.status === 'claimed' ? 'Claimed' : 'Sold'}
                      </span>
                    </div>

                    {flashEditId === design.id ? (
                      <div className="flash-card-edit">
                        <input
                          className="flash-form-input"
                          type="text"
                          placeholder="Title"
                          value={flashEditMeta.title}
                          onChange={e => setFlashEditMeta(m => ({ ...m, title: e.target.value }))}
                        />
                        <div className="flash-form-row--half" style={{ display: 'flex', gap: '0.5rem' }}>
                          <input className="flash-form-input" type="number" placeholder="Price" value={flashEditMeta.price} onChange={e => setFlashEditMeta(m => ({ ...m, price: e.target.value }))} />
                          <input className="flash-form-input" type="text" placeholder="Size" value={flashEditMeta.size_estimate} onChange={e => setFlashEditMeta(m => ({ ...m, size_estimate: e.target.value }))} />
                        </div>
                        <input className="flash-form-input" type="text" placeholder="Style" value={flashEditMeta.style} onChange={e => setFlashEditMeta(m => ({ ...m, style: e.target.value }))} />
                        <div className="flash-card-edit-actions">
                          <button className="flash-stage-cancel" onClick={() => setFlashEditId(null)}>Cancel</button>
                          <button className="flash-stage-submit" onClick={() => handleFlashUpdate(design.id)}>Save</button>
                        </div>
                      </div>
                    ) : (
                      <div className="flash-card-info">
                        <span className="flash-card-name">{design.title || 'Untitled'}</span>
                        <div className="flash-card-meta">
                          {design.price != null && <span className="flash-card-price">${design.price}</span>}
                          {design.size_estimate && <span className="flash-card-size">{design.size_estimate}</span>}
                          {design.style && <span className="flash-card-style">{design.style}</span>}
                        </div>
                        <div className="flash-card-controls">
                          <button
                            className="flash-ctrl-btn"
                            onClick={() => {
                              setFlashEditId(design.id)
                              setFlashEditMeta({
                                title: design.title || '',
                                description: design.description || '',
                                price: design.price != null ? String(design.price) : '',
                                size_estimate: design.size_estimate || '',
                                style: design.style || '',
                              })
                            }}
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"/></svg>
                          </button>
                          <button
                            className={`flash-ctrl-btn flash-ctrl-toggle${design.status === 'available' ? '' : ' flash-ctrl-toggle--off'}`}
                            onClick={() => handleFlashStatusToggle(design)}
                            title={design.status === 'available' ? 'Mark as Sold' : 'Mark as Available'}
                          >
                            {design.status === 'available' ? (
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>
                            ) : (
                              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24"/><line x1="1" y1="1" x2="23" y2="23"/></svg>
                            )}
                          </button>
                          <button
                            className="flash-ctrl-btn flash-ctrl-delete"
                            onClick={() => handleFlashRemove(design.id)}
                          >
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M10 11v6"/><path d="M14 11v6"/><path d="M9 6V4h6v2"/></svg>
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}

            {/* Flash upload modal */}
            {showFlashModal && (
              <FlashUploadModal
                onClose={() => setShowFlashModal(false)}
                onPublished={(designs) => {
                  setFlashDesigns(prev => [...designs, ...prev])
                  setShowFlashModal(false)
                }}
              />
            )}
          </div>
        )}

        {/* ── Tab: Shop (placeholder) ────────── */}
        {activeTab === 'shop' && (
          <div style={{ textAlign: 'center', padding: '3rem 1rem' }}>
            <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ marginBottom: '0.5rem' }}>
              <path d="M6 2 3 6v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2V6l-3-4Z" /><line x1="3" y1="6" x2="21" y2="6" /><path d="M16 10a4 4 0 0 1-8 0" />
            </svg>
            <h3 style={{ color: 'var(--text)', marginBottom: '0.5rem' }}>Merch & Prints</h3>
            <p style={{ color: 'var(--muted)', fontSize: '0.9rem' }}>
              Shop management coming soon. Sell prints, merch, and other items
              directly through your portfolio.
            </p>
          </div>
        )}

        {/* ── Account Settings ── */}
        {activeTab === 'account' && (
          <div className="profile-form">
            <div className="form-group">
              <label className="form-label">Email</label>
              <input type="email" value={user?.email || ''} disabled className="form-input disabled" />
            </div>

            <div className="form-group">
              <label className="form-label">Display Name</label>
              <input
                type="text"
                value={acctName}
                onChange={(e) => setAcctName(e.target.value)}
                className="form-input"
              />
            </div>

            <div className="form-group">
              <label className="form-label">Role</label>
              <input type="text" value={user?.role || ''} disabled className="form-input disabled" />
            </div>

            <div className="form-group">
              <label className="form-checkbox-label">
                <input
                  type="checkbox"
                  checked={acctMarketing}
                  onChange={(e) => setAcctMarketing(e.target.checked)}
                />
                Receive marketing emails
              </label>
            </div>

            <div className="form-group">
              <label className="form-label">Member Since</label>
              <input
                type="text"
                value={user?.created_at ? new Date(user.created_at).toLocaleDateString() : ''}
                disabled
                className="form-input disabled"
              />
            </div>

            <button
              type="button"
              className="action-btn"
              disabled={acctSaving}
              onClick={async () => {
                setAcctSaving(true)
                try {
                  await usersApi.updateProfile({ name: acctName, marketing_opt_in: acctMarketing })
                  await fetchMe()
                  toast.success('Account updated')
                } catch {
                  toast.error('Failed to update account')
                } finally {
                  setAcctSaving(false)
                }
              }}
              style={{ marginTop: '0.5rem' }}
            >
              {acctSaving ? 'Saving...' : 'Update Account'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}


/* ── FlashUploadModal ── */

interface DesignMeta {
  title: string
  price: string
  size: string
  style: string
  description: string
}

const emptyMeta = (): DesignMeta => ({
  title: '', price: '', size: '', style: '', description: '',
})

function StepIndicator({ step }: { step: number }) {
  return (
    <div className="flash-modal-steps">
      {[1, 2, 3, 4].map((s) => (
        <div key={s} className="flash-modal-step-item">
          <div className={`flash-modal-step-dot${step === s ? ' active' : step > s ? ' done' : ''}`}>
            {step > s ? (
              <svg width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="3" strokeLinecap="round" strokeLinejoin="round"><polyline points="20 6 9 17 4 12"/></svg>
            ) : s}
          </div>
          {s < 4 && <div className={`flash-modal-step-line${step > s ? ' done' : ''}`} />}
        </div>
      ))}
    </div>
  )
}

function FlashUploadModal({
  onClose,
  onPublished,
}: {
  onClose: () => void
  onPublished: (designs: FlashDesign[]) => void
}) {
  const [step, setStep] = useState<1 | 2 | 3 | 4>(1)
  const [file, setFile] = useState<File | null>(null)
  const [preview, setPreview] = useState<string | null>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [sheetType, setSheetType] = useState<'single' | 'multi'>('single')
  const [designCount, setDesignCount] = useState(2)
  const [activeDesignTab, setActiveDesignTab] = useState(0)
  const [metas, setMetas] = useState<DesignMeta[]>([emptyMeta()])
  const [publishing, setPublishing] = useState(false)
  const [publishError, setPublishError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const totalDesigns = sheetType === 'single' ? 1 : designCount

  // Sync metas array length when count changes
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect -- intentional: resize metas to match user-selected count
    setMetas(prev => {
      const next = [...prev]
      while (next.length < totalDesigns) next.push(emptyMeta())
      return next.slice(0, totalDesigns)
    })
  }, [totalDesigns])

  const handleFileSelect = useCallback((f: File) => {
    setFile(f)
    setPreview(URL.createObjectURL(f))
  }, [])

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    const f = e.dataTransfer.files?.[0]
    if (f && f.type.startsWith('image/')) handleFileSelect(f)
  }

  const updateMeta = (idx: number, field: keyof DesignMeta, value: string) => {
    setMetas(prev => prev.map((m, i) => i === idx ? { ...m, [field]: value } : m))
  }

  const handlePublish = async () => {
    if (!file) return
    setPublishing(true)
    setPublishError(null)
    const published: FlashDesign[] = []
    try {
      for (let i = 0; i < totalDesigns; i++) {
        const m = metas[i]
        const design = await flashApi.create(file, {
          title: m.title || undefined,
          description: m.description || undefined,
          price: m.price ? parseFloat(m.price) : undefined,
          size_estimate: m.size || undefined,
          style: m.style || undefined,
        })
        published.push(design)
      }
      onPublished(published)
    } catch (err: unknown) {
      setPublishError(err instanceof Error ? err.message : 'Publish failed.')
      setPublishing(false)
    }
  }

  // Close on backdrop click
  const handleBackdrop = (e: React.MouseEvent<HTMLDivElement>) => {
    if (e.target === e.currentTarget) onClose()
  }

  const stepLabels = ['Upload', 'Sheet Type', 'Details', 'Review']

  return ReactDOM.createPortal(
    <div className="flash-modal-overlay" onClick={handleBackdrop}>
      <div className="flash-modal">
        {/* Header */}
        <div className="flash-modal-header">
          <div>
            <span className="flash-modal-step-label">{stepLabels[step - 1]}</span>
            <h2 className="flash-modal-title">Add Flash</h2>
          </div>
          <button className="flash-modal-close" onClick={onClose} aria-label="Close">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
          </button>
        </div>

        <StepIndicator step={step} />

        {/* Body */}
        <div className="flash-modal-body">

          {/* Step 1: Upload */}
          {step === 1 && (
            <div className="flash-upload-step">
              {!preview ? (
                <div
                  className={`flash-upload-zone${isDragging ? ' dragging' : ''}`}
                  onDragOver={(e) => { e.preventDefault(); setIsDragging(true) }}
                  onDragLeave={() => setIsDragging(false)}
                  onDrop={handleDrop}
                  onClick={() => fileInputRef.current?.click()}
                >
                  <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="var(--gold)" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round">
                    <polyline points="16 16 12 12 8 16"/><line x1="12" y1="12" x2="12" y2="21"/>
                    <path d="M20.39 18.39A5 5 0 0 0 18 9h-1.26A8 8 0 1 0 3 16.3"/>
                  </svg>
                  <p className="flash-upload-zone-label">Drag & drop your image here</p>
                  <p className="flash-upload-zone-sub">or click to browse — JPG, PNG, WebP</p>
                  <input
                    ref={fileInputRef}
                    type="file"
                    accept="image/*"
                    style={{ display: 'none' }}
                    onChange={(e) => {
                      const f = e.target.files?.[0]
                      if (f) handleFileSelect(f)
                      e.target.value = ''
                    }}
                  />
                </div>
              ) : (
                <div className="flash-upload-preview">
                  <img src={preview} alt="Preview" className="flash-upload-preview-img" />
                  <button
                    className="flash-upload-reselect"
                    onClick={() => { setFile(null); setPreview(null) }}
                  >
                    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="3 6 5 6 21 6"/><path d="M19 6l-1 14H6L5 6"/><path d="M9 6V4h6v2"/></svg>
                    Remove
                  </button>
                </div>
              )}
            </div>
          )}

          {/* Step 2: Sheet Type */}
          {step === 2 && (
            <div className="flash-sheet-step">
              <div className="flash-sheet-preview">
                {preview && <img src={preview} alt="Sheet preview" className="flash-sheet-preview-img" />}
              </div>
              <div className="flash-sheet-options">
                <p className="flash-sheet-question">What's on this sheet?</p>
                <div className="flash-sheet-cards">
                  <button
                    className={`flash-sheet-card${sheetType === 'single' ? ' selected' : ''}`}
                    onClick={() => setSheetType('single')}
                  >
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="3" y="3" width="18" height="18" rx="2"/>
                    </svg>
                    <span className="flash-sheet-card-label">Single design</span>
                    <span className="flash-sheet-card-sub">One tattoo on the sheet</span>
                  </button>
                  <button
                    className={`flash-sheet-card${sheetType === 'multi' ? ' selected' : ''}`}
                    onClick={() => setSheetType('multi')}
                  >
                    <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round">
                      <rect x="2" y="2" width="9" height="9" rx="1"/><rect x="13" y="2" width="9" height="9" rx="1"/>
                      <rect x="2" y="13" width="9" height="9" rx="1"/><rect x="13" y="13" width="9" height="9" rx="1"/>
                    </svg>
                    <span className="flash-sheet-card-label">Multiple designs</span>
                    <span className="flash-sheet-card-sub">Several tattoos on one sheet</span>
                  </button>
                </div>
                {sheetType === 'multi' && (
                  <div className="flash-sheet-counter">
                    <span className="flash-sheet-counter-label">How many designs?</span>
                    <div className="flash-sheet-counter-ctrl">
                      <button
                        className="flash-sheet-counter-btn"
                        onClick={() => setDesignCount(c => Math.max(2, c - 1))}
                        disabled={designCount <= 2}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="5" y1="12" x2="19" y2="12"/></svg>
                      </button>
                      <span className="flash-sheet-counter-val">{designCount}</span>
                      <button
                        className="flash-sheet-counter-btn"
                        onClick={() => setDesignCount(c => Math.min(12, c + 1))}
                        disabled={designCount >= 12}
                      >
                        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round"><line x1="12" y1="5" x2="12" y2="19"/><line x1="5" y1="12" x2="19" y2="12"/></svg>
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Step 3: Details */}
          {step === 3 && (
            <div className="flash-detail-step">
              <div className="flash-detail-preview">
                {preview && <img src={preview} alt="Preview" className="flash-detail-preview-img" />}
              </div>
              <div className="flash-detail-form-wrap">
                {totalDesigns > 1 && (
                  <div className="flash-detail-tabs">
                    {metas.map((_, i) => (
                      <button
                        key={i}
                        className={`flash-detail-tab${activeDesignTab === i ? ' active' : ''}`}
                        onClick={() => setActiveDesignTab(i)}
                      >
                        Design {i + 1}
                      </button>
                    ))}
                  </div>
                )}
                <div className="flash-detail-form">
                  <div className="flash-detail-field">
                    <label className="flash-detail-label">Title</label>
                    <input
                      className="flash-detail-input"
                      type="text"
                      placeholder="e.g. Traditional Rose"
                      value={metas[activeDesignTab]?.title ?? ''}
                      onChange={e => updateMeta(activeDesignTab, 'title', e.target.value)}
                    />
                  </div>
                  <div className="flash-detail-row-half">
                    <div className="flash-detail-field">
                      <label className="flash-detail-label">Price $</label>
                      <input
                        className="flash-detail-input"
                        type="number"
                        min="0"
                        step="5"
                        placeholder="200"
                        value={metas[activeDesignTab]?.price ?? ''}
                        onChange={e => updateMeta(activeDesignTab, 'price', e.target.value)}
                      />
                    </div>
                    <div className="flash-detail-field">
                      <label className="flash-detail-label">Size</label>
                      <input
                        className="flash-detail-input"
                        type="text"
                        placeholder="e.g. 3x3 in"
                        value={metas[activeDesignTab]?.size ?? ''}
                        onChange={e => updateMeta(activeDesignTab, 'size', e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="flash-detail-field">
                    <label className="flash-detail-label">Style</label>
                    <input
                      className="flash-detail-input"
                      type="text"
                      placeholder="e.g. Traditional, Neo-Trad"
                      value={metas[activeDesignTab]?.style ?? ''}
                      onChange={e => updateMeta(activeDesignTab, 'style', e.target.value)}
                    />
                  </div>
                  <div className="flash-detail-field">
                    <label className="flash-detail-label">Description</label>
                    <textarea
                      className="flash-detail-input flash-detail-textarea"
                      placeholder="Optional notes for collectors..."
                      value={metas[activeDesignTab]?.description ?? ''}
                      onChange={e => updateMeta(activeDesignTab, 'description', e.target.value)}
                    />
                  </div>
                  {totalDesigns > 1 && activeDesignTab < totalDesigns - 1 && (
                    <button
                      className="flash-detail-next-btn"
                      onClick={() => setActiveDesignTab(i => i + 1)}
                    >
                      Next Design
                      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
                    </button>
                  )}
                </div>
              </div>
            </div>
          )}

          {/* Step 4: Review */}
          {step === 4 && (
            <div className="flash-review-step">
              {metas.slice(0, totalDesigns).map((m, i) => (
                <div key={i} className="flash-review-item">
                  <div className="flash-review-thumb">
                    {preview && <img src={preview} alt={`Design ${i + 1}`} />}
                  </div>
                  <div className="flash-review-info">
                    <div className="flash-review-name">
                      {m.title || <span className="flash-review-untitled">Untitled</span>}
                      {totalDesigns > 1 && <span className="flash-review-num">Design {i + 1}</span>}
                    </div>
                    <div className="flash-review-meta">
                      {m.price && <span>${m.price}</span>}
                      {m.size && <span>{m.size}</span>}
                      {m.style && <span>{m.style}</span>}
                    </div>
                    {m.description && <p className="flash-review-desc">{m.description}</p>}
                  </div>
                  <button
                    className="flash-review-edit"
                    onClick={() => { setActiveDesignTab(i); setStep(3) }}
                  >
                    Edit
                  </button>
                </div>
              ))}
              {publishError && (
                <p className="flash-review-error">{publishError}</p>
              )}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flash-modal-footer">
          {step > 1 && (
            <button
              className="flash-modal-back"
              onClick={() => setStep(s => (s - 1) as 1 | 2 | 3 | 4)}
              disabled={publishing}
            >
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="15 18 9 12 15 6"/></svg>
              Back
            </button>
          )}
          <div style={{ flex: 1 }} />
          {step === 1 && (
            <button
              className="flash-modal-next"
              disabled={!file}
              onClick={() => setStep(2)}
            >
              Continue
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
            </button>
          )}
          {step === 2 && (
            <button
              className="flash-modal-next"
              onClick={() => { setActiveDesignTab(0); setStep(3) }}
            >
              Continue
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
            </button>
          )}
          {step === 3 && (
            <button
              className="flash-modal-next"
              onClick={() => setStep(4)}
            >
              Review
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><polyline points="9 18 15 12 9 6"/></svg>
            </button>
          )}
          {step === 4 && (
            <button
              className="flash-modal-publish"
              onClick={handlePublish}
              disabled={publishing}
            >
              {publishing ? 'Publishing...' : `Publish ${totalDesigns} Design${totalDesigns !== 1 ? 's' : ''}`}
            </button>
          )}
        </div>
      </div>
    </div>,
    document.body
  )
}

/* ── Modz Tab ── */

function IntegrationsTab({
  stripeStatus,
  emailStatus,
  loading,
  stripeOnboarding,
  user,
  googleConnected,
  googleManaged,
  pinterestConnected,
  pinterestUsername,
  onLoadIntegrations,
  onStripeOnboard,
  onGoogleDisconnect,
  onGoogleConnect,
  onPinterestDisconnect,
  onPinterestConnect,
  toast,
}: {
  stripeStatus: StripeConnectStatus | null
  emailStatus: EmailStatus | null
  loading: boolean
  stripeOnboarding: boolean
  user: { role?: string; email?: string } | null
  googleConnected: boolean
  googleManaged: boolean
  pinterestConnected: boolean
  pinterestUsername: string | null
  onLoadIntegrations: () => Promise<void>
  onStripeOnboard: () => Promise<void>
  onGoogleDisconnect: () => Promise<void>
  onGoogleConnect: () => Promise<void>
  onPinterestDisconnect: () => Promise<void>
  onPinterestConnect: () => Promise<void>
  toast: { success: (m: string) => void; error: (m: string) => void; info: (m: string) => void }
}) {
  const [googleActing, setGoogleActing] = useState(false)
  const [pinterestActing, setPinterestActing] = useState(false)
  const [loaded, setLoaded] = useState(false)

  useEffect(() => {
    if (!loaded) {
      setLoaded(true)
      onLoadIntegrations()
    }
  }, [loaded, onLoadIntegrations])

  if (loading) {
    return <div style={{ padding: '2rem', textAlign: 'center', color: 'var(--muted)' }}>Loading Modz...</div>
  }

  const integrations = [
    {
      name: 'Stripe Connect',
      desc: 'Eco Modz payment rail for deposits, payouts, and booking-linked revenue.',
      status: stripeStatus?.charges_enabled ? 'active' : stripeStatus?.connected ? 'pending' : 'disconnected',
      statusLabel: stripeStatus?.charges_enabled ? 'Active' : stripeStatus?.connected ? 'Pending Verification' : 'Not Connected',
      action: stripeStatus?.charges_enabled ? null : {
        label: stripeOnboarding ? 'Connecting...' : stripeStatus?.connected ? 'Complete Setup' : 'Connect Stripe',
        onClick: onStripeOnboard,
        disabled: stripeOnboarding,
      },
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--gold)', flexShrink: 0 }}>
          <rect x="1" y="4" width="22" height="16" rx="2" /><line x1="1" y1="10" x2="23" y2="10" />
        </svg>
      ),
    },
    {
      name: 'Ryozan Email',
      desc: 'Courier Modz address for shop-domain replies, routing, and client communication.',
      status: emailStatus?.verified ? 'active' : emailStatus?.connected ? 'pending' : 'disconnected',
      statusLabel: emailStatus?.verified
        ? `${emailStatus.handle}@ryozan.app`
        : emailStatus?.connected ? 'Pending Verification' : 'Not Set Up',
      action: emailStatus?.verified ? null : {
        label: 'Set Up Email',
        onClick: () => toast.info('Complete the Digital Address quest to claim your handle'),
        disabled: false,
      },
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--gold)', flexShrink: 0 }}>
          <rect x="2" y="4" width="20" height="16" rx="2" /><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
        </svg>
      ),
    },
    {
      name: 'Google Account',
      desc: 'Realm Modz connection for calendar sync, one-tap login, and document storage.',
      status: googleConnected ? 'active' : 'disconnected',
      statusLabel: googleManaged ? 'Managed by Ryozan booking' : googleConnected ? (user?.email || 'Connected') : 'Not Connected',
      action: googleManaged ? null : googleConnected
        ? {
            label: googleActing ? 'Disconnecting...' : 'Disconnect',
            onClick: async () => {
              setGoogleActing(true)
              try { await onGoogleDisconnect() } finally { setGoogleActing(false) }
            },
            disabled: googleActing,
            danger: true,
          }
        : {
            label: googleActing ? 'Connecting...' : 'Connect Google',
            onClick: async () => {
              setGoogleActing(true)
              try { await onGoogleConnect() } finally { setGoogleActing(false) }
            },
            disabled: googleActing,
          },
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--gold)', flexShrink: 0 }}>
          <circle cx="12" cy="12" r="3" /><path d="M12 1v2m0 18v2M4.22 4.22l1.42 1.42m12.72 12.72 1.42 1.42M1 12h2m18 0h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42" />
        </svg>
      ),
    },
    {
      name: 'Pinterest',
      desc: 'Reference boards, flash publishing, and board analytics through your Pinterest account.',
      status: pinterestConnected ? 'active' : 'disconnected',
      statusLabel: pinterestConnected ? (pinterestUsername ? `@${pinterestUsername}` : 'Connected') : 'Not Connected',
      action: pinterestConnected
        ? {
            label: pinterestActing ? 'Disconnecting...' : 'Disconnect',
            onClick: async () => {
              setPinterestActing(true)
              try { await onPinterestDisconnect() } finally { setPinterestActing(false) }
            },
            disabled: pinterestActing,
            danger: true,
            testid: 'pinterest-disconnect',
          }
        : {
            label: pinterestActing ? 'Connecting...' : 'Connect Pinterest',
            onClick: onPinterestConnect,
            disabled: pinterestActing,
            testid: 'pinterest-connect',
          },
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--gold)', flexShrink: 0 }}>
          <circle cx="12" cy="12" r="10" /><path d="M9 18c1-3 1.5-5 2-7.5" /><path d="M9.5 9.5a2.5 2.5 0 1 1 4.5 1.5c-.5 1.5-2.5 2-3.5 1" />
        </svg>
      ),
    },
    {
      name: 'Venue.ink',
      desc: 'Realm Modz bridge for artists who route booking flow through Venue.ink.',
      status: 'available',
      statusLabel: 'Available',
      action: {
        label: 'Configure in Booking tab',
        onClick: () => toast.info('Switch to the Booking tab to set up Venue.ink'),
        disabled: false,
      },
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--gold)', flexShrink: 0 }}>
          <rect x="3" y="4" width="18" height="18" rx="2" /><line x1="16" y1="2" x2="16" y2="6" /><line x1="8" y1="2" x2="8" y2="6" /><line x1="3" y1="10" x2="21" y2="10" />
        </svg>
      ),
    },
    {
      name: 'Plaid',
      desc: 'Eco Modz bank telemetry for deposits, payouts, and shop revenue visibility.',
      status: 'coming-soon',
      statusLabel: 'Coming Soon',
      action: null,
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--muted)', flexShrink: 0 }}>
          <rect x="2" y="5" width="20" height="14" rx="2" /><path d="M2 10h20" /><path d="M6 15h2m4 0h2" />
        </svg>
      ),
    },
    {
      name: 'Mailchimp',
      desc: 'Herald/Courier Modz campaign bridge for promos, flash announcements, and client segments.',
      status: 'coming-soon',
      statusLabel: 'Coming Soon',
      action: null,
      icon: (
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" style={{ color: 'var(--muted)', flexShrink: 0 }}>
          <rect x="2" y="4" width="20" height="16" rx="2" /><path d="m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7" />
        </svg>
      ),
    },
  ]

  const statusColors: Record<string, string> = {
    active: '#48bb78',
    pending: '#ecc94b',
    disconnected: '#e53e3e',
    available: 'var(--gold)',
    'coming-soon': 'var(--muted)',
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: '1rem' }}>
      {integrations.map((int) => (
        <div
          key={int.name}
          style={{
            display: 'flex',
            alignItems: 'center',
            gap: '1rem',
            padding: '1rem 1.25rem',
            background: 'rgba(255,255,255,0.02)',
            border: '1px solid rgba(255,255,255,0.06)',
            borderRadius: '10px',
            opacity: int.status === 'coming-soon' ? 0.5 : 1,
          }}
        >
          {int.icon}
          <div style={{ flex: 1, minWidth: 0 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', marginBottom: '0.25rem' }}>
              <span style={{ fontWeight: 600, color: 'var(--text)', fontSize: '0.95rem' }}>{int.name}</span>
              <span style={{
                fontSize: '0.7rem',
                fontWeight: 600,
                textTransform: 'uppercase',
                letterSpacing: '0.05em',
                color: statusColors[int.status] || 'var(--muted)',
              }}>
                {int.statusLabel}
              </span>
            </div>
            <p style={{ color: 'var(--muted)', fontSize: '0.8rem', margin: 0, lineHeight: 1.4 }}>
              {int.desc}
            </p>
          </div>
          {int.action && (
            <button
              className="action-btn"
              data-testid={'testid' in int.action ? int.action.testid : undefined}
              onClick={int.action.onClick}
              disabled={int.action.disabled}
              style={{
                flexShrink: 0,
                fontSize: '0.8rem',
                ...('danger' in int.action && int.action.danger ? { color: '#f87171', borderColor: 'rgba(248,113,113,0.3)' } : {}),
              }}
            >
              {int.action.label}
            </button>
          )}
          {!int.action && int.status === 'active' && (
            <span style={{
              width: '8px',
              height: '8px',
              borderRadius: '50%',
              background: '#48bb78',
              flexShrink: 0,
            }} />
          )}
        </div>
      ))}
    </div>
  )
}

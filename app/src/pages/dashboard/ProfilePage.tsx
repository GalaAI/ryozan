/**
 * ProfilePage: User profile editor (collector role only).
 *
 * Staff roles (artist/admin/founder) redirect to ArtistProfileEditor
 * which has an Account tab covering the same settings.
 *
 * Allows updating: name, avatar, marketing opt-in.
 * Uses PATCH /api/v1/users/me + POST /api/v1/users/me/avatar
 */

import { useState, useRef, type FormEvent } from 'react'
import { Navigate } from 'react-router-dom'
import { useAuthStore } from '../../stores/auth'
import { users } from '../../lib/api'
import { useToast } from '../../components/Toast'

const STAFF_ROLES = ['assistant', 'artist', 'admin', 'founder']

export default function ProfilePage() {
  const { user, fetchMe } = useAuthStore()
  const effectiveRole = useAuthStore((s) => s.effectiveRole)
  const toast = useToast()
  const [name, setName] = useState(user?.name || '')
  const [marketingOptIn, setMarketingOptIn] = useState(user?.marketing_opt_in || false)
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  // Avatar state
  const [avatarUrl, setAvatarUrl] = useState<string | null>(user?.avatar_url ?? null)
  const [isUploadingAvatar, setIsUploadingAvatar] = useState(false)
  const [avatarMsg, setAvatarMsg] = useState<{ type: 'ok' | 'err'; text: string } | null>(null)
  const avatarInputRef = useRef<HTMLInputElement>(null)

  // Staff roles go to Portfolio > Account tab (respects View As mode)
  if (user && STAFF_ROLES.includes(effectiveRole)) {
    return <Navigate to="/dashboard/artist-profile" replace />
  }

  const handleAvatarUpload = async (file: File) => {
    setIsUploadingAvatar(true)
    setAvatarMsg(null)
    try {
      const updated = await users.uploadAvatar(file)
      setAvatarUrl(updated.avatar_url)
      // Update auth store so topbar/sidebar reflect the change immediately
      const authUser = useAuthStore.getState().user
      if (authUser) {
        useAuthStore.setState({ user: { ...authUser, avatar_url: updated.avatar_url } })
      }
      setAvatarMsg({ type: 'ok', text: 'Profile picture updated.' })
      setTimeout(() => setAvatarMsg(null), 3000)
    } catch (err: unknown) {
      setAvatarMsg({ type: 'err', text: err instanceof Error ? err.message : 'Avatar upload failed.' })
    } finally {
      setIsUploadingAvatar(false)
    }
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setSaving(true)
    setSaved(false)
    try {
      await users.updateProfile({
        name,
        marketing_opt_in: marketingOptIn,
      })
      await fetchMe()
      setSaved(true)
      setTimeout(() => setSaved(false), 3000)
    } catch {
      toast.error('Failed to update profile')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="dashboard-panel">
      <header className="panel-header">
        <h1 className="panel-title">Profile</h1>
      </header>
      <form onSubmit={handleSubmit} className="profile-form" style={{ padding: '2rem', maxWidth: '500px', margin: '0 auto' }}>
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
                {(user?.name || '?').charAt(0).toUpperCase()}
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

        {avatarMsg && (
          <div
            className={avatarMsg.type === 'ok' ? 'form-success-msg' : 'form-error-msg'}
            style={{ marginBottom: '1rem', padding: '0.5rem 0.75rem', borderRadius: '6px', fontSize: '0.85rem' }}
          >
            {avatarMsg.text}
          </div>
        )}

        <div className="form-group">
          <label className="form-label">Email</label>
          <input
            type="email"
            value={user?.email || ''}
            disabled
            className="form-input disabled"
          />
        </div>

        <div className="form-group">
          <label className="form-label">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="form-input"
          />
        </div>

        <div className="form-group">
          <label className="form-label">Role</label>
          <input
            type="text"
            value={user?.role || ''}
            disabled
            className="form-input disabled"
          />
        </div>

        <div className="form-group">
          <label className="form-checkbox-label">
            <input
              type="checkbox"
              checked={marketingOptIn}
              onChange={(e) => setMarketingOptIn(e.target.checked)}
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
          type="submit"
          className="btn-gold"
          disabled={saving}
        >
          {saving ? 'Saving...' : saved ? 'Saved!' : 'Update Profile'}
        </button>
      </form>
    </div>
  )
}

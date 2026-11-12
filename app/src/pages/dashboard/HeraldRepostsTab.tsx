/**
 * Herald Reposts: the shop artists' new posts, drafted for the shop account.
 *
 * Ark owns the drafts and the Social Modz publish; this tab is the portal's
 * checkpoint over the same records. Nothing posts until a founder or admin
 * approves it, and the publish tags the artist and geotags the shop.
 */

import { useCallback, useEffect, useMemo, useState } from 'react'
import { socialReposts, type RepostDraft, type RepostStatus } from '../../lib/api'

type Filter = RepostStatus | 'all'

const FILTERS: Filter[] = ['pending', 'approved', 'failed', 'published', 'rejected', 'all']

function tagLine(tags: string[]) {
  return tags.map((tag) => `#${tag}`).join(' ')
}

function toLocalInput(iso?: string) {
  if (!iso) return ''
  const date = new Date(iso)
  return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 16)
}

function errorText(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function RepostCard({
  draft,
  busy,
  onChanged,
  onNotice,
}: {
  draft: RepostDraft
  busy: boolean
  onChanged: () => Promise<void>
  onNotice: (message: string) => void
}) {
  const [caption, setCaption] = useState(draft.caption)
  const [hashtags, setHashtags] = useState(tagLine(draft.hashtags))
  const [schedule, setSchedule] = useState(toLocalInput(draft.scheduledFor))
  const [working, setWorking] = useState(false)

  // A refreshed draft replaces the local edits (adjusted during render).
  const [shownDraft, setShownDraft] = useState(draft)
  if (shownDraft !== draft) {
    setShownDraft(draft)
    setCaption(draft.caption)
    setHashtags(tagLine(draft.hashtags))
    setSchedule(toLocalInput(draft.scheduledFor))
  }

  const locked = draft.status === 'published' || draft.status === 'rejected'
  const edited =
    caption !== draft.caption || hashtags !== tagLine(draft.hashtags) || schedule !== toLocalInput(draft.scheduledFor)
  const preview = draft.media[0]
  const disabled = busy || working

  async function saveEdits() {
    if (!edited) return
    await socialReposts.edit(draft.id, {
      caption,
      hashtags: hashtags
        .split(/[\s,]+/)
        .map((tag) => tag.replace(/^#/, ''))
        .filter(Boolean),
      ...(schedule ? { scheduled_for: new Date(schedule).toISOString() } : { clear_schedule: true }),
    })
  }

  async function run(action: 'save' | 'approve' | 'reject' | 'publish') {
    setWorking(true)
    try {
      await saveEdits()
      if (action === 'approve' || action === 'reject') await socialReposts.review(draft.id, action)
      if (action === 'publish') {
        await socialReposts.publish(draft.id)
        onNotice(`Published @${draft.artist.handle}'s post to the shop account.`)
      }
    } catch (error) {
      onNotice(errorText(error))
    } finally {
      setWorking(false)
      await onChanged()
    }
  }

  return (
    <article className="herald-repost-card">
      <div className="herald-repost-media">
        {preview?.type === 'VIDEO' ? (
          <video src={preview.url} poster={draft.source.thumbnailUrl} controls muted playsInline preload="metadata" />
        ) : (
          <img src={preview?.url} alt={`Tattoo by ${draft.artist.name}`} loading="lazy" />
        )}
        {draft.media.length > 1 && <span className="herald-repost-count">x{draft.media.length}</span>}
      </div>
      <div className="herald-repost-body">
        <div className="herald-list-head">
          <div>
            <strong>{draft.artist.name}</strong>
            <small className="herald-repost-handle">@{draft.artist.handle}</small>
          </div>
          <span className={`herald-repost-status is-${draft.status}`}>{draft.status}</span>
        </div>
        <p className="herald-publisher-note">
          {draft.source.postedAt ? `Posted ${new Date(draft.source.postedAt).toLocaleString()}` : 'Post time unknown'}
          {draft.source.permalink && (
            <>
              {' · '}
              <a className="herald-link" href={draft.source.permalink} target="_blank" rel="noreferrer">
                Original post
              </a>
            </>
          )}
        </p>
        <div className="herald-publisher-panel herald-repost-fields">
          <label>
            <span>Caption</span>
            <textarea value={caption} onChange={(e) => setCaption(e.target.value)} rows={5} disabled={locked || disabled} />
          </label>
          <label>
            <span>Hashtags</span>
            <input value={hashtags} onChange={(e) => setHashtags(e.target.value)} disabled={locked || disabled} />
          </label>
          <label>
            <span>Publish at (optional)</span>
            <input
              type="datetime-local"
              value={schedule}
              onChange={(e) => setSchedule(e.target.value)}
              disabled={locked || disabled}
            />
          </label>
          <p className="herald-publisher-note">
            {caption === draft.draftCaption ? 'First draft. ' : 'Edited from the first draft. '}
            The artist tag and the shop location are added when it publishes.
          </p>
        </div>
        {draft.publish?.error && <div className="herald-result fail"><span>{draft.publish.error}</span></div>}
        {draft.publish?.mediaId && (
          <p className="herald-publisher-note">Published as {draft.publish.mediaId} by {draft.publish.by}</p>
        )}
        {draft.review && (
          <p className="herald-publisher-note">
            {draft.review.decision === 'approve' ? 'Approved' : 'Rejected'} by {draft.review.by}
          </p>
        )}
        {!locked && (
          <div className="herald-repost-actions">
            {edited && (
              <button type="button" className="herald-secondary-btn" disabled={disabled} onClick={() => run('save')}>
                Save edits
              </button>
            )}
            {draft.status === 'pending' && (
              <>
                <button type="button" className="herald-primary-btn" disabled={disabled} onClick={() => run('approve')}>
                  Approve
                </button>
                <button type="button" className="herald-secondary-btn" disabled={disabled} onClick={() => run('reject')}>
                  Reject
                </button>
              </>
            )}
            {(draft.status === 'approved' || draft.status === 'failed') && (
              <button type="button" className="herald-primary-btn" disabled={disabled} onClick={() => run('publish')}>
                {working ? 'Publishing...' : draft.status === 'failed' ? 'Retry publish' : 'Publish now'}
              </button>
            )}
          </div>
        )}
      </div>
    </article>
  )
}

export default function HeraldRepostsTab() {
  const [drafts, setDrafts] = useState<RepostDraft[] | null>(null)
  const [filter, setFilter] = useState<Filter>('pending')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [geotag, setGeotag] = useState<string | null>(null)

  const load = useCallback(() => socialReposts.list()
    .then((result) => setDrafts(result.drafts))
    .catch((error: unknown) => {
      setNotice(errorText(error))
      setDrafts([])
    }), [])

  useEffect(() => {
    void load()
  }, [load])

  const counts = useMemo(() => {
    const result: Record<Filter, number> = {
      pending: 0,
      approved: 0,
      failed: 0,
      published: 0,
      rejected: 0,
      all: drafts?.length ?? 0,
    }
    for (const draft of drafts ?? []) result[draft.status] += 1
    return result
  }, [drafts])

  const visible = (drafts ?? []).filter((draft) => filter === 'all' || draft.status === filter)

  async function scan() {
    setBusy(true)
    setNotice(null)
    try {
      const result = await socialReposts.scan()
      const unread = result.errors.map((item) => `@${item.handle}`).join(', ')
      setNotice(
        `${result.created.length} new drafts, ${result.skipped.length} posts without a usable file${
          unread ? `, could not read ${unread}` : ''
        }.`,
      )
      await load()
    } catch (error) {
      setNotice(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  async function checkGeotag() {
    setBusy(true)
    try {
      const { location } = await socialReposts.location()
      setGeotag(
        location.locationId
          ? `Geotag ready: ${location.name ?? 'shop'} (${location.singleLineAddress ?? location.locationId})`
          : 'The shop Page has no geotag location, so posts publish without one.',
      )
    } catch (error) {
      setGeotag(errorText(error))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="herald-tab-body herald-publisher">
      <div className="herald-section-intro herald-repost-intro">
        <div>
          <h2>Artist Reposts</h2>
          <p>
            New posts from the shop artists, drafted for the studio account with the artist tagged and the shop
            geotagged. Nothing posts until you approve it.
          </p>
        </div>
        <div className="herald-repost-actions">
          <button type="button" className="herald-secondary-btn" disabled={busy} onClick={checkGeotag}>
            Check geotag
          </button>
          <button type="button" className="herald-primary-btn" disabled={busy} onClick={scan}>
            {busy ? 'Working...' : 'Scan artists'}
          </button>
        </div>
      </div>
      {geotag && <div className="herald-publish-status">{geotag}</div>}
      {notice && <div className="herald-publish-status">{notice}</div>}
      <div className="herald-repost-filters" role="group" aria-label="Draft status">
        {FILTERS.map((item) => (
          <button
            key={item}
            type="button"
            className={`herald-tab-btn${item === filter ? ' active' : ''}`}
            aria-pressed={item === filter}
            onClick={() => setFilter(item)}
          >
            <span>{item}</span> <small>{counts[item]}</small>
          </button>
        ))}
      </div>
      {drafts === null ? (
        <p className="herald-publisher-note">Loading drafts...</p>
      ) : visible.length === 0 ? (
        <p className="herald-publisher-note">
          No {filter === 'all' ? '' : `${filter} `}drafts. Scan the artists to look for new posts.
        </p>
      ) : (
        <div className="herald-repost-grid">
          {visible.map((draft) => (
            <RepostCard key={draft.id} draft={draft} busy={busy} onChanged={load} onNotice={setNotice} />
          ))}
        </div>
      )}
    </div>
  )
}

import { useEffect, useState, type CSSProperties } from 'react'
import type { ResponseTemplate } from '../../lib/api'

// CourierLayout owns the existing full template editor and messaging transport.
// This desktop action only selects and previews that shared library in the native
// panel; the page retains the recipient draft and the separate explicit send.
export interface QuickRepliesWorkspaceProps {
  loadTemplates: () => Promise<ResponseTemplate[]>
  recipient: string | null
  existingDraft: string
  disabled: boolean
  onUse: (text: string) => void
}

const control: CSSProperties = { background: 'var(--surface)', color: 'var(--ink)', border: '1px solid var(--line-2)', borderRadius: 8, padding: 10, font: 'inherit' }

export default function QuickRepliesWorkspace({ loadTemplates, recipient, existingDraft, disabled, onUse }: QuickRepliesWorkspaceProps) {
  const [revision, setRevision] = useState(0)
  const [templates, setTemplates] = useState<ResponseTemplate[] | null>(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string | null>(null)
  const [preview, setPreview] = useState('')
  const [notice, setNotice] = useState('')

  useEffect(() => {
    let active = true
    void loadTemplates().then(result => { if (active) setTemplates(result) }).catch(failure => {
      if (active) setError(failure instanceof Error ? failure.message : 'Saved replies could not be loaded.')
    })
    return () => { active = false }
  }, [loadTemplates, revision])

  return <section aria-label="Quick replies workspace" data-quick-replies-workspace style={{ display: 'grid', gap: 12, minWidth: 0, color: 'var(--ink)' }}>
    <style>{`[data-quick-replies-workspace] button:focus-visible,[data-quick-replies-workspace] textarea:focus-visible{outline:2px solid var(--ag-accent);outline-offset:2px}[data-quick-replies-workspace] button:disabled{opacity:.45;cursor:not-allowed}`}</style>
    <p>Shared shop replies. Preview and edit locally before placing one in Courier. Nothing is sent by this action.</p>
    <p>{recipient ? `Recipient: ${recipient}` : 'Select a conversation in Courier before using a reply.'}</p>
    {!templates && !error && <p role="status">Loading saved replies…</p>}
    {error && <p role="alert">{error}</p>}
    {(templates || error) && <button type="button" style={control} onClick={() => {
      setTemplates(null); setError(''); setSelected(null); setPreview(''); setNotice(''); setRevision(value => value + 1)
    }}>{error ? 'Retry saved replies' : 'Refresh saved replies'}</button>}
    {templates?.length === 0 && <p role="status">No saved replies are available.</p>}
    {!!templates?.length && <div role="group" aria-label="Saved replies" style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit,minmax(min(100%,220px),1fr))', gap: 10 }}>
      {templates.map(template => <button type="button" key={template.id} data-response-template={template.id} aria-pressed={selected === template.id} style={{ ...control, textAlign: 'left', overflowWrap: 'anywhere', borderColor: selected === template.id ? 'var(--ag-accent)' : 'var(--line-2)' }} onClick={() => { setSelected(template.id); setPreview(template.body); setNotice('') }}>{template.title || 'Untitled reply'}</button>)}
    </div>}
    {selected && <>
      <label style={{ display: 'grid', gap: 8 }}>Reply preview<textarea aria-label="Reply preview" rows={6} style={{ ...control, width: '100%', boxSizing: 'border-box', resize: 'vertical' }} value={preview} onChange={event => { setPreview(event.target.value); setNotice('') }} /></label>
      <p>Editing this preview does not change the saved template.{existingDraft.trim() ? ' Using it will replace your current unsent draft.' : ''}</p>
      <button type="button" style={control} disabled={disabled || !recipient || !preview.trim()} onClick={() => {
        try { onUse(preview); setNotice('Draft prepared. Close Quick replies to review it in Courier and send when ready. Nothing has been sent.') }
        catch (failure) { setNotice(failure instanceof Error ? failure.message : 'The draft could not be prepared.') }
      }}>{existingDraft.trim() ? 'Replace current draft' : 'Use in draft'}</button>
    </>}
    {notice && <p role="status">{notice}</p>}
  </section>
}

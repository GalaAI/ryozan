/**
 * Ryozan shop surface for Apex-owned Bella and Gala evolution.
 *
 * This extends the existing Gala API and upload contracts instead of creating
 * a second trainer. Apex owns profile versions and evidence, while this panel
 * supplies founder conversation, visual controls, media references, and the
 * explicit promotion into Portal chat and an optional Bella phone handoff.
 */
import { useCallback, useEffect, useState, type ChangeEvent } from 'react'
import {
  gala,
  galaUpload,
  type ApexAgentId,
  type ApexAgentProfile,
} from '../lib/api'
import '../styles/agent-evolution-studio.css'

const traitControls: Array<{
  key: 'curiosity' | 'warmth' | 'initiative' | 'precision' | 'brevity'
  label: string
}> = [
  { key: 'curiosity', label: 'Curiosity' },
  { key: 'warmth', label: 'Warmth' },
  { key: 'initiative', label: 'Initiative' },
  { key: 'precision', label: 'Precision' },
  { key: 'brevity', label: 'Brevity' },
]

export default function AgentEvolutionStudio({ onBack }: { onBack: () => void }) {
  const [agentId, setAgentId] = useState<ApexAgentId>('bella')
  const [profile, setProfile] = useState<ApexAgentProfile | null>(null)
  const [state, setState] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState('')
  const [message, setMessage] = useState('')
  const [attachment, setAttachment] = useState<File | null>(null)
  const [evaluationArtifactId, setEvaluationArtifactId] = useState('')

  // Fetch only; the loading reset happens where the request is triggered
  // (agent switch, retry) so the effect never sets state synchronously.
  const fetchProfile = useCallback(() => {
    gala.evolution.get(agentId)
      .then((result) => {
        setProfile(result.profile ?? null)
        setState(result.profile ? 'ready' : 'failed')
      })
      .catch(() => setState('failed'))
  }, [agentId])

  const load = () => {
    setState('loading')
    setEvaluationArtifactId('')
    fetchProfile()
  }

  useEffect(() => fetchProfile(), [fetchProfile])

  const run = async (action: () => Promise<{ profile?: ApexAgentProfile; response?: string }>, success: string) => {
    setBusy(true)
    setNotice('')
    try {
      const result = await action()
      if (result.profile) setProfile(result.profile)
      setNotice(result.response || success)
      return true
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Apex could not complete that action.')
      return false
    } finally {
      setBusy(false)
    }
  }

  const updateTrait = (key: keyof ApexAgentProfile['traits'], value: number | boolean) => {
    setEvaluationArtifactId('')
    setProfile((current) => current
      ? { ...current, traits: { ...current.traits, [key]: value } }
      : current)
  }

  const trainThroughDialogue = async () => {
    if (!profile || !message.trim()) return
    setBusy(true)
    setNotice('')
    setEvaluationArtifactId('')
    try {
      const inputRefs: string[] = []
      if (attachment) {
        const upload = await galaUpload({
          file: attachment,
          category: 'other',
          note: `Apex behavior evidence for ${profile.displayName}`,
        })
        if (upload.url) inputRefs.push(upload.url)
      }
      const result = await gala.evolution.dialogue(agentId, {
        message: message.trim(),
        history: [],
        input_refs: inputRefs,
      })
      if (result.profile) setProfile(result.profile)
      setNotice(result.response || `${profile.displayName} candidate updated.`)
      setMessage('')
      setAttachment(null)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Training dialogue failed.')
    } finally {
      setBusy(false)
    }
  }

  const runGauntlet = async () => {
    setBusy(true)
    setNotice('')
    setEvaluationArtifactId('')
    try {
      const result = await gala.evolution.runGauntlet(agentId)
      if (result.profile) setProfile(result.profile)
      if (result.artifact?.id) setEvaluationArtifactId(result.artifact.id)
      setNotice(result.result?.summary || `${profile?.displayName ?? agentId} profile checks completed.`)
    } catch (error) {
      setNotice(error instanceof Error ? error.message : 'Apex profile checks failed.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <section className="ryozan-agent-evolution" aria-label="Bella and Gala agent settings">
      <header>
        <button type="button" onClick={onBack} aria-label="Back to Hub">← Hub</button>
        <div><span>Agent settings</span><h2>Bella and Gala</h2></div>
        <select value={agentId} onChange={(event) => {
          setEvaluationArtifactId('')
          setState('loading')
          setAgentId(event.target.value as ApexAgentId)
        }}>
          <option value="bella">Bella · Shop</option>
          <option value="gala">Gala · Personal</option>
        </select>
      </header>

      {state === 'loading' ? <p className="ryozan-agent-evolution__empty">Loading the current Apex profile.</p> : null}
      {state === 'failed' ? <button type="button" className="ryozan-agent-evolution__empty" onClick={load}>Apex profile unavailable · retry</button> : null}

      {state === 'ready' && profile ? (
        <>
          <div className="ryozan-agent-evolution__status">
            <strong>{profile.displayName}</strong>
            <span>{profile.status.replaceAll('_', ' ')}</span>
            <small>v{profile.version} · revision {profile.revision}</small>
            <small>Portal profile {profile.status === 'promoted' ? 'live' : 'not live'}</small>
            {agentId === 'bella' ? <small>Phone profile {profile.providerBinding.syncStatus.replaceAll('_', ' ')}</small> : null}
          </div>

          <div className="ryozan-agent-evolution__traits">
            {traitControls.map((trait) => (
              <label key={trait.key}>
                <span>{trait.label}</span><output>{profile.traits[trait.key]}</output>
                <input type="range" min="0" max="100" value={profile.traits[trait.key]} onChange={(event) => updateTrait(trait.key, Number(event.target.value))} />
              </label>
            ))}
            <label>
              <span>Questions per turn</span><output>{profile.traits.questionBudget}</output>
              <input type="range" min="0" max="5" value={profile.traits.questionBudget} onChange={(event) => updateTrait('questionBudget', Number(event.target.value))} />
            </label>
            <label className="is-check">
              <input type="checkbox" checked={profile.traits.oneQuestionAtATime} onChange={(event) => updateTrait('oneQuestionAtATime', event.target.checked)} />
              <span>One question at a time</span>
            </label>
          </div>

          <div className="ryozan-agent-evolution__actions">
            <button
              type="button"
              disabled={busy}
              onClick={() => void run(
                () => {
                  setEvaluationArtifactId('')
                  return gala.evolution.saveProfile(agentId, profile)
                },
                `${profile.displayName} visual settings saved as a candidate.`,
              )}
            >Save visual settings</button>
            <button
              type="button"
              disabled={busy || profile.status === 'promoted'}
              onClick={() => void runGauntlet()}
            >Run profile checks</button>
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setBusy(true)
                setNotice('')
                void gala.evolution.recoverLearningCaptures()
                  .then((result) => {
                    setNotice(
                      result.requeued
                        ? `${result.requeued} failed learning captures were requeued and replayed.`
                        : 'No source-registration failures need recovery.',
                    )
                  })
                  .catch((error) => {
                    setNotice(error instanceof Error ? error.message : 'Learning capture recovery failed.')
                  })
                  .finally(() => setBusy(false))
              }}
            >Recover learning captures</button>
          </div>

          <div className="ryozan-agent-evolution__workbench">
            <article>
              <span>Conversational training</span>
              <textarea value={message} onChange={(event) => setMessage(event.target.value)} placeholder={`Tell Apex how ${profile.displayName} should act. Say “remember that …” for durable memory.`} />
              <label className="ryozan-agent-evolution__file">
                <span>{attachment ? attachment.name : 'Attach audio, image, video, or document'}</span>
                <input type="file" onChange={(event: ChangeEvent<HTMLInputElement>) => setAttachment(event.target.files?.[0] ?? null)} />
              </label>
              <button type="button" disabled={busy || !message.trim()} onClick={() => void trainThroughDialogue()}>Train through conversation</button>
            </article>

            <article>
              <span>Independent evaluation</span>
              <p>Apex runs the frozen rubric through an independent Model Gateway evaluator and stores the result as a digest-bound Nexus artifact.</p>
              <input value={evaluationArtifactId} readOnly placeholder="Run profile checks to create the Nexus evaluation artifact" />
              <button
                type="button"
                disabled={busy || profile.status !== 'gauntlet_passed' || !evaluationArtifactId}
                onClick={() => void run(
                  () => gala.evolution.recordEvaluation(agentId, evaluationArtifactId),
                  `${profile.displayName} evaluation recorded.`,
                )}
              >Record verified pass</button>
              <button
                type="button"
                className="is-live"
                disabled={busy || profile.status !== 'evaluation_passed'}
                onClick={() => void run(
                  () => gala.evolution.promote(agentId),
                  `${profile.displayName} was promoted into live Portal chat.`,
                )}
              >Promote to Portal</button>
              {agentId === 'bella' ? (
                <button
                  type="button"
                  disabled={busy || profile.status !== 'promoted'}
                  onClick={() => void run(
                    () => gala.evolution.promoteAndApply(agentId),
                    `${profile.displayName} was applied to the live phone agent.`,
                  )}
                >Apply to phone</button>
              ) : null}
            </article>
          </div>

          {notice ? <p className="ryozan-agent-evolution__notice" role="status">{notice}</p> : null}
        </>
      ) : null}
    </section>
  )
}

/**
 * ArtistScroll - Complete compliance document package for artists.
 *
 * 6 tabbed sections covering all SC DHEC 61-42 requirements,
 * employment records, and safety training.
 *
 * Sections:
 *   1. Employment Application
 *   2. Artist Agreement
 *   3. Bloodborne Pathogen Acknowledgment
 *   4. Fire Safety & Emergency Procedures
 *   5. SC DHEC 61-42 Summary
 *   6. Acknowledgment & Signature
 */

import { useState, useRef } from 'react'
import SignaturePad from './SignaturePad'
import { submitArtistScroll } from '../lib/api'
import { useStudioSettings } from '../hooks/useStudioSettings'
import '../styles/artist-scroll.css'

/* ── Section definitions ── */

const SCROLL_SECTIONS = [
  { id: 'application', label: 'Application', num: 'I' },
  { id: 'agreement', label: 'Agreement', num: 'II' },
  { id: 'bloodborne', label: 'BBP Training', num: 'III' },
  { id: 'fire', label: 'Fire Safety', num: 'IV' },
  { id: 'dhec', label: 'DHEC 61-42', num: 'V' },
  { id: 'signature', label: 'Sign-Off', num: 'VI' },
] as const

type ScrollSection = typeof SCROLL_SECTIONS[number]['id']
const ARTIST_SCROLL_TERMS_VERSION = 'artist-scroll-2026-08-10-v1'

/* ── Component ── */

interface ArtistScrollProps {
  artistName?: string
  artistSlug?: string
  onSubmitted?: () => void
  layout?: 'tabbed' | 'continuous'
  preview?: boolean
}

export default function ArtistScroll({ artistName = 'Artist', onSubmitted, layout = 'tabbed', preview = false }: ArtistScrollProps) {
  const continuous = layout === 'continuous'
  const { settings: studioSettings } = useStudioSettings()
  const [activeSection, setActiveSection] = useState<ScrollSection>('application')
  const [acceptedSections, setAcceptedSections] = useState<Partial<Record<ScrollSection, boolean>>>({})
  const [acceptanceNames, setAcceptanceNames] = useState<Partial<Record<ScrollSection, string>>>({})
  const [artistSig, setArtistSig] = useState('')
  const [fields, setFields] = useState<Record<string, string>>({})
  const [bbpCert, setBbpCert] = useState<File | null>(null)
  const [bbpCertPreview, setBbpCertPreview] = useState<string | null>(null)
  const [firstAidCert, setFirstAidCert] = useState<File | null>(null)
  const [firstAidPreview, setFirstAidPreview] = useState<string | null>(null)
  const [cprCert, setCprCert] = useState<File | null>(null)
  const [cprPreview, setCprPreview] = useState<string | null>(null)
  const [govtId, setGovtId] = useState<File | null>(null)
  const [govtIdPreview, setGovtIdPreview] = useState<string | null>(null)
  const [taxOptIn, setTaxOptIn] = useState<boolean | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [submitted, setSubmitted] = useState(false)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const certInputRef = useRef<HTMLInputElement>(null)
  const firstAidRef = useRef<HTMLInputElement>(null)
  const cprRef = useRef<HTMLInputElement>(null)
  const govtIdRef = useRef<HTMLInputElement>(null)

  const field = (key: string) => ({
    className: 'scroll-field-input',
    value: fields[key] ?? '',
    onChange: (e: React.ChangeEvent<HTMLInputElement>) =>
      setFields(prev => ({ ...prev, [key]: e.target.value })),
  })

  function makeCertHandler(
    setter: (f: File | null) => void,
    previewSetter: (url: string | null) => void,
  ) {
    return (e: React.ChangeEvent<HTMLInputElement>) => {
      const f = e.target.files?.[0] ?? null
      setter(f)
      previewSetter(f ? URL.createObjectURL(f) : null)
    }
  }

  const handleCertFile = makeCertHandler(setBbpCert, setBbpCertPreview)
  const handleFirstAidFile = makeCertHandler(setFirstAidCert, setFirstAidPreview)
  const handleCprFile = makeCertHandler(setCprCert, setCprPreview)
  const handleGovtIdFile = makeCertHandler(setGovtId, setGovtIdPreview)

  const handleSubmit = async () => {
    if (preview) {
      setSubmitError('Preview complete. Nothing was submitted or saved.')
      return
    }
    if (!artistSig) return
    if (!bbpCert) { setSubmitError('BBP training certificate is required.'); return }
    if (!firstAidCert) { setSubmitError('First Aid certificate is required.'); return }
    if (!cprCert) { setSubmitError('CPR certificate is required.'); return }
    if (!govtId) { setSubmitError('Government-issued ID is required.'); return }
    if (taxOptIn === null) { setSubmitError('Tax status selection is required.'); return }
    const acceptedStages = SCROLL_SECTIONS.filter(section => section.id !== 'signature')
    if (acceptedStages.some(section => !acceptedSections[section.id] || !acceptanceNames[section.id]?.trim())) {
      setSubmitError('Accept every Artist Scroll section before signing.'); return
    }
    setSubmitting(true)
    setSubmitError(null)
    try {
      const sectionAcceptances = Object.fromEntries(
        acceptedStages.map(section => [section.id, {
          label: section.label,
          accepted: true,
          legal_name: acceptanceNames[section.id]?.trim(),
        }]),
      )
      const allFields = {
        ...fields,
        tax_opt_in: String(taxOptIn),
        terms_version: ARTIST_SCROLL_TERMS_VERSION,
        section_acceptances: sectionAcceptances,
      }
      await submitArtistScroll({
        artistSignature: artistSig,
        fieldsJson: JSON.stringify(allFields),
        bbpCert,
        firstAidCert,
        cprCert,
        govtId,
      })
      setSubmitted(true)
      onSubmitted?.()
    } catch (err: unknown) {
      setSubmitError(err instanceof Error ? err.message : 'Submission failed')
    } finally {
      setSubmitting(false)
    }
  }

  const applicationRequiredFields = ['name', 'dob', 'address', 'phone', 'email', 'emergency', 'prev_shop', 'experience', 'specialties', 'references']
  const sectionRequirementMessage: Partial<Record<ScrollSection, string>> = {
    application: 'Complete all application fields before accepting this stage.',
    bloodborne: 'Upload BBP, First Aid, CPR, government ID, and select tax status before accepting this stage.',
  }

  const sectionRequirementsMet = (section: ScrollSection) => {
    if (section === 'application') return applicationRequiredFields.every(key => fields[key]?.trim())
    if (section === 'bloodborne') return !!bbpCert && !!firstAidCert && !!cprCert && !!govtId && taxOptIn !== null
    return true
  }

  const sectionIsComplete = (section: ScrollSection) => acceptedSections[section] && sectionRequirementsMet(section)
  const sectionIsUnlocked = (section: ScrollSection) => {
    const index = SCROLL_SECTIONS.findIndex(s => s.id === section)
    if (index <= 0) return true
    return SCROLL_SECTIONS.slice(0, index).every(s => sectionIsComplete(s.id))
  }

  const canAcceptSection = (section: ScrollSection) => (
    sectionIsUnlocked(section)
    && sectionRequirementsMet(section)
    && (acceptanceNames[section]?.trim().length ?? 0) >= 2
  )
  const acceptSection = (section: ScrollSection) => {
    if (!canAcceptSection(section)) return
    setAcceptedSections(prev => ({ ...prev, [section]: true }))
    if (!continuous) {
      const sectionIndex = SCROLL_SECTIONS.findIndex(item => item.id === section)
      const next = SCROLL_SECTIONS[sectionIndex + 1]?.id
      if (next) setActiveSection(next)
    }
  }

  const renderStageGate = (section: ScrollSection) => {
    if (section === 'signature') return null
    const isAccepted = sectionIsComplete(section)
    const sectionIndex = SCROLL_SECTIONS.findIndex(item => item.id === section)
    const nextLabel = SCROLL_SECTIONS[sectionIndex + 1]?.label ?? 'Sign-Off'
    const canAccept = canAcceptSection(section)

    return (
      <div className={`scroll-stage-gate${isAccepted ? ' accepted' : ''}`}>
        <div>
          <div className="scroll-stage-gate-title">{isAccepted ? 'Stage accepted' : 'Sign and accept this stage'}</div>
          <p>{isAccepted ? `You can continue to ${nextLabel}.` : 'Type your name to confirm you read, completed, and accept this section.'}</p>
          {!sectionRequirementsMet(section) && (
            <p className="scroll-stage-gate-warning">{sectionRequirementMessage[section] ?? 'Complete this stage before continuing.'}</p>
          )}
        </div>
        <div className="scroll-stage-gate-action">
          <input
            className="scroll-stage-gate-input"
            value={acceptanceNames[section] ?? ''}
            onChange={(e) => setAcceptanceNames(prev => ({ ...prev, [section]: e.target.value }))}
            placeholder="Type legal name"
            aria-label="Type legal name to accept stage"
          />
          <button type="button" className="scroll-stage-gate-btn" onClick={() => acceptSection(section)} disabled={!canAccept}>
            {continuous ? 'Accept Section' : 'Accept & Continue'}
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className={`scroll-container${continuous ? ' scroll-container--continuous' : ''}`}>
      {/* Header */}
      <div className="scroll-header">
        <h2 className="scroll-title">Artist Scroll</h2>
        <span className="scroll-artist">{artistName}</span>
      </div>

      {/* Section tabs */}
      {!continuous && <div className="scroll-tabs">
        {SCROLL_SECTIONS.map(s => {
          const locked = !sectionIsUnlocked(s.id)
          const complete = sectionIsComplete(s.id)
          return (
          <button
            key={s.id}
            className={`scroll-tab ${activeSection === s.id ? 'active' : ''}${locked ? ' locked' : ''}${complete ? ' complete' : ''}`}
            onClick={() => { if (!locked) setActiveSection(s.id) }}
            disabled={locked}
            title={locked ? 'Accept prior stage to unlock' : undefined}
          >
            <span className="scroll-tab-num">{s.num}</span>
            <span className="scroll-tab-label">{s.label}</span>
          </button>
        )})}
      </div>}

      {/* Content */}
      <div className="scroll-content">

        {/* ═══ I. EMPLOYMENT APPLICATION ═══ */}
        {(continuous || activeSection === 'application') && (
          <div className="scroll-section scroll-section-application">
            <h3 className="scroll-section-title">Employment Application</h3>
            <p className="scroll-legal-ref">SC DHEC Regulation 61-42 requires employment records for all body art practitioners.</p>

            <div className="scroll-application-grid">
              <div className="scroll-field-group">
                <div className="scroll-field-label">Personal Information</div>
                <div className="scroll-field-row"><span>Full Legal Name</span><input {...field('name')} /></div>
                <div className="scroll-field-row"><span>Date of Birth</span><input type="date" {...field('dob')} /></div>
                <div className="scroll-field-row"><span>Street Address</span><input {...field('address')} /></div>
                <div className="scroll-field-row"><span>Phone</span><input type="tel" {...field('phone')} /></div>
                <div className="scroll-field-row"><span>Email</span><input type="email" {...field('email')} /></div>
                <div className="scroll-field-row"><span>Emergency Contact</span><input {...field('emergency')} /></div>
              </div>

              <div className="scroll-field-group">
                <div className="scroll-field-label">Employment History</div>
                <div className="scroll-field-row"><span>Previous Shop / Studio</span><input {...field('prev_shop')} /></div>
                <div className="scroll-field-row"><span>Years of Experience</span><input {...field('experience')} /></div>
                <div className="scroll-field-row"><span>Specialties</span><input {...field('specialties')} /></div>
                <div className="scroll-field-row"><span>References (2 minimum)</span><input {...field('references')} /></div>
              </div>
            </div>

            <div className="scroll-consent-box">
              <p>I authorize Ryozan Studio to verify the information provided above, including contacting references and conducting a background check as permitted by law.</p>
            </div>

            {renderStageGate('application')}
          </div>
        )}

        {/* ═══ II. ARTIST AGREEMENT ═══ */}
        {(continuous || activeSection === 'agreement') && (
          <div className="scroll-section">
            <h3 className="scroll-section-title">Artist Agreement</h3>
            <p className="scroll-legal-ref">Terms of engagement between the artist and Ryozan Studio</p>

            <div className="scroll-agreement-compact">
              <section>
                <h4>Compensation & Schedule</h4>
                <p>Compensation, commission rates, and payment schedule follow the attached compensation addendum. Artist keeps availability current in Ryozan systems and gives 48-hour notice for schedule changes unless emergency prevents it.</p>
              </section>
              <section>
                <h4>Client, Brand & Station Standards</h4>
                <p>Artist maintains professional conduct, verifies age and identity, obtains consent documentation, gives aftercare instructions, represents the shop appropriately, and keeps station/equipment compliant with Ryozan and SC DHEC standards.</p>
              </section>
              <section>
                <h4>Compliance & Termination</h4>
                <p>Artist keeps permits, certifications, and training current. Non-compliance may suspend tattooing privileges. Either party may terminate with 14 days written notice, with immediate termination allowed for safety, conduct, or permit violations.</p>
              </section>
            </div>

            {renderStageGate('agreement')}
          </div>
        )}

        {/* ═══ III. BLOODBORNE PATHOGEN ACKNOWLEDGMENT ═══ */}
        {(continuous || activeSection === 'bloodborne') && (
          <div className="scroll-section">
            <h3 className="scroll-section-title">Bloodborne Pathogen Exposure Control Plan</h3>
            <p className="scroll-legal-ref">OSHA 29 CFR 1910.1030 | SC DHEC Regulation 61-42</p>

            <div className="scroll-article">
              <h4>Universal Precautions</h4>
              <p>All human blood and certain body fluids shall be treated as if known to be infectious for HIV, HBV, HCV, and other bloodborne pathogens. Universal precautions apply to all client interactions involving potential exposure.</p>
            </div>

            <div className="scroll-article">
              <h4>Personal Protective Equipment</h4>
              <p>Gloves must be worn during all procedures involving potential contact with blood or body fluids. Gloves must be changed between clients and when torn or punctured. Additional PPE (face shields, protective eyewear) is available upon request.</p>
            </div>

            <div className="scroll-article">
              <h4>Sharps Handling</h4>
              <p>All sharps (needles, razor blades) must be disposed of immediately after use in designated sharps containers. Sharps containers must not be overfilled beyond the 3/4 fill line. Recapping needles by hand is strictly prohibited.</p>
            </div>

            <div className="scroll-article">
              <h4>Decontamination</h4>
              <p>Work surfaces must be decontaminated with EPA-registered disinfectant after each client and at the end of each shift. All reusable instruments must be properly sterilized between uses.</p>
            </div>

            <div className="scroll-article">
              <h4>Post-Exposure Protocol</h4>
              <p>In the event of a needlestick or other exposure incident: (1) Wash affected area immediately with soap and water. (2) Report incident to shop owner/manager. (3) Seek medical evaluation within 2 hours. (4) Document incident in the exposure log.</p>
            </div>

            <div className="scroll-consent-box">
              <p>I acknowledge that I have received training on the Bloodborne Pathogen Exposure Control Plan, understand the risks associated with occupational exposure, and agree to follow all protocols outlined above.</p>
            </div>

            {/* BBP Certificate Upload */}
            <div className="scroll-cert-upload">
              <div className="scroll-field-label">BBP Training Certificate</div>
              <p className="scroll-cert-hint">
                Upload your current Bloodborne Pathogen training certificate (OSHA-accepted). Accepted formats: JPG, PNG, PDF.
              </p>
              <input
                ref={certInputRef}
                type="file"
                accept="image/jpeg,image/png,application/pdf"
                style={{ display: 'none' }}
                onChange={handleCertFile}
              />
              {bbpCertPreview ? (
                <div className="scroll-cert-preview">
                  {bbpCert?.type === 'application/pdf' ? (
                    <div className="scroll-cert-pdf-indicator">
                      <span>PDF: {bbpCert.name}</span>
                    </div>
                  ) : (
                    <img src={bbpCertPreview} alt="BBP Certificate preview" className="scroll-cert-img" />
                  )}
                  <button
                    type="button"
                    className="scroll-cert-remove"
                    onClick={() => { setBbpCert(null); setBbpCertPreview(null); if (certInputRef.current) certInputRef.current.value = '' }}
                  >
                    Remove
                  </button>
                </div>
              ) : (
                <button
                  type="button"
                  className="scroll-cert-upload-btn"
                  onClick={() => certInputRef.current?.click()}
                >
                  Upload Certificate
                </button>
              )}
            </div>

            {/* First Aid Certificate — Required */}
            <div className="scroll-cert-upload">
              <div className="scroll-field-label">
                First Aid Certificate <span style={{ color: '#e05c5c', fontSize: '0.65rem', fontWeight: 400 }}>Required</span>
              </div>
              <p className="scroll-cert-hint">Current First Aid certification. Accepted formats: JPG, PNG, PDF.</p>
              <input
                ref={firstAidRef}
                type="file"
                accept="image/jpeg,image/png,application/pdf"
                style={{ display: 'none' }}
                onChange={handleFirstAidFile}
              />
              {firstAidPreview ? (
                <div className="scroll-cert-preview">
                  {firstAidCert?.type === 'application/pdf'
                    ? <div className="scroll-cert-pdf-indicator"><span>PDF: {firstAidCert.name}</span></div>
                    : <img src={firstAidPreview} alt="First Aid cert" className="scroll-cert-img" />
                  }
                  <button type="button" className="scroll-cert-remove"
                    onClick={() => { setFirstAidCert(null); setFirstAidPreview(null); if (firstAidRef.current) firstAidRef.current.value = '' }}>
                    Remove
                  </button>
                </div>
              ) : (
                <button type="button" className="scroll-cert-upload-btn" onClick={() => firstAidRef.current?.click()}>
                  Upload First Aid Cert
                </button>
              )}
            </div>

            {/* CPR Certificate — Required */}
            <div className="scroll-cert-upload">
              <div className="scroll-field-label">
                CPR Certificate <span style={{ color: '#e05c5c', fontSize: '0.65rem', fontWeight: 400 }}>Required</span>
              </div>
              <p className="scroll-cert-hint">Current CPR certification. Accepted formats: JPG, PNG, PDF.</p>
              <input
                ref={cprRef}
                type="file"
                accept="image/jpeg,image/png,application/pdf"
                style={{ display: 'none' }}
                onChange={handleCprFile}
              />
              {cprPreview ? (
                <div className="scroll-cert-preview">
                  {cprCert?.type === 'application/pdf'
                    ? <div className="scroll-cert-pdf-indicator"><span>PDF: {cprCert.name}</span></div>
                    : <img src={cprPreview} alt="CPR cert" className="scroll-cert-img" />
                  }
                  <button type="button" className="scroll-cert-remove"
                    onClick={() => { setCprCert(null); setCprPreview(null); if (cprRef.current) cprRef.current.value = '' }}>
                    Remove
                  </button>
                </div>
              ) : (
                <button type="button" className="scroll-cert-upload-btn" onClick={() => cprRef.current?.click()}>
                  Upload CPR Cert
                </button>
              )}
            </div>

            {/* Government ID — Required */}
            <div className="scroll-cert-upload">
              <div className="scroll-field-label">
                Government-Issued ID <span style={{ color: '#e05c5c', fontSize: '0.65rem', fontWeight: 400 }}>Required</span>
              </div>
              <p className="scroll-cert-hint">Driver's license, passport, or state ID. Accepted formats: JPG, PNG, PDF.</p>
              <input
                ref={govtIdRef}
                type="file"
                accept="image/jpeg,image/png,application/pdf"
                style={{ display: 'none' }}
                onChange={handleGovtIdFile}
              />
              {govtIdPreview ? (
                <div className="scroll-cert-preview">
                  {govtId?.type === 'application/pdf'
                    ? <div className="scroll-cert-pdf-indicator"><span>PDF: {govtId.name}</span></div>
                    : <img src={govtIdPreview} alt="Government ID" className="scroll-cert-img" />
                  }
                  <button type="button" className="scroll-cert-remove"
                    onClick={() => { setGovtId(null); setGovtIdPreview(null); if (govtIdRef.current) govtIdRef.current.value = '' }}>
                    Remove
                  </button>
                </div>
              ) : (
                <button type="button" className="scroll-cert-upload-btn" onClick={() => govtIdRef.current?.click()}>
                  Upload Government ID
                </button>
              )}
            </div>

            {/* Tax Status — Required */}
            <div className="scroll-cert-upload">
              <div className="scroll-field-label">
                Tax Status <span style={{ color: '#e05c5c', fontSize: '0.65rem', fontWeight: 400 }}>Required</span>
              </div>
              <p className="scroll-cert-hint">Select how your earnings will be handled for tax purposes.</p>
              <div className="scroll-tax-options">
                <label className={`scroll-tax-option${taxOptIn === true ? ' is-selected' : ''}`}>
                  <input type="radio" name="tax_opt_in" checked={taxOptIn === true} onChange={() => setTaxOptIn(true)} />
                  <div className="scroll-tax-option__copy">
                    <div className="scroll-tax-option__title">I want taxes done for me</div>
                    <div className="scroll-tax-option__description">I am employed by Ryozan. Applicable taxes will be withheld from my earnings and a W-2 issued at year end.</div>
                  </div>
                </label>
                <label className={`scroll-tax-option${taxOptIn === false ? ' is-selected' : ''}`}>
                  <input type="radio" name="tax_opt_in" checked={taxOptIn === false} onChange={() => setTaxOptIn(false)} />
                  <div className="scroll-tax-option__copy">
                    <div className="scroll-tax-option__title">I am a pirate</div>
                    <div className="scroll-tax-option__description">Earnings paid in full as contractor contributions. You are responsible for filing and paying your own taxes. A 1099 will be issued if applicable.</div>
                  </div>
                </label>
              </div>
            </div>

            {renderStageGate('bloodborne')}
          </div>
        )}

        {/* ═══ IV. FIRE SAFETY & EMERGENCY PROCEDURES ═══ */}
        {(continuous || activeSection === 'fire') && (
          <div className="scroll-section">
            <h3 className="scroll-section-title">Fire Safety & Emergency Procedures</h3>
            <p className="scroll-legal-ref">OSHA General Industry Standards | Local Fire Code</p>

            <div className="scroll-article">
              <h4>Emergency Exits</h4>
              <p>Know the location of all emergency exits. The primary exit is the front entrance. The secondary exit is the rear door. Emergency exit routes must remain clear and unobstructed at all times.</p>
            </div>

            <div className="scroll-article">
              <h4>Fire Extinguishers</h4>
              <p>ABC-rated fire extinguishers are located near the front entrance and in the break area. All artists must know the PASS technique: Pull pin, Aim at base of fire, Squeeze handle, Sweep side to side.</p>
            </div>

            <div className="scroll-article">
              <h4>Evacuation Procedure</h4>
              <p>1. Alert others verbally ("Fire, everyone out").<br />
              2. If actively tattooing, secure the client (stop machine, cover wound with clean barrier).<br />
              3. Guide your client to the nearest exit.<br />
              4. Do not stop to collect personal belongings.<br />
              5. Meet at the designated assembly point (parking lot, away from building).<br />
              6. Account for all clients and staff. Report to fire department on arrival.</p>
            </div>

            <div className="scroll-article">
              <h4>Emergency Contacts</h4>
              <div className="scroll-contact-grid">
                <span className="scroll-contact-label">Emergency</span><span className="scroll-contact-value">911</span>
                <span className="scroll-contact-label">Poison Control</span><span className="scroll-contact-value">1-800-222-1222</span>
                {studioSettings.owner_name && (
                  <><span className="scroll-contact-label">Shop Owner</span><span className="scroll-contact-value">{studioSettings.owner_name}</span></>
                )}
                {studioSettings.contact_phone && (
                  <><span className="scroll-contact-label">Shop Phone</span><span className="scroll-contact-value">{studioSettings.contact_phone}</span></>
                )}
              </div>
            </div>

            <div className="scroll-consent-box">
              <p>I acknowledge that I have been trained on fire safety and emergency evacuation procedures at Ryozan Studio I know the location of all exits and fire extinguishers.</p>
            </div>

            {renderStageGate('fire')}
          </div>
        )}

        {/* ═══ V. SC DHEC 61-42 SUMMARY ═══ */}
        {(continuous || activeSection === 'dhec') && (
          <div className="scroll-section">
            <h3 className="scroll-section-title">SC DHEC Regulation 61-42</h3>
            <p className="scroll-legal-ref">Body Art Facility Standards | SC Tattoo Facility Licensing Act (SC Code 44-34-10 et seq.)</p>

            <div className="scroll-article">
              <h4>Facility Permit</h4>
              <p>Ryozan Studio maintains a current SC DHEC Body Art Facility Permit, renewed annually. The permit must be displayed in a location visible to clients at all times.</p>
            </div>

            <div className="scroll-article">
              <h4>Practitioner Permits</h4>
              <p>Every artist must hold a valid SC Body Art Practitioner Permit. Permits must be displayed at or near your station. Operating without a valid permit is a violation subject to fines and facility closure.</p>
            </div>

            <div className="scroll-article">
              <h4>Client Consent</h4>
              <p>Written informed consent must be obtained before every procedure. The consent form must include: client identification verification, health history, procedure description, risk acknowledgment, and client signature.</p>
            </div>

            <div className="scroll-article">
              <h4>Age Verification</h4>
              <p>SC Code 16-17-700 prohibits tattooing of minors regardless of parental consent. Valid government-issued photo ID must be verified for every client. No exceptions.</p>
            </div>

            <div className="scroll-article">
              <h4>Record Retention</h4>
              <p>All client records (consent forms, waivers, ID verification) must be retained for a minimum of 3 years. Records are stored digitally in the Ryozan system and are accessible for DHEC inspection upon request.</p>
            </div>

            <div className="scroll-article">
              <h4>Inspections</h4>
              <p>SC DHEC inspectors may conduct unannounced inspections during operating hours. All staff are expected to cooperate fully with inspectors and provide access to all areas of the facility and all records.</p>
            </div>

            <div className="scroll-article">
              <h4>Penalties</h4>
              <p>Violations may result in: written warnings, monetary fines, permit suspension, permit revocation, or facility closure. Repeat violations carry escalating penalties.</p>
            </div>

            {renderStageGate('dhec')}
          </div>
        )}

        {/* ═══ VI. ACKNOWLEDGMENT & SIGNATURE ═══ */}
        {(continuous || activeSection === 'signature') && (
          <div className="scroll-section">
            <h3 className="scroll-section-title">Acknowledgment & Signature</h3>
            <p className="scroll-legal-ref">Final sign-off binding all sections of the Artist Scroll.</p>

            {/* ── Filled Form Review ── */}
            {Object.keys(fields).filter(k => fields[k]?.trim()).length > 0 && (
              <div style={{ marginBottom: '1.5rem' }}>
                <div className="scroll-field-label" style={{ marginBottom: '0.75rem' }}>
                  Review Your Application
                </div>

                {/* Personal Information */}
                {['name','dob','address','phone','email','emergency'].some(k => fields[k]) && (
                  <div style={{ marginBottom: '1rem', padding: '0.85rem 1rem', background: 'rgba(226,218,194,0.025)', border: '1px solid var(--border-subtle)', borderRadius: '8px' }}>
                    <div style={{ fontSize: '0.62rem', color: 'var(--accent-cream-tile)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.6rem', fontWeight: 700 }}>
                      I — Personal Information
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.45rem 1.25rem' }}>
                      {[
                        ['name', 'Full Legal Name'],
                        ['dob', 'Date of Birth'],
                        ['address', 'Street Address'],
                        ['phone', 'Phone'],
                        ['email', 'Email'],
                        ['emergency', 'Emergency Contact'],
                      ].filter(([k]) => fields[k]?.trim()).map(([k, label]) => (
                        <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                          <span style={{ fontSize: '0.6rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
                          <span style={{ fontSize: '0.76rem', color: 'var(--text)', fontFamily: 'Inter, sans-serif' }}>{fields[k]}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Employment History */}
                {['prev_shop','experience','specialties','references'].some(k => fields[k]) && (
                  <div style={{ marginBottom: '1rem', padding: '0.85rem 1rem', background: 'rgba(226,218,194,0.025)', border: '1px solid var(--border-subtle)', borderRadius: '8px' }}>
                    <div style={{ fontSize: '0.62rem', color: 'var(--accent-cream-tile)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.6rem', fontWeight: 700 }}>
                      I — Employment History
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.45rem 1.25rem' }}>
                      {[
                        ['prev_shop', 'Previous Shop / Studio'],
                        ['experience', 'Years of Experience'],
                        ['specialties', 'Specialties'],
                        ['references', 'References'],
                      ].filter(([k]) => fields[k]?.trim()).map(([k, label]) => (
                        <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                          <span style={{ fontSize: '0.6rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{label}</span>
                          <span style={{ fontSize: '0.76rem', color: 'var(--text)', fontFamily: 'Inter, sans-serif' }}>{fields[k]}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}

                {/* Compliance docs summary */}
                {[
                  { label: 'III — BBP Certificate', file: bbpCert },
                  { label: 'III — First Aid Certificate', file: firstAidCert },
                  { label: 'III — CPR Certificate', file: cprCert },
                  { label: 'III — Government ID', file: govtId },
                ].filter(d => d.file).map(d => (
                  <div key={d.label} style={{ marginBottom: '0.5rem', padding: '0.75rem 1rem', background: 'rgba(226,218,194,0.025)', border: '1px solid var(--border-subtle)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <div style={{ fontSize: '0.62rem', color: 'var(--accent-cream-tile)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 700 }}>{d.label}</div>
                    <span style={{ fontSize: '0.76rem', color: 'var(--text)', marginLeft: 'auto' }}>{d.file!.name}</span>
                    <span style={{ fontSize: '0.65rem', color: '#6dbf7e' }}>Ready</span>
                  </div>
                ))}
                {taxOptIn !== null && (
                  <div style={{ marginBottom: '1rem', padding: '0.75rem 1rem', background: 'rgba(226,218,194,0.025)', border: '1px solid var(--border-subtle)', borderRadius: '8px', display: 'flex', alignItems: 'center', gap: '0.75rem' }}>
                    <div style={{ fontSize: '0.62rem', color: 'var(--accent-cream-tile)', textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 700 }}>III — Tax Status</div>
                    <span style={{ fontSize: '0.76rem', color: 'var(--text)', marginLeft: 'auto' }}>{taxOptIn ? 'Withhold taxes (W-2)' : 'Contractor (1099)'}</span>
                    <span style={{ fontSize: '0.65rem', color: '#6dbf7e' }}>Selected</span>
                  </div>
                )}

                {/* Any remaining custom fields */}
                {Object.entries(fields)
                  .filter(([k, v]) => !['name','dob','address','phone','email','emergency','prev_shop','experience','specialties','references'].includes(k) && v?.trim())
                  .length > 0 && (
                  <div style={{ padding: '0.85rem 1rem', background: 'rgba(226,218,194,0.025)', border: '1px solid var(--border-subtle)', borderRadius: '8px' }}>
                    <div style={{ fontSize: '0.62rem', color: 'var(--accent-cream-tile)', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: '0.6rem', fontWeight: 700 }}>
                      Additional Fields
                    </div>
                    <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '0.45rem 1.25rem' }}>
                      {Object.entries(fields)
                        .filter(([k, v]) => !['name','dob','address','phone','email','emergency','prev_shop','experience','specialties','references'].includes(k) && v?.trim())
                        .map(([k, v]) => (
                          <div key={k} style={{ display: 'flex', flexDirection: 'column', gap: 2 }}>
                            <span style={{ fontSize: '0.6rem', color: 'var(--muted)', textTransform: 'uppercase', letterSpacing: '0.06em' }}>{k.replace(/_/g, ' ')}</span>
                            <span style={{ fontSize: '0.76rem', color: 'var(--text)' }}>{v}</span>
                          </div>
                        ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            <div className="scroll-consent-box scroll-consent-final">
              <p>I, the undersigned, acknowledge that I have received, read, and understand all sections of the Artist Scroll, including:</p>
              <ul>
                <li>Employment Application (Section I)</li>
                <li>Artist Agreement (Section II)</li>
                <li>Bloodborne Pathogen Exposure Control Plan (Section III)</li>
                <li>Fire Safety and Emergency Procedures (Section IV)</li>
                <li>SC DHEC Regulation 61-42 requirements (Section V)</li>
              </ul>
              <p>I agree to maintain compliance with all listed requirements and understand that failure to do so may result in disciplinary action up to and including termination.</p>
            </div>

            <div className="scroll-sig-block">
              <div className="scroll-sig-label">Artist Signature</div>
              <SignaturePad onSign={setArtistSig} value={artistSig} strokeColor="#e8e0d4" />
              <div className="scroll-sig-date">{new Date().toLocaleDateString('en-US', { weekday: 'long', year: 'numeric', month: 'long', day: 'numeric' })}</div>
            </div>

            <div className="scroll-sig-block scroll-sig-block--owner-pending">
              <div className="scroll-sig-label">Owner / Manager Countersignature</div>
              <p>Your submission stays bound to your account. Founder or admin review is recorded separately and cannot be signed by you or Spyder.</p>
            </div>

            {submitted ? (
              <div className="scroll-submit-success">
                Artist Scroll signed and saved. All sections acknowledged.
              </div>
            ) : (
              <>
                {submitError && (
                  <div className="scroll-submit-error">{submitError}</div>
                )}
                {artistSig && (
                  <button
                    className="scroll-submit-btn"
                    onClick={handleSubmit}
                    disabled={submitting}
                  >
                    {submitting ? 'Saving...' : preview ? 'Finish preview' : 'Complete Artist Scroll'}
                  </button>
                )}
                {!artistSig && (
                  <p style={{ fontSize: '0.72rem', color: 'var(--muted)', textAlign: 'center', marginTop: '1rem', opacity: 0.7 }}>
                    Your signature is required to submit.
                  </p>
                )}
              </>
            )}
          </div>
        )}
      </div>

      {/* Footer */}
      <div className="scroll-footer">
        <span>Ryozan Studio</span>
        <span>SC DHEC Regulation 61-42 Compliant</span>
      </div>
    </div>
  )
}

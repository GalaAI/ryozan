import { Helmet } from 'react-helmet-async'
import { Link } from 'react-router-dom'
import AppProductShell from '../components/app-product/AppProductShell'
import { BRAND } from '../lib/brand'
import { RYOZAN_BANDIT_ORCHESTRA } from '../lib/agent-card-hands'

const steps = [
  {
    number: '01',
    title: 'Sign in to the studio',
    body: 'Founders, artists and staff open one portal. Every agent works inside the role and records of the signed-in account.',
  },
  {
    number: '02',
    title: 'Run the day with Gala',
    body: 'Gala reads real bookings, schedules and conversations through registered tools and answers with what it found.',
  },
  {
    number: '03',
    title: 'Hand work to a Bandit',
    body: 'Each Bandit owns one lane, from booking and Courier replies to aftercare, and runs on its own Nemotron model.',
  },
  {
    number: '04',
    title: 'Keep the receipts',
    body: 'Agents claim an action only from a tool receipt. Bookings and Courier threads stay the durable record.',
  },
]

export default function AppLandingPage() {
  return (
    <AppProductShell>
      <Helmet>
        <title>{`${BRAND.product} | Studio operating system`}</title>
        <meta name="description" content={`${BRAND.product}: ${BRAND.tagline}, powered by ${BRAND.models}.`} />
        <meta property="og:title" content={BRAND.product} />
        <meta property="og:description" content={BRAND.tagline} />
      </Helmet>

      <section className="app-hero">
        <div className="app-hero__copy">
          <p className="app-kicker"><span /> {BRAND.models}</p>
          <h1>{BRAND.product}.<br /><em>The studio runs itself.</em></h1>
          <p className="app-hero__lede">
            {BRAND.product} is a multi-agent operating system for tattoo studios. Gala orchestrates eight specialist
            agents across bookings, scheduling, Courier messages, commerce and aftercare, all on open NVIDIA models.
          </p>
          <div className="app-hero__actions">
            <Link className="app-button app-button--primary" to="/portal">Open the studio portal <span>↗</span></Link>
          </div>
          <ul className="app-hero__proof" aria-label="Platform facts">
            <li><span>✓</span> Nemotron 3 Super and Nano</li>
            <li><span>✓</span> Role-scoped tools</li>
            <li><span>✓</span> Receipts, not claims</li>
          </ul>
        </div>
        <div className="booking-ledger" aria-label={`${BRAND.product} seal`}>
          <img className="booking-ledger__mark" src={BRAND.mark} alt={BRAND.markAlt} />
        </div>
      </section>

      <section className="app-purpose" aria-labelledby="roster-title">
        <p className="app-section-label">The roster</p>
        <h2 id="roster-title">Gala and the eight Bandits of Ryozanpaku.</h2>
        <ol className="app-flow__steps" aria-label="Agents and their models">
          {RYOZAN_BANDIT_ORCHESTRA.map(member => (
            <li key={member.id}>
              <span>{member.manaPip}</span>
              <h3>{member.fullTitle}</h3>
              <p>{member.domain}</p>
              <p><code>{member.nemotronModel}</code></p>
            </li>
          ))}
        </ol>
      </section>

      <section className="app-flow" id="how-it-works" aria-labelledby="flow-title">
        <div className="app-section-heading">
          <div>
            <p className="app-section-label">How it works</p>
            <h2 id="flow-title">From request to receipt.</h2>
          </div>
          <p>One portal, one set of records, and agents that only report what their tools returned.</p>
        </div>
        <ol className="app-flow__steps">
          {steps.map(step => (
            <li key={step.number}>
              <span>{step.number}</span>
              <h3>{step.title}</h3>
              <p>{step.body}</p>
            </li>
          ))}
        </ol>
      </section>

      <section className="app-portal-cta">
        <img src={BRAND.mark} alt={BRAND.markAlt} />
        <p className="app-section-label">Open the stronghold</p>
        <h2>Sign in and let Gala brief you.</h2>
        <div>
          <Link className="app-button app-button--primary" to="/portal">Studio portal <span>↗</span></Link>
        </div>
      </section>
    </AppProductShell>
  )
}

import { Helmet } from 'react-helmet-async'
import AppProductShell from '../components/app-product/AppProductShell'

export default function AppTermsPage() {
  return (
    <AppProductShell policyPage>
      <Helmet>
        <title>App Terms of Service | Ryozan Studio</title>
        <meta name="description" content="Terms of service for the Ryozan Studio booking and client portal app." />
        <link rel="canonical" href={`${window.location.origin}/terms`} />
      </Helmet>
      <article className="app-policy">
        <header>
          <p className="app-section-label">Ryozan Studio App</p>
          <h1>Terms of Service</h1>
          <p>Effective August 18, 2026</p>
        </header>

        <section>
          <h2>1. The service</h2>
          <p>The Ryozan Studio app provides tattoo inquiry, consultation booking, appointment scheduling, deposit payment, forms, client portal, collecting and rewards features for Ryozan clients and authorized staff.</p>
        </section>

        <section>
          <h2>2. Accounts and eligibility</h2>
          <p>You must provide accurate information, protect your login credentials and use the app only for lawful purposes. Tattoo services are subject to age, identification, consent and health requirements that may apply at the time of service.</p>
        </section>

        <section>
          <h2>3. Booking and availability</h2>
          <p>Displayed times are based on artist availability and connected scheduling data. A submitted request is not a confirmed tattoo appointment until the app or an authorized artist confirms it. Consultation bookings may be confirmed directly when a valid time is selected.</p>
        </section>

        <section>
          <h2>4. Deposits and payments</h2>
          <p>Some tattoo appointments require a deposit before they are secured. Consultations do not require a deposit. All payments are final and non-refundable except where required by law or for a duplicate or unauthorized charge. With artist approval, a tattoo deposit may be transferred to a rescheduled appointment, but it is not returned as cash. Payments are processed by Stripe, and the amount shown at checkout is the full amount charged by the studio.</p>
        </section>

        <section>
          <h2>5. Google Calendar</h2>
          <p>Authorized artist accounts may connect Google Calendar to read availability and create, update or delete synchronized booking events. Calendar access is optional and can be disconnected. Use of Google information is governed by our <a href="/privacy">App Privacy Policy</a> and Google&apos;s applicable terms.</p>
        </section>

        <section>
          <h2>6. Acceptable use</h2>
          <p>You may not misuse the app, interfere with its operation, attempt unauthorized access, upload unlawful or harmful material, impersonate another person or use the service to harass artists, staff or clients.</p>
        </section>

        <section>
          <h2>7. Availability and changes</h2>
          <p>We may maintain, change, suspend or discontinue features when necessary. We work to keep booking and scheduling information accurate, but service interruptions, provider outages or artist changes may affect availability.</p>
        </section>

        <section>
          <h2>8. Contact</h2>
          <p>Questions about these terms go to the studio that operates this Ryozan install, through the portal&apos;s Courier inbox.</p>
        </section>
      </article>
    </AppProductShell>
  )
}

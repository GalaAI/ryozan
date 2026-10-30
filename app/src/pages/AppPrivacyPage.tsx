import { Helmet } from 'react-helmet-async'
import AppProductShell from '../components/app-product/AppProductShell'

export default function AppPrivacyPage() {
  return (
    <AppProductShell policyPage>
      <Helmet>
        <title>App Privacy Policy | Ryozan Studio</title>
        <meta name="description" content="Privacy policy for the Ryozan Studio booking app, including its use of Google Calendar data." />
        <link rel="canonical" href="https://app.ryozan.app/privacy" />
      </Helmet>
      <article className="app-policy">
        <header>
          <p className="app-section-label">Ryozan Studio App</p>
          <h1>Privacy Policy</h1>
          <p>Effective August 16, 2026</p>
        </header>

        <section>
          <h2>1. Purpose of this app</h2>
          <p>The Ryozan Studio app helps clients request tattoo work, book consultations, choose available appointment times, pay required deposits, complete forms, manage appointments and use a private client portal. It helps authorized artists manage those bookings and synchronize their schedules.</p>
        </section>

        <section>
          <h2>2. Information we collect</h2>
          <ul>
            <li><strong>Account information:</strong> name, email address, phone number and authentication information.</li>
            <li><strong>Booking information:</strong> artist selection, tattoo ideas, placement, reference images, consultation preferences, appointment times and communication history.</li>
            <li><strong>Payment information:</strong> deposits and other payments are processed by Stripe. Ryozan does not store full payment card numbers.</li>
            <li><strong>Forms and health information:</strong> information submitted through required consent and safety forms.</li>
            <li><strong>Google account and Calendar information:</strong> the specific data described in Section 3 when an authorized artist connects Google Calendar.</li>
            <li><strong>Usage information:</strong> device, browser, IP address, page visits and feature interactions used for security, reliability and service improvement.</li>
          </ul>
        </section>

        <section className="app-policy__google">
          <h2>3. Google Calendar data we access and use</h2>
          <p>Connecting Google Calendar is optional. When an artist authorizes the integration, Ryozan requests Google account identity information and Calendar access so the booking app can perform these scheduling functions:</p>
          <ul>
            <li><strong>Read:</strong> the connected account identity, calendar identifiers and names, free or busy time ranges, and event identifiers, titles, locations, start and end times and links.</li>
            <li><strong>Create:</strong> consultation and tattoo appointment events with booking titles, descriptions, locations, times, reminders and reference codes.</li>
            <li><strong>Update:</strong> the matching Calendar event when an authorized artist changes a booking time or appointment information in Ryozan.</li>
            <li><strong>Delete:</strong> the matching Calendar event when an authorized artist cancels or removes the synchronized booking in Ryozan.</li>
            <li><strong>Invite:</strong> add a client email address as an event attendee when a booking is confirmed.</li>
            <li><strong>Conference:</strong> request a Google Meet link when a consultation is booked with Google Meet as its method.</li>
          </ul>
          <p>Google account identity and Calendar data are used only to authenticate the connected artist, prevent scheduling conflicts, show accurate availability, import relevant appointments and keep Ryozan bookings synchronized with the connected calendar. Ryozan does not request access to Gmail, Google Drive, Google Contacts or Google Directory.</p>
          <p>If Ryozan adds another Google-powered feature, this policy and the app home page will identify that service, the data requested and its purpose before the additional Google scope is requested.</p>
        </section>

        <section>
          <h2>4. Google data storage, sharing and retention</h2>
          <p>Ryozan stores the OAuth refresh token needed to maintain an authorized connection, along with calendar identifiers, Google event identifiers and event links needed to synchronize bookings. Tokens are stored on the protected application backend and are not exposed to clients.</p>
          <p>Google user data is not sold, used for advertising or shared with data brokers. It is shared only with infrastructure providers needed to operate the app, or when required by law. Booking event information may be shared with the assigned artist and the invited client as part of the appointment.</p>
          <p>Disconnecting Google Calendar removes the stored connection token. Synchronized event identifiers may remain with the related booking record for operational history, and events already written to Google Calendar remain in Google Calendar unless the authorized user removes them.</p>
          <p>Ryozan Studio&apos;s use and transfer of information received from Google APIs adheres to the <a href="https://developers.google.com/terms/api-services-user-data-policy" target="_blank" rel="noreferrer">Google API Services User Data Policy</a>, including the Limited Use requirements.</p>
        </section>

        <section>
          <h2>5. How we use other information</h2>
          <p>We use information to operate bookings and portals, communicate about appointments and aftercare, process deposits and payments, maintain health and consent records, protect the app, comply with legal obligations and improve the service.</p>
        </section>

        <section>
          <h2>6. Service providers</h2>
          <p>Ryozan uses service providers for payment processing, email delivery, text messaging, hosting, storage, analytics and application operations. Providers receive only the information needed to perform their services and operate under their own privacy and security obligations.</p>
        </section>

        <section>
          <h2>7. Security and your choices</h2>
          <p>We use encrypted transmission, access controls, protected credentials and operational logging. No system can guarantee absolute security. You may disconnect Google Calendar from your artist account, revoke Ryozan access in your Google Account permissions, or contact us to request access, correction or deletion of eligible personal information.</p>
        </section>

        <section>
          <h2>8. Contact</h2>
          <p>Questions about this policy or Google data use go to the studio that operates this Ryozan install, through the portal&apos;s Courier inbox.</p>
        </section>
      </article>
    </AppProductShell>
  )
}

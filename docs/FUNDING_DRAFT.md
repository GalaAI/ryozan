# Funding page text (DRAFT)

> **Draft.** Not published anywhere. No GitHub Sponsors or Open Collective page exists for Ryozan yet. This is text to paste into one later, after review. Check the links and the roadmap items against [`ROADMAP.md`](../ROADMAP.md) before using it.

---

## Ryozan

Ryozan is a free, open-source shop operating system for tattoo studios and other artist businesses. It runs a studio's bookings, client messages, waivers, payments and flash in one web app. A crew of AI agents works alongside the staff. Each studio hosts its own install. The code is Apache-2.0.

Code: https://github.com/GalaAI/ryozan

### Who it is for

- Tattoo studios and other artist-run shops that want their own system instead of renting one.
- Artists, front desk staff and owners who need bookings, deposits, waivers and the client inbox in one place.
- People who can run a Docker install, or know someone who can.

### Why it exists

> *"I needed help managing my business so I built my shop's app and website from scratch. I taught myself how to develop, build and ship full stack applications using AI. What I didn't expect was to fall in love with the process of architecting orchestrated software and now am founding a tech company called Ark Omnitek made by a tattooer, built for artists and creators. I also wanted to build something that would help other tattooers like my dad. But if it didn't help mine, what good would that do? Once my analytics proved it worked on my shop first, I knew I could confidently give him something that works and put it out there in the world."*
> **Justin Walker**

### What your money pays for

Each item comes from the roadmap.

- **A public demo.** Hosting one demo install, with its database, so a studio can try Ryozan before installing it. The code already supports a demo install with invite-code sign-up and a shared demo account with monthly caps.
- **Model inference for the demo.** The agent crew calls a hosted model endpoint for every message. On a public demo that cost is paid by the project, not by the visitor. Web search for the agents is a paid API too.
- **Integration reviews.** Checking each optional integration (Stripe Connect, Google Calendar, Twilio SMS, email, Meta, Pinterest, Plaid, Clover, Mercury, ElevenLabs) on a clean install, and writing down the steps. Some of this needs paid test numbers, sending domains or provider accounts.
- **Maintainer time on the roadmap.** The next items are:
  - finish removing the old BTTCO name from the API contract and stored data, with a versioned change;
  - make the shop timezone a setting instead of a fixed value;
  - add a setting to switch off each optional extra, such as quests, the coin wallet and the club feed;
  - review code that nothing uses, and wire it in or remove it.

### What it does not promise

- No hosted service. Hosted multi-studio Ryozan is a later roadmap item and is not started.
- No dates. Roadmap items land when they are done.
- No support contract or uptime guarantee. Ryozan has one maintainer.
- No private features. Everything stays in the public, Apache-2.0 code.
- No say over the roadmap or over security fixes in exchange for money.

Thank you for helping keep Ryozan free for studios.

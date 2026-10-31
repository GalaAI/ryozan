# Security policy

## Reporting a vulnerability

Report vulnerabilities privately through GitHub private vulnerability reporting on the public repository, GalaAI/ryozan:

1. Open https://github.com/GalaAI/ryozan/security/advisories/new.
2. Fill in the report. Only you and the maintainer can see it.

Never open a public issue, pull request or discussion for a vulnerability. A public report puts every studio running Ryozan at risk before a fix exists.

### What to include

- What the issue is and what an attacker could do with it. For example: read another client's bookings or messages, act as a higher role, reach a secret, or run code on the server.
- Where it is: the endpoint, page, MCP tool or file, if you know it.
- The commit or date of `main` you tested against.
- Steps to reproduce, with the smallest setup that shows it. Say which role you were signed in as (`collector`, `assistant`, `artist`, `admin` or `founder`) and which optional integrations were configured.
- Any proof of concept, logs or screenshots. Leave out real client data and real keys.
- Whether you want to be credited, and under what name.

Test only against an install you run yourself. Do not test against a studio's live install or against other people's data.

## What to expect

Ryozan has one maintainer, so response times are modest.

- The maintainer aims to acknowledge a report within a week.
- You will get a first assessment once the issue is reproduced, and updates in the advisory thread as a fix is worked on.
- When a fix lands on `main`, the advisory is published. You are credited if you asked to be.

These are aims, not guarantees. If you hear nothing for two weeks, reply in the advisory thread.

## Supported versions

Only `main` is supported. Fixes land on `main` and nowhere else. There are no release branches or backports. Self-hosted installs should update to the latest `main`.

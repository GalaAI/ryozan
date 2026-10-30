# Candidate Preview — bounded side-port harness for BTTCO worktrees

How a BTTCO branch is run, inspected and torn down before it lands on the canonical
dev stack. This is the Ark 4GE Stage 3 (Metis) candidate pattern applied to BTTCO:
one canonical frontend, one canonical API, and short-lived candidates on a fixed
side-port range with a recorded owner and an expiry supervisor.

## Port boundaries

| Port | Owner | Role |
|---|---|---|
| 5173 | `bttco-dev` (compose profile `arkdev`, `.ark/scripts/bttco-dev.ps1`) | canonical frontend, explicitly assigned source and branch |
| 8801 | `bttco-dev` | canonical API |
| 5174 to 5179 | `scripts/bttco-candidate.ps1` | candidate previews, one worktree each |

5173 is refused by the launcher. Nothing outside 5174 to 5179 is a candidate.

## The launcher: `scripts/bttco-candidate.ps1`

This file delegates to `<ark-root>\.ark\scripts\bttco-candidate.ps1`, the same
owner the runtime gateway calls. Keep lifecycle logic in Ark. Do not copy it into
BTTCO. `scripts/start-dev.ps1 -PreferredAppPort 5174` also delegates to this owner;
`-TtlMinutes 1` requests a one-minute preview (default 60, maximum 60).

```
bttco-candidate.ps1 start   -Worktree <path> [-Port 5174] [-ApiTarget http://127.0.0.1:8801] [-TtlMinutes 60] [-Expose] [-Json]
bttco-candidate.ps1 status  [-Port 5174] [-Json]
bttco-candidate.ps1 probe   [-Port 5174] [-ApiTarget ...] [-Json]
bttco-candidate.ps1 extend  [-Port 5174] [-TtlMinutes 30]
bttco-candidate.ps1 stop    [-Port 5174]
```

Environment: `ARK_BTTCO_DEV_PROJECT_PATH` (canonical root), `ARK_BTTCO_API_TARGET`
(default API target), `ARK_BTTCO_CANDIDATE_WORKTREE` (default worktree),
`ARK_BTTCO_CANDIDATE_ROOTS` (extra allowed roots, `;` separated), `ARK_STATE_ROOT`
(state under `<root>\state\bttco-candidates`, default `<ark-root>\.ark\state\bttco-candidates`).

### Containment

`-Worktree` must be a linked Git worktree with the `GalaAI/BTTCO` origin under
`.ark\.arkhive\worktrees`, `<canonical>\.worktrees`, `<canonical>-worktrees`, or an
`ARK_BTTCO_CANDIDATE_ROOTS` entry. The canonical checkout itself is refused.

### Lifecycle

1. `start` refuses if the API target's `/health` is not serving (start `bttco-dev` first),
   refuses an occupied port or any recorded live candidate or supervisor. A live
   candidate without a listener still owns its receipt. Stop it explicitly first.
2. Vite starts in `<worktree>\app` with `VITE_API_TARGET=<ApiTarget>` on `--strictPort`.
3. An independently launched hidden supervisor (`supervise` action) polls every
   5 s, marks `exited` if Vite dies, kills the whole process tree at `expiresAt`
   (`expired`), and refreshes the probe every 30 s.
4. `stop` checks creation time and executable identity before terminating the owned
   candidate process tree and supervisor. It records `stopped` only after observed exit.

Lifecycle actions are serialized per port. Receipt replacement is atomic, corrupt
receipts are not treated as absent, and each supervisor follows only its launch ID.

TTL is 60 minutes maximum. `extend` pushes `expiresAt` but never past 60 minutes from now.

### Probe

`ready` = the app root answers 200. `connected` = `/health` through the candidate's own
proxy returns the same JSON body as `<ApiTarget>/health` directly. `probe` exits 2
when the candidate is not connected; inspect the JSON status for lifecycle state.

### State

`candidate-<port>.json` next to `candidate-<port>.log` in the state root:
`id, launchId, status (starting|ready|connected|exited|expired|stopped|failed), port, bind, worktree,
branch, headSha, dirty, apiTarget, localUrl, previewUrl, tailnetUrl, routes, pid,
processIdentity, supervisorPid, supervisorIdentity, startedAt, expiresAt, ttlMinutes, log, probe`. Pulse events go through
`scripts/pulse-dev-health.ps1` (source `CandidateLauncher`, mode `candidate:<port>`).

## Exposure to Axiom

The runtime gateway registers `bttco-candidate` (`.ark/apps/runtime-gateway/src/index.ts`,
`createBttcoCandidateRegistration`): endpoint `http://127.0.0.1:5174/` is the preview URL,
health evidence is `app-*`, `api-via-proxy-*`, `preview-port:5174`, lifecycle actions
`start`/`stop`/`restart` call the launcher. Read it at
`GET /v1/services/bttco-candidate` on the gateway (8820). `.claude/launch.json` carries a
`bttco-candidate` entry on 5174 next to `bttco` on 5173.

`-Expose` binds 0.0.0.0 and records `tailnetUrl` for a browser that is not on this machine
(`<machine>.<tailnet>.ts.net` is in the app's `allowedHosts`).

## Merge and teardown gate

A candidate exists until the operator has inspected it visually in a real browser and the
relevant tests pass. Follow the protected delivery path, stop the candidate, and
perform the explicit 5173 ownership handoff through `bttco-dev`. The candidate is
never promoted in place, and unrelated dirty work must be preserved.

## Historical branch example (`feat/booking-flow-canon-port`)

This branch originally needed its unmerged API changes on 8802. This is historical
context, not an instruction to start a second API for ordinary development:

```
<ark-root>\vessels\entities\bttco\api\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8802   # from the worktree api/, LOCAL_DEV_MODE=true
powershell -File scripts\bttco-candidate.ps1 start -Worktree <this worktree> -Port 5174 -ApiTarget http://127.0.0.1:8802 -Json
```

After merge the `-ApiTarget` flag goes away and the default 8801 applies.

# Scene → component map

Scope: `Portal Mobile v2.dc.html` (phone) and `BTTCO Portal One.dc.html`
(tablet/desktop). Everything else in the project is out of scope for this
package.

**UNRESOLVED** means: present in a scene, deliberately *not* translated,
because approximating it would be inventing design. Do not fill these in from
imagination — they need an extraction pass against the scene, or a decision.

---

## Assembled screens

The scene regions below are composed into ready screens, so no one has to
assemble them (or invent them) at the call site.

| Screen export | Covers |
|---|---|
| `PortalShell` | phone frame: scrolling page, agent FAB, tab bar, sheet positioning |
| `HomeScreen` | home for both roles: header, session pass, tiles, Gala line, today list |
| `BookingSheet` | staff quick-book: name, time, sesh type, artist, commit |
| `AgentChatSheet` | agent header, transcript, curated starters, composer slot |
| `DesktopShell` | tablet/desktop frame: nav rail, topbar, main, context pane |

Screens still **UNRESOLVED** (not assembled, because their parts are not
extracted): calendar, card table, quote sheet, settings sheet, aftercare board,
inbox, Gala workspace.

## Chrome

| Scene region | Scene | Component |
|---|---|---|
| Unified page header (title + avatar button) | mobile | `PageTitle` (+ `Avatar`) |
| Fixed command rail with top-edge content dissolve | mobile | `EdgeDissolveRail` |
| Home mono meta line ("SAT AUG 1 // 2 APPOINTMENTS") | mobile | `PageTitle` `meta` prop |
| Topbar (title, exit-preview pill, inbox, avatar) | desktop | `TopBar` + `Button` + `IconButton` + `Avatar` |
| Bottom tab bar (5 tabs, badges, agent hairline) | mobile | `TabBar` |
| Nav rail: gold trim, chevron cut, grain, vertical nav | desktop | `NavRail` |
| Rail vertical BTTCO wordmark | desktop | `Wordmark` |
| Rail medallion (dashed-ring profile) | desktop | `Avatar` `dashed` |
| Tiger mark at rail crown | desktop | `NavRail` `markSrc` prop |
| Account popover (identity, view-as, links, sign out) | both | `AccountMenu` (+ `Chip` for view-as) |
| Settings popover rows with right-aligned values | desktop | `AccountMenu` `rows[].value` |
| Grain film over panels | both | `GrainOverlay` |
| Tab-bar sweep animation | mobile | **UNRESOLVED** — animation, not structure. Belongs in the app's motion layer as a baked constant |

## Home

| Scene region | Scene | Component |
|---|---|---|
| Session pass with vertical 刺青 ticket stub | mobile | `SessionPass` `railVariant="stub"` |
| Session pass with date rail (Aug/01/Saturday) | desktop | `SessionPass` `railVariant="date"` |
| Pass ledger rows (artist, when, placement, balance) | both | `MetaList` (inside `SessionPass`) |
| "● Confirmed" status | both | `StatusText` |
| Pass primary CTA / "Message Justin" secondary | both | `Button` `accent` / `ghost` |
| Staff TODAY list (time, label, state, quick-book) | mobile | `ScheduleList` |
| Collector two-up tiles (Aftercare Day 3, Club) | mobile | `QuickTile` |
| Gala one-line greeting with emblem | mobile | `AgentMark` + text (no wrapper component — one line, not a pattern) |
| Section labels ("TODAY") | both | `SectionLabel` |

## Sheets and agents

| Scene region | Scene | Component |
|---|---|---|
| Bottom-sheet shell (scrim, accent hairline, header, close) | mobile | `Sheet` |
| Booking sheet fields (name, time/type/artist chips) | mobile | `Sheet` + `TextField` + `ChipGroup`/`Chip` + `Button` |
| Docked agent sheet, no scrim (Booky debrief) | mobile | `Sheet` `scrim={false}` |
| Agent chat line with notched bubble | mobile | `ChatBubble` |
| Agent FAB (glyph, glow, flip) | mobile | `AgentFab` |
| Agent emblem in sheet headers | both | `AgentMark` |
| Agent tool layer (Upload, Cards, Memory) | mobile | `AgentTools` |
| Card flip face, per-card shell identity | mobile | `ActionCardFace` driven by `AgentActionCard.shell` |
| Landed interactive action card | mobile | `ActionCard` |
| Agent-colored pointer, touch, and focus edge light | both | `AgentGlow` |
| Receipt block after a card settles | mobile | `ReceiptBlock` |
| Settings sheet body (toggles incl. memory consent) | mobile | **UNRESOLVED** — needs a `Toggle` and a settings-row component; not extracted |
| Quote sheet (line items, totals, deposit math) | mobile | **UNRESOLVED** — money surface, Xar-owned, double-gated. Extract with the card shells, not before |
| Agent composer + upload dock | both | `AgentChatSheet` composer slot + `AgentTools`; desktop dock composition remains unresolved |
| Day debrief brief/flag body | mobile | **UNRESOLVED** |

## Panels not yet extracted

| Scene region | Scene | Status |
|---|---|---|
| Inbox panel (rows, unread, thread preview) | desktop | **UNRESOLVED** — panel shell reads as `Panel`; the row anatomy was not extracted |
| Aftercare board (14-day tappable grid) | desktop | `ActionCard` with `heal-chart`; desktop board composition remains unresolved |
| Calendar / day grid | both | **UNRESOLVED** |
| Gala chat workspace (right pane) | desktop | **UNRESOLVED** |
| Card table and six action-card shells | both | `AgentTools` + `ActionCardFace` + `ActionCard`. The shell is selected per card, not inferred from agent or reduced to a colour prop |
| Session-pass barcode / check-in code | both | **UNRESOLVED** — server-generated (Code 128, `/barcode/{ref}.png`). A component would be a placeholder |

## Deliberately excluded

Not design — authoring tooling, and it must never ship (handoff §0):

- Motion desk, aesthetics desk, left/right docks, spotlight picker, transport bar
- `pom.desk.v1` / `pom.desk.saved.v1` persistence
- Any `dp*` symbol

Also excluded per scope: `Motion Labs/`, `Card Audit*`, `Booking Emails`,
`Orchestra Prototype`, `Maestro Console`, `_archive/`, `uploads/`.

# 06 — Frontend (Next.js) (v2)

Screens map to the wireframes in `specs/wireframes/` (PNG + static HTML). **Wireframes were not regenerated for v2**; the deltas below are authoritative, and where a wireframe and a requirement disagree, the requirement wins (log it in `DECISIONS.md`).

**Wireframe rules:** match layout, hierarchy and labels; sample values and `[bracketed]` text are placeholders; tokens: bg `#F6F6F4`, ink `#1A1A1A`, border `#D4D4CF`, accent `#2457C5`, pass `#1F4FB5`, fail/warn `#B4530A`; IBM Plex Sans / Mono.

| Screen | Wireframe |
|---|---|
| UI-1 Agents | `wireframes/01-agents.*` |
| UI-2 Studio | `wireframes/02-studio.*` |
| UI-3 Versions & Deploy | `wireframes/03-versions-deploy.*` |
| UI-4 Share chat | `wireframes/04-share-chat.*` |
| UI-5 Issues & Evals | `wireframes/05-evals.*` |
| UI-6 Improve | `wireframes/06-improve.*` |

## Wireframe deltas (v2)

| Screen | Change |
|---|---|
| UI-1 | Column "Needs review" shows **open issues** count (was feedback count). |
| UI-2 / UI-3 | Version list is P0 inside Studio (a "Versions" section); the standalone Versions & Deploy screen is P1. Health banner reflects `isolated` (IS-2). |
| UI-4 | **Left sidebar**: "New chat" button + this visitor's chats (title, relative time, 👎 marker, spinner if running); selected chat highlighted. On phones the sidebar collapses behind a "Chats" button. In the transcript, a **divider** "Agent updated to v5" where `version` changes between assistant messages. **Files expired** notice when applicable. "Agent is busy" message on 429. |
| UI-5 | Top section becomes **Issues** (replaces "From feedback"): title, lesson, axis, signal count by type, reproduction status ("Reproduced: 3 of 3 variants fail" / "Not reproduced" / "Checking…"), ready badge, actions: Confirm · Dismiss · **Improve** (enabled when ready) · Improve anyway (asks for a note). Case table gains **Split** column (Improve / Benchmark) and Origin. Policy panel gains: min target gain, per-axis floors, max cost increase, min signals, cooldown. |
| UI-6 | Header shows **target axis** and **trigger** ("From issue: Refunds counted as revenue"). Score strip: **Target axis** base→cand, **Benchmark (target axis)** base→cand, Overall, **Cost per run** base→cand (%). Verdict banner lists reasons and warnings. Case table groups by split, then axis. Footer shows "This proposal cost $X to produce" (or "—"). |
| New UI-7 (owner) | Conversations list (`/agents/[id]/conversations`): title, visitor short id, last activity, runs, 👎; click opens a read-only transcript with traces. P0 as a simple table. |

## Requirements

| ID | P | Requirement |
|---|---|---|
| UI-R1 | P0 | Show `tool.call`/`tool.result` live and the answer on `message.final`; show `version.changed` as a divider. |
| UI-R2 | P0 | Share page has no owner navigation or config. |
| UI-R3 | P0 | 👎 opens the correction box; empty correction allowed. |
| UI-R4 | P0 | Poll every 1.5 s while evals, reproduction or proposals run; show progress (`k / n`). |
| UI-R5 | P0 | IF `meets_policy` is false, THEN buttons read "Accept anyway" and require a note. |
| UI-R6 | P0 | Pass/fail uses symbol + color. |
| UI-R7 | P0 | (**changed v2**) IF `isolated` is false, owner screens show "Sandbox: local, not isolated". |
| UI-R8 | P0 | Every async action has loading and error states. |
| UI-R9 | P0 | (**v2**) Share page loads the visitor's chat list on open, selects the most recent chat (or an empty new chat if none), and switching chats never interrupts a running run in another chat. |
| UI-R10 | P0 | (**v2**) Cost values of `null` render as "—", never as $0. |
| UI-R11 | P0 | (**v2**) "Improve" is disabled with a tooltip explaining why when: issue not ready, a proposal is open, or cooldown is active (with remaining time). |

## UX acceptance (validated in V-1)
- First-time owner reaches a working share link in ≤ 3 min.
- From the Improve screen alone, a first-time user can say what changed, what improved, whether it generalizes (benchmark), what it costs, and whether it's safe to accept.
- A teammate can find yesterday's chat and continue it.

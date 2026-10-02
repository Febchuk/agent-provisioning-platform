# Spec changelog — v1 → v2

v2 folds in four design discussions held after the v1 handoff:

1. **Isolation** — what stops an agent from touching other agents, other users, secrets or the host; build vs. sandbox provider.
2. **Hallucination and cost** — guards must justify their cost to serve; cost becomes a measured quantity.
3. **Measuring improvement** — ground truth, benchmarks, thresholds, one dimension at a time.
4. **Triggering improvement** — detection (does the live agent need work?) is separate from evaluation (is this change better?); one thumbs-down is not enough on its own.

Plus one product requirement: **chat history per deployed agent** (switch between old chats, start new ones).

Rationale for every change is in `08-design-decisions.md` (D-31 … D-45). Superseded decisions are marked there, not deleted.

## New spec files

| File | Contents |
|---|---|
| `09-isolation.md` | Threat model; secrets never in sandbox; backend choice (provider spike vs Docker); hardening; limits; visitor isolation |
| `10-quality-and-cost.md` | Usage and cost tracking; `grounded` check; cost in verdict/policy; reviewer (P1) with on/off comparison |
| `11-signals-and-triggers.md` | Signals → issues; reproduction with synthetic variants; trigger policy; cooldown; LLM monitor (P1) |

## Changed requirements (by ID)

| ID | Change | Why (decision) |
|---|---|---|
| DM-2 | **Rewritten.** Version is chosen **per run** (deployed version at the time of the message), not pinned per conversation. A `version.changed` event marks the switch. | D-37 (supersedes D-13) |
| DM-4 | `hidden` + `parent_case_id` replaced by `split` (`improve` · `benchmark`) + `origin` | D-33 |
| SB-1…SB-5 | Moved to `09` as IS-*; Docker specifics only apply if Docker is the chosen backend | D-31 |
| SB-4 | Idle reaping keeps the conversation's **workspace** (volume / provider persistence) for a retention window | D-38 |
| CD-4 | Share endpoints now also enforce visitor ownership of conversations | D-36 |
| Chat endpoints | Message POST moved to `/share/{slug}/conversations/{cid}/messages`; new list/open endpoints; owner `GET /agents/{id}/conversations`; new CH-1…CH-7 | D-36 |
| CD-6 | Unchanged per conversation; plus per-agent and global concurrency limits (IS-6) | D-31 |
| CD-9 / API keys | Still P1, now **first on the cut list** | time budget |
| EV-2 | Feedback no longer drafts a case directly; it becomes a **signal** that joins an **issue** (11). Cases are drafted from a confirmed issue. | D-40 |
| EV-9 | Verdict rewritten: target axis gain, benchmark gate, per-axis floors, cost limit (see `04` §Verdict) | D-34, D-35, D-32 |
| EV-10 | **Removed** (siblings). Replaced by reproduction variants (SG-6…SG-9) that land in the benchmark split | D-33, D-41 |
| EV-11 | `cases_for_improver()` returns only `split = improve` cases on the **target axis** | D-33, D-34 |
| Policy | `min_avg_improvement_pct` → `min_target_gain_pct`; new `axis_floors`, `max_cost_increase_pct`, trigger fields | D-34, D-35, D-32, D-42 |
| IM-1, IM-2 | Proposals take a `target_axis` (from an issue or chosen by owner); targets are improve-split failures on that axis | D-34 |
| IM-16 | Multi-candidate stays P1 (unchanged) | — |
| New IM-17…IM-19 | One open proposal per agent; cooldown; trigger provenance recorded | D-43 |
| UI-4 | Share page gains a chat-history sidebar, version divider, files-expired notice | D-36…D-38 |
| UI-5 | "From feedback" inbox becomes the **Issues** inbox | D-40 |
| UI-6 | Improve screen adds target axis, benchmark row, cost row, trigger source | D-32…D-35 |
| UI-2b, UI-3 | Playground stays P1; Versions becomes a P0 list (standalone screen P1) | time budget |

## New requirement families

`IS-*` (09), `QC-*` (10), `SG-*` (11), `CH-*` (chat history, in 03).

## Wireframes

Not regenerated for v2. Where a wireframe and a v2 requirement disagree, **the requirement wins** (already the rule in `06`). Deltas are listed in `06` §Wireframe deltas.

## Migration notes (if you already built parts of v1)

| If you built… | Do this |
|---|---|
| Conversation pinned to `version_id` | Keep the column as "version at creation" (informational). Look up the deployed version when each run starts; store it on `runs.version_id` (already there) and on the assistant message. Emit `version.changed`. |
| `eval_cases.hidden` / `parent_case_id` | Add `split` (default `improve`) and `origin`; migrate `hidden = true` → `split = benchmark, origin = variant`. Drop the old columns when convenient. |
| `/feedback/{id}/draft-case` | Keep it, but call it from the issue flow (`/issues/{id}/draft-case`). Feedback POST now also creates a signal. |
| Policy `min_avg_improvement_pct` | Rename to `min_target_gain_pct`; keep overall average as a reported (non-gating) number. |
| Docker sandbox | Still valid as a backend. Add IS-4 hardening flags and the secrets test (IS-1). If the provider spike passes, add the adapter and switch `SANDBOX_BACKEND`. |
| Verdict tests | Replace the 5-row table with the v2 table in `04` (8 rows). |

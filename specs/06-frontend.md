# 06 — Frontend (Next.js)

Screens map 1:1 to the wireframes canvas. Owner screens share a header + tabs: Configure · Evals · Improve · Versions & Deploy.

## In scope
- Six screens below, SSE trace rendering, polling for eval/proposal progress, minimal Tailwind styling consistent with the wireframes.

## Out of scope
- Auth/login, dark mode, mobile-optimized owner screens (share page must work on phone), drag-and-drop file upload, editing eval case history, charts.

## Screens

| # | Route | P | Must show | Primary action |
|---|---|---|---|---|
| UI-1 | `/` Agents | P0 | Table: name, live version, chats (7d), feedback to review, eval score. New-agent panel with 3 templates | Create → Studio |
| UI-2 | `/agents/[id]` Studio | P0 | Name, model (read-only), system prompt, learned guidelines (with "added for case X" on hover), tools, files, limits | Save as new version · Save & deploy |
| UI-2b | Studio playground | P1 | Chat on the draft with step trace | Send |
| UI-3 | `/agents/[id]/versions` | P0 | Version list: number, source, change note, score, live badge; share link with copy | Deploy / Roll back |
| UI-4 | `/share/[slug]` | P0 | Chat, collapsible "Worked for N steps" trace per turn, 👍/👎, correction box on 👎, files in chat (P1) | Send · Send feedback |
| UI-5 | `/agents/[id]/evals` | P0 | Feedback inbox with drafted case (check, axis, rubric), case table (axis, check, pinned, 3-trial dots), policy panel | Add as test case · Run evals · Save policy |
| UI-6 | `/agents/[id]/improve` | P0 | Status while running (step names), score strip, verdict banner with reasons, guideline diff with lint flags, improver reasoning (diagnoses), case-by-case matrix, generalization row (P1), change note | Accept · Accept & deploy · Reject |

## Requirements

| ID | P | Requirement |
|---|---|---|
| UI-R1 | P0 | WHEN a teammate sends a message, THE UI SHALL show each `tool.call`/`tool.result` as it arrives and the final answer on `message.final`. |
| UI-R2 | P0 | THE share page SHALL NOT render any owner navigation or config. |
| UI-R3 | P0 | WHEN 👎 is clicked, THE UI SHALL open the correction box; feedback can be sent with an empty correction. |
| UI-R4 | P0 | WHILE an eval run or proposal is in progress, THE UI SHALL poll every 1.5 s and show progress (`k / n trials`). |
| UI-R5 | P0 | IF `verdict.meets_policy` is false, THEN the Accept buttons SHALL be labeled "Accept anyway" and require the note field. |
| UI-R6 | P0 | Pass/fail SHALL be shown with both symbol (✓/✗) and color (never color alone). |
| UI-R7 | P0 | IF `GET /health` reports `local-unsafe`, THEN owner screens SHALL show a banner "Sandbox: local, not isolated". |
| UI-R8 | P0 | Every async action SHALL show loading and error states (no silent failures). |

## UX acceptance (validated in V-1, not unit-tested)

- A first-time user reaches a working share link in ≤ 3 minutes without help.
- A first-time user can explain, from the Improve screen alone, *what changed, what got better, what got worse, and whether it is safe to accept*.

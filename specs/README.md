# Agent Platform — Specs

Spec-driven build for the Brainbase on-site (5 hours). These specs are the source of truth.
Code follows specs; when a decision changes, update the spec first, then the code.

## Files

| File | What it defines |
|---|---|
| `00-overview.md` | Problem, users, goals, **scope (in / out)**, assumptions, constraints, glossary, milestones |
| `01-data-model.md` | Tables, fields, invariants (the contract every other spec uses) |
| `02-agent-runtime.md` | Runner (ReAct loop), tools, sandbox, model gateway |
| `03-chat-and-deploy.md` | Versions, deploy pointer, conversations, SSE, share page, API keys |
| `04-feedback-and-evals.md` | Feedback, case drafting, checks, eval executor, policy, verdict |
| `05-improver.md` | Triage → diagnose → propose → lint → evaluate → verdict |
| `06-frontend.md` | Screens (maps 1:1 to the wireframes), states, UX acceptance criteria |
| `07-verification-and-validation.md` | Test strategy, traceability matrix, demo validation script |
| `tasks.md` | Ordered, time-boxed implementation tasks with "done when" checks |

## Conventions (spec best practices used here)

- **Requirement IDs** — every requirement has a stable ID (`RT-3`, `EV-12`). Tests, tasks and commits reference IDs.
- **EARS phrasing** — requirements use *"WHEN <trigger>, THE SYSTEM SHALL <response>"* (event), *"WHILE <state>, …"* (state), *"IF <unwanted condition>, THEN …"* (error), or plain *"THE SYSTEM SHALL …"* (always). One behavior per requirement; each is testable.
- **Priority** — `P0` must exist for the demo. `P1` if time allows. Anything else is listed under *Out of scope* — not "later", explicitly not built.
- **Acceptance criteria** — Given / When / Then, written so a test can be derived mechanically.
- **Verification vs validation** — *verification* = we built it right (automated tests per requirement). *Validation* = we built the right thing (demo script run by a fresh user, judged against the interview criteria). See `07`.
- **Decisions log** — any deviation from a spec during the build goes in `DECISIONS.md` (one line: date, spec ID, what changed, why). This feeds the "what I cut and why" part of the walkthrough.

## How to use with an AI coding agent

1. Work one task from `tasks.md` at a time. Give the agent the task plus the spec sections it references.
2. Ask for tests first (from the acceptance criteria), then implementation.
3. A task is done only when its **Done when** check passes.
4. Do not let the agent add anything listed under *Out of scope*.

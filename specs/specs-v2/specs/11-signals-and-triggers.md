# 11 — Signals and Triggers (Detection)

## Purpose

Separate two questions the v1 design blurred:

| Question | Mechanism | Spec |
|---|---|---|
| **Detection:** does the live agent need improving? | Signals → issues → reproduction → trigger policy | this file |
| **Evaluation:** is this proposed change better? | Eval runs → verdict → owner decision | `04`, `05` |

One thumbs-down is not, by itself, evidence. Evidence is **repeated signals** or a **reproduced failure**.

## Concepts

- **Signal** — one observation that something may be wrong (a thumbs-down, a failed run, an ungrounded number).
- **Issue** — a cluster of signals that share an underlying lesson ("refunds are being counted as revenue"). The owner works with issues, not individual signals.
- **Reproduction** — generate synthetic variants of the failing question and run the current deployed version on them. If they fail too, the problem is general and real.

## Signal types

| Type | Source | Cost | P |
|---|---|---|---|
| `thumbs_down` | Teammate feedback (with optional correction) | Free | P0 |
| `run_failed`, `step_limit` | Runner status | Free | P0 |
| `ungrounded_numbers` | QC-10 | Free | P0 |
| `tool_error_streak` | ≥ 3 consecutive tool errors in one run | Free | P1 |
| `rephrase` | Same visitor re-asks within 2 turns with high word overlap | Cheap heuristic | P1 |
| `monitor` | Sampled LLM review of live runs | Model tokens | P1, off by default |

## Data model additions

`signals`: `id, agent_id, conversation_id, run_id, version_id, type, payload JSON, issue_id?, created_at`

`issues`: `id, agent_id, title, lesson, axis, status, signal_count, repro JSON?, trigger_source?, created_at, updated_at`

Issue `status`: `open` → `reproducing` → `reproduced` | `not_reproduced` → `confirmed` (owner) → `in_proposal` → `resolved` | `dismissed`.

## Endpoints

| Method | Path | P | Purpose |
|---|---|---|---|
| GET | `/agents/{id}/issues?status=` | P0 | Issues with signal counts by type, reproduction result, `ready` flag and reason if not ready |
| GET | `/issues/{id}` | P0 | Issue + signals (with links to runs/conversations) + variant cases |
| POST | `/issues/{id}/confirm` | P0 | Owner confirms: variants → active benchmark cases (SG-9); returns drafted improve case (SG-10) |
| POST | `/issues/{id}/dismiss` | P0 | Dismiss; variants → `dismissed` |
| POST | `/issues/{id}/reproduce` | P0 | Re-run reproduction (e.g. after a redeploy) |
| POST | `/issues/{id}/draft-case` | P0 | See `04` EV-2 |
| POST | `/signals/{id}/move` | P1 | Move a signal to another issue (fix clustering mistakes) |

Proposals are started from an issue via `POST /agents/{id}/proposals {issue_id}` (`05`).

## Requirements

| ID | P | Requirement |
|---|---|---|
| SG-1 | P0 | WHEN feedback with rating `down` is posted, THE SYSTEM SHALL create a `thumbs_down` signal (payload: correction, question, answer). Thumbs-up is stored as feedback only. |
| SG-2 | P0 | WHEN a run ends `failed` or `step_limit`, or has ungrounded claims, THE SYSTEM SHALL create the matching signal. |
| SG-3 | P0 | WHEN a `thumbs_down` signal is created, THE SYSTEM SHALL ask the LLM to either attach it to one of the agent's open issues (given their titles and lessons) or create a new issue with `{title, lesson, axis}`. |
| SG-4 | P0 | Runtime signals (`run_failed`, `step_limit`, `ungrounded_numbers`) SHALL be grouped deterministically: one issue per (signal type, version), no LLM call. |
| SG-5 | P0 | `issues.signal_count` SHALL equal the number of attached signals. |
| SG-6 | P0 | WHEN a `thumbs_down` issue is created, THE SYSTEM SHALL start reproduction automatically: generate 3 variants of the original question (same intent, different period/grouping/wording) with an `llm_judge` rubric derived from the lesson. |
| SG-7 | P0 | Reproduction SHALL run each variant **once** on the currently deployed version (fresh sandbox, temperature 0) and record `repro = {variants: [case_ids], failed: k, total: 3}`. |
| SG-8 | P0 | The issue SHALL be `reproduced` IF `failed ≥ 2`, else `not_reproduced`. |
| SG-9 | P0 | Variants SHALL be stored as eval cases with `split = benchmark`, `origin = variant`, `status = draft`; they become `active` when the owner confirms the issue. |
| SG-10 | P0 | WHEN the owner confirms an issue, THE SYSTEM SHALL draft one `improve`-split case from the original conversation (EV-2) for the owner to accept. |
| SG-11 | P0 | An issue SHALL be **ready to improve** IF it is `confirmed` AND (`signal_count ≥ policy.min_signals` OR status was `reproduced`). |
| SG-12 | P0 | THE SYSTEM SHALL NOT start proposals automatically. Ready issues show "Improve"; the owner starts it. |
| SG-13 | P0 | The owner MAY start Improve on an issue that is not ready ("Improve anyway"); the proposal records `trigger_source = manual_override`. |
| SG-14 | P1 | Rate trigger: an issue is also ready IF its signals cover ≥ `policy.min_signal_rate_pct` of runs in the last 7 days. |
| SG-15 | P1 | Monitor: WHERE `policy.monitor_sample_pct > 0`, THE SYSTEM SHALL review that fraction of live runs with an LLM against the version's prompt and guidelines; findings create `monitor` signals only (never cases), and monitor cost is shown on the Issues screen. |

## Policy fields (added to `policies`)

| Field | Default | Note |
|---|---|---|
| `min_signals` | 3 | Signals needed for an un-reproduced issue to be ready |
| `cooldown_hours` | 24 | Between accepted proposals (IM-18). Demo seed sets 0 |
| `max_open_proposals` | 1 | IM-17 |
| `min_signal_rate_pct` | 5 | P1 |
| `monitor_sample_pct` | 0 | P1, off by default |

## Acceptance criteria

- **AC-SG-a** Given two thumbs-downs about refunds on different questions, when the second arrives, then FakeLLM is asked with the first issue in context and the signal attaches to it (`signal_count = 2`).
- **AC-SG-b** Given reproduction results `[fail, fail, pass]`, then status `reproduced`; `[fail, pass, pass]` → `not_reproduced`.
- **AC-SG-c** Given `min_signals = 3`, a confirmed issue with 1 signal and `not_reproduced` is not ready; the same issue `reproduced` is ready.
- **AC-SG-d** Given three `step_limit` runs on v4, then exactly one issue exists for (`step_limit`, v4) with `signal_count = 3`, and no LLM call was made.
- **AC-SG-e** No code path creates a proposal without an owner request (grep + API test).

## Demo consequence

The demo has a single thumbs-down. Reproduction is what makes acting on it legitimate: *"One report, but 3 of 3 variants fail too, so it's real."*

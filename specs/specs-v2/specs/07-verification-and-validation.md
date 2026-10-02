# 07 — Verification and Validation (v2)

- **Verification** — *built right?* Automated per requirement; the default suite runs in < 60 s with no network (`FakeLLM`, `LocalSandbox`).
- **Validation** — *built the right thing?* A fresh-user run-through against the goals in `00` and the interview criteria.

## 1. Test layers

| Layer | Tooling | Network / sandbox | Covers |
|---|---|---|---|
| Unit | pytest | No / none | Tools, verdict, lint, ops, checks (incl. `numeric`, `grounded`), grounding extraction, cost math, title trimming, trigger readiness |
| Runner | pytest + FakeLLM + LocalSandbox | No / local | Loop, limits, errors, events, usage, `version.changed` |
| API contract | pytest + httpx | No / local | Endpoints, 400/401/404/409/429, share redaction, visitor isolation, proposal rate limits |
| Integration | pytest `-m integration` | Yes / chosen backend | Isolation (IS-*), real model answers, workspace persistence |
| Smoke scripts | `scripts/` | Yes / chosen backend | Milestone gates |
| E2E improvement | `scripts/e2e_improve.py` | Yes / chosen backend | AC-IM-h |

## 2. Milestone gates (v2)

| Gate | Command | Passes when |
|---|---|---|
| M0 | `pytest -q` + spike note in `DECISIONS.md` | Suite runs; `SANDBOX_BACKEND` chosen with measured timings |
| M1 | `python scripts/smoke_runner.py` + `pytest -m integration tests/integration/test_isolation.py` | Real model answers a row-count question; AC-IS-a, b, e (if Docker) pass |
| M2 | `bash scripts/smoke_chat.sh` | AC-CD-f (two chats, persistence, reopen) |
| M3 | `pytest -q tests/test_verdict.py tests/test_signals.py tests/test_checks.py` + `python scripts/smoke_issue.py` | Verdict table (8 rows) passes; a scripted 👎 creates an issue that reproduces on seed v1 |
| M4 | `python scripts/e2e_improve.py` | AC-IM-h |
| M5 | Validation V-1 | All V-1 checks pass |

## 3. Traceability

| Req | Test / check |
|---|---|
| DM-1, DM-5 | `test_models.py::test_versions_immutable`, `::test_version_numbers_monotonic` |
| RT-1 | `test_runner.py::test_system_prompt_includes_guidelines` |
| RT-2, RT-3, RT-7 | `test_runner.py::test_tool_loop_and_events` (AC-RT-a) |
| RT-4 | `test_runner.py::test_step_limit` (AC-RT-b) |
| RT-5 | `test_runner.py::test_model_error_retry_then_fail` |
| RT-6, RT-9 | `test_runner.py::test_unknown_tool_returns_error`, `::test_only_enabled_tools_exposed` |
| TL-1…TL-7 | `test_tools.py` (AC-RT-c, d, e; truncation) |
| MG-2, MG-5 | `test_architecture.py::test_no_provider_imports_outside_llm`, `::test_api_key_only_read_in_llm` |
| CD-1…CD-10 | `test_api_agents.py`, `test_api_chat.py` (AC-CD-a…e), smoke M2 (AC-CD-f) |
| EV-1…EV-8 | `test_evals.py` (AC-EV-a, b, d, e) |
| IM-3…IM-15 | `test_improver.py`, `test_ops.py`, `test_lint.py`, `test_api_proposals.py` (AC-IM-a…g) |
| DM-2 (rewritten), RT-10, CH-5 | `test_runner.py::test_version_changed_event`, `test_api_chat.py::test_reopened_chat_uses_deployed_version` (AC-RT-i, AC-CD-b) |
| DM-4, EV-11 | `test_evals.py::test_cases_for_improver_split_and_axis` (AC-EV-f) |
| DM-6, SG-5 | `test_signals.py::test_signal_count_consistent` |
| MG-4, QC-1…QC-5 | `test_cost.py` (AC-QC-a, c), AC-RT-h |
| QC-6…QC-10 | `test_grounding.py` (AC-QC-b, d) |
| IS-1 | `tests/integration/test_isolation.py::test_no_secrets_in_sandbox` (AC-IS-a) — **also run against LocalSandbox in unit suite** |
| IS-3, IS-4, IS-8 | `test_isolation.py::test_workspaces_isolated`, `::test_no_network`, `::test_fork_bomb_contained` (AC-IS-b, e) |
| IS-6 | `test_api_chat.py::test_agent_concurrency_cap_429` (AC-IS-d) |
| IS-7, CH-1, CH-2 | `test_api_chat.py::test_visitor_isolation_404`, `::test_history_scoped_and_sorted` (AC-IS-c, AC-CH-a, b) |
| IS-9 | `test_evals.py::test_trial_sandbox_destroyed_on_error` (AC-IS-f) |
| IS-10, SB-4, CH-6 | `test_isolation.py::test_workspace_survives_idle_stop` (AC-IS-g, AC-CH-d), `test_api_chat.py::test_expired_workspace_note` (AC-CH-e) |
| CH-3 | `test_titles.py` (AC-CH-c) |
| EV-9 (rewritten) | `test_verdict.py` — 8-row table (AC-EV-c) |
| `numeric`, `all_of` | `test_checks.py` (AC-EV-g) |
| SG-1…SG-13 | `test_signals.py` (AC-SG-a…e) |
| IM-1, IM-2 | `test_improver.py::test_target_axis_selection`, `::test_targets_improve_split_only` |
| IM-17…IM-19 | `test_api_proposals.py::test_one_open_proposal`, `::test_cooldown_and_override`, `::test_issue_resolved_on_accept` (AC-IM-i, j, k) |
| UI-R9…R11 | V-1 checklist |

## 4. Validation V-1 (v2, ~12 min)

| Step | Task given | Pass criterion | Validates |
|---|---|---|---|
| 1 | "Create a data analyst agent and send me a link." | Share link in ≤ 3 min | G-1 |
| 2 | (teammate) "Ask what total revenue was in Q3." | Steps stream; answer appears | G-2 |
| 3 | "Refunds shouldn't count. Tell the agent." | 👎 + correction without help | UI-R3 |
| 4 | (teammate) "Start a new chat, ask something else, then go back to the first chat." | Finds and reopens the first chat | CH-*, UI-R9 |
| 5 | (owner) "Is there a real problem, or is this one person?" | Finds the issue; reads "Reproduced: k of 3" and explains it | SG-*, G-3 |
| 6 | "Make the agent learn from it." | Confirms issue, adds case, starts Improve on the right axis | UI-5 |
| 7 | "Is it safe to accept? Does it generalize? What does it cost?" | Correctly reads target gain, benchmark row, regressions, cost | G-4, UI-6 |
| 8 | "Ship it; as the teammate, ask again **in the same chat**." | Divider "Agent updated to vN" then a correct answer | DM-2, G-3 |
| 9 | "Undo that." | Rolls back | DM-1 |

V-1 passes if steps 1–8 succeed and step 7's explanation is correct.

## 5. Demo-day reliability checks

- [ ] `python scripts/seed_demo.py --reset` restores v1, clears issues/proposals, sets `cooldown_hours = 0`.
- [ ] AC-IM-h run 3× (target 3/3; if < 2/3, pre-record a proposal and say so).
- [ ] Reproduction on seed v1 run 3× (target: reproduced each time).
- [ ] Offline fallback works: `SANDBOX_BACKEND=local` and a recorded proposal can be loaded.
- [ ] `GET /health` shows the intended backend and `isolated: true`.

## 6. Known weak spots (state them in Q&A)

- Judge noise in `llm_judge` checks and in reproduction variants (variants run once each).
- Small benchmark per axis; a single case moves rates a lot. Warnings shown when coverage is empty.
- Visitor identity is a cookie: no cross-device history.
- If Docker is the backend: containers are not a strong security boundary; no disk quota.
- SQLite write contention under parallel trials.
- Cost is only as accurate as `MODEL_PRICES`.

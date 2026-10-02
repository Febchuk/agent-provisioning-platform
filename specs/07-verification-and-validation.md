# 07 — Verification and Validation

- **Verification** — *did we build it right?* Automated, per requirement, runs in < 60 s without network (FakeLLM) plus a small set of integration checks with Docker and the real model.
- **Validation** — *did we build the right thing?* A scripted run-through by someone who hasn't seen the product, judged against the goals in `00` and the interview criteria.

## 1. Test layers

| Layer | Tooling | Network / Docker | Covers |
|---|---|---|---|
| Unit | pytest | No / No | Tools (path guard, edit_file), verdict, lint, op application, check evaluators, prompt builders |
| Runner | pytest + `FakeLLM` + `LocalSandbox` | No / No | Loop, limits, errors, events |
| API contract | pytest + httpx `AsyncClient` | No / No | Endpoints, status codes, 409/400/401, share redaction |
| Integration | pytest `-m integration` | Yes / Yes | Real Docker sandbox, real model answer, network blocked |
| Smoke scripts | bash/python in `scripts/` | Yes / Yes | Milestone exit gates |
| End-to-end improvement | `scripts/e2e_improve.py` | Yes / Yes | AC-IM-h on the seed agent |

**Determinism:** unit/runner/contract tests use `FakeLLM` and `LocalSandbox`; no test depends on model output except the integration and e2e layers, which are run manually at milestone gates.

## 2. Milestone exit gates

| Gate | Command | Passes when |
|---|---|---|
| M0 | `pytest -q` | Collects and passes (placeholder test + FakeLLM test) |
| M1 | `python scripts/smoke_runner.py` | Real model answers "How many orders are in orders.csv?" correctly, using `bash`; trace printed |
| M2 | `bash scripts/smoke_chat.sh` | AC-CD-f (two dependent turns) passes |
| M3 | `pytest -q tests/test_verdict.py tests/test_evals.py` and `python scripts/smoke_evals.py` | Verdict table passes; seed agent v1 eval run completes with expected failures |
| M4 | `python scripts/e2e_improve.py` | Candidate fixes the refund case; verdict printed; accept deploys |
| M5 | Validation V-1 | All V-1 checks pass |

## 3. Traceability matrix (requirement → verification)

| Req | Test / check |
|---|---|
| DM-1, DM-2, DM-5 | `test_models.py::test_versions_immutable`, `::test_conversation_pins_version`, `::test_version_numbers_monotonic` |
| RT-1 | `test_runner.py::test_system_prompt_includes_guidelines` |
| RT-2, RT-3, RT-7 | `test_runner.py::test_tool_loop_and_events` (AC-RT-a) |
| RT-4 | `test_runner.py::test_step_limit` (AC-RT-b) |
| RT-5 | `test_runner.py::test_model_error_retry_then_fail` |
| RT-6, RT-9 | `test_runner.py::test_unknown_tool_returns_error`, `::test_only_enabled_tools_exposed` |
| TL-1…TL-7 | `test_tools.py` (AC-RT-c, d, e; truncation) |
| SB-1, SB-2 | `tests/integration/test_docker_sandbox.py` (AC-RT-f, g) |
| SB-5 | `test_health.py::test_fallback_mode_reported` |
| MG-2 | `test_architecture.py::test_no_provider_imports_outside_llm` (grep imports) |
| CD-1…CD-10 | `test_api_agents.py`, `test_api_chat.py` (AC-CD-a…e), smoke M2 (AC-CD-f) |
| EV-1…EV-8 | `test_evals.py` (AC-EV-a, b, d, e) |
| EV-9 | `test_verdict.py` (AC-EV-c table, parametrized) |
| EV-10 | Manual inspection in V-1 (siblings read as same-kind questions) |
| EV-11, IM-3 | `test_improver.py::test_prompt_excludes_rubric_and_hidden` (AC-IM-a, b; AC-EV-f) |
| IM-2, IM-6 | `test_improver.py::test_flaky_skipped`, `::test_no_ops_no_candidate` |
| IM-5 | `test_improver.py::test_invalid_json_retry_then_fail` (AC-IM-f) |
| IM-7, IM-10, IM-15 | `test_ops.py` (AC-IM-e, budget, addresses recorded) |
| IM-8, IM-9, IM-11 | `test_lint.py` (AC-IM-c, d) |
| IM-12 | `scripts/e2e_improve.py` (AC-IM-h) |
| IM-13, IM-14 | `test_api_proposals.py::test_override_requires_note`, `::test_accept_and_deploy` (AC-IM-g) |
| UI-R1…UI-R8 | V-1 checklist (manual) |
| G-1…G-5 | V-1 checklist |

## 4. Validation V-1 (fresh-user run-through, ~10 min, at 4:30)

Ask an interviewer (or anyone who hasn't seen it) to do this with no guidance beyond the task card. Record time and every moment of hesitation in `DECISIONS.md`.

| Step | Task given | Pass criterion | Validates |
|---|---|---|---|
| 1 | "Create a data analyst agent and send me a link to it." | Share link works in ≤ 3 min | G-1, UI-1, UI-3 |
| 2 | (as teammate) "Ask what total revenue was in Q3." | Steps stream, answer appears | G-2, UI-R1 |
| 3 | "That's wrong — refunds shouldn't count. Tell the agent." | 👎 + correction sent without help | UI-R3 |
| 4 | (as owner) "Make the agent learn from that." | Finds feedback, adds case, starts Improve | G-3, UI-5 |
| 5 | "Is this change safe to accept? Why?" | User correctly states fixed / regressed / policy verdict | G-4, UI-6 |
| 6 | "Ship it, then ask the question again as a teammate." | New chat answers correctly | G-3 end-to-end |
| 7 | "Can you undo that?" | Rolls back on Versions | DM-1, CD-3 |

**V-1 passes** if steps 1–6 succeed and step 5's explanation is correct. Any failure → fix UI copy/layout first (cheapest), then logic.

## 5. Demo-day reliability checks (at 4:45)

- [ ] Seed reset script restores agent to v1 with known failures: `python scripts/seed_demo.py --reset`
- [ ] AC-IM-h run 3× in a row; fix rate recorded (target 3/3; if < 2/3, pre-record a proposal and say so)
- [ ] Backup: a recorded proposal JSON can be loaded if the model endpoint is down
- [ ] `GET /health` shows `docker` sandbox mode

## 6. Known weak spots (state these honestly in Q&A)

- LLM-judge checks inherit judge noise; mitigated by 3 trials, not eliminated.
- 8 visible cases is a small eval set; avg_delta swings 12.5 pts per case.
- Container sandbox is not a security boundary.
- SQLite write contention under parallel eval trials; first thing to change at scale.

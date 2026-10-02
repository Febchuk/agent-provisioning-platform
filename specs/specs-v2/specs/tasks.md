# Tasks (v2)

Work top to bottom. Each task: **Refs** → **Build** → **Done when**. Tests first from the acceptance criteria. Don't start a milestone until the previous gate passes (`07` §2). Tasks marked **(v2)** are new or changed.

## M0 — Foundation (0:00–0:25)

- [ ] **T0.1 Scaffold** — `backend/`, `frontend/`, `scripts/`, `specs/`, `DECISIONS.md`. **Done when** `/health` serves and `pytest -q` passes.
- [ ] **T0.2 Seed data** — deterministic `orders.csv` (~2,000 rows; ~8% `refunded`; ~1% missing dates) + printed ground truth. **Done when** two runs are identical.
- [ ] **T0.3 LLM gateway + FakeLLM with usage** — MG-1, MG-2, MG-4, MG-5. **Done when** FakeLLM test passes and one real call returns usage.
- [ ] **T0.4 Sandbox interface + provider spike (v2)** — SB-6, `09` §Backend decision. Ask interviewers about a provider account first. Spike: create → upload → exec pandas → write → stop/resume → read → destroy, with timings. **Done when** `DECISIONS.md` records the backend choice and timings. **Hard stop at 15 min**: if not passing, choose Docker.

## M1 — Runtime (0:25–1:20)

- [ ] **T1.1 Tools on LocalSandbox** — TL-1…TL-7. **Done when** `test_tools.py` passes.
- [ ] **T1.2 Runner + usage + version.changed** — RT-1…RT-11, QC-1, QC-2. **Done when** `test_runner.py`, `test_cost.py` pass.
- [ ] **T1.3 Chosen backend (v2)** — provider adapter (IS-5) **or** hardened Docker (IS-4 + image). IS-1, IS-3, IS-8, IS-9. **Done when** AC-IS-a, b (and e if Docker) pass.
- [ ] **T1.4 Grounding analysis (v2)** — QC-6…QC-8. **Done when** `test_grounding.py` passes.
- [ ] **T1.5 Smoke** — `scripts/smoke_runner.py`. **Gate M1.**

## M2 — Agents, versions, chat with history (1:20–2:00)

- [ ] **T2.1 Models** — `01` v2. **Done when** DM tests pass.
- [ ] **T2.2 Agents/versions/deploy/templates/files** — CD-1…CD-3, CD-10.
- [ ] **T2.3 Visitor cookie + conversation list/open (v2)** — CH-1…CH-4, IS-7. **Done when** AC-CH-a, b, c pass.
- [ ] **T2.4 Messages, background runs, SSE, per-turn version, workspace resume (v2)** — CD-4…CD-8, CH-5…CH-7, DM-2, SB-4, IS-6, IS-10. **Done when** AC-CD-b…e, AC-CH-d, e, AC-IS-d pass and `smoke_chat.sh` passes. **Gate M2.**

## M3 — Detection and measurement (2:00–3:00)

- [ ] **T3.1 Feedback → signals → issues (v2)** — EV-1, SG-1…SG-5, QC-10. **Done when** AC-SG-a, d pass.
- [ ] **T3.2 Reproduction (v2)** — SG-6…SG-9. **Done when** AC-SG-b passes (FakeLLM) and seed v1 reproduces the refund issue for real.
- [ ] **T3.3 Issue confirm/dismiss/ready + draft case (v2)** — SG-10…SG-13, EV-2…EV-4. **Done when** AC-SG-c, e and AC-EV-a pass.
- [ ] **T3.4 Checks (v2)** — `contains`, `numeric`, `python_assert`, `llm_judge`, `grounded`, `all_of`. **Done when** each has pass/fail tests; AC-EV-d, g pass.
- [ ] **T3.5 Executor** — EV-5…EV-8, EV-12, IS-9. **Done when** AC-EV-b, e, AC-IS-f pass.
- [ ] **T3.6 Policy + verdict (v2)** — EV-9, QC-5. **Done when** the 8-row verdict table passes.
- [ ] **T3.7 Seed cases (v2)** — seed agent v1 with ~6 active cases across axes and both splits (incl. pinned safety and tool-use cases; at least 2 accuracy benchmark cases with `numeric` + `grounded`). The refund issue arrives live via 👎 during the demo. **Gate M3** via `smoke_issue.py`.

## M4 — Improver (3:00–3:40)

- [ ] **T4.1 Prompt builder + target axis (v2)** — IM-1…IM-4, EV-11. **Done when** AC-IM-a, b pass.
- [ ] **T4.2 Ops + budget + lint** — IM-7…IM-11, IM-15. **Done when** AC-IM-c, d, e pass.
- [ ] **T4.3 Pipeline + proposals + rate limits (v2)** — IM-5, IM-6, IM-12…IM-14, IM-17…IM-19. **Done when** AC-IM-f, g, i, j, k pass and `e2e_improve.py` passes (AC-IM-h). **Gate M4.**

## M5 — Frontend (3:40–4:30) — in demo order

- [ ] **T5.1 Share page with history (v2)** — UI-4 deltas, UI-R1…R3, R6, R9.
- [ ] **T5.2 Issues & Evals (v2)** — UI-5 deltas, UI-R4, R11.
- [ ] **T5.3 Improve (v2)** — UI-6 deltas, UI-R5, R10.
- [ ] **T5.4 Agents + Studio (with version list) + Conversations (v2)** — UI-1, UI-2, UI-7, UI-R7, R8.
- [ ] **T5.5 Validation V-1.** **Gate M5.**

## M6 — 4:30–5:00

- [ ] **T6.1 Demo reliability checklist** (`07` §5) — always.
- [ ] T6.2 P1 items in `00` order, only if everything above passed.

## Cut list (decide at 3:00, in this order)

1. All P1 (ground-truth import, reviewer, monitor, API keys, standalone Versions, playground).
2. Owner Conversations screen (UI-7) → link to raw transcript JSON.
3. `python_assert` check → seed cases use `numeric`, `contains`, `llm_judge`, `grounded`.
4. Runtime signals except `step_limit` (keep `thumbs_down`).
5. Cooldown (keep one-open-proposal).
6. Studio editing → read-only; changes only via proposals.

**Never cut:** IS-1 (secrets), visitor isolation, reproduction, benchmark gate, cost display. They carry the v2 story.

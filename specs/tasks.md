# Tasks

Work top to bottom. Each task: **Refs** (spec IDs) → **Build** → **Done when** (verification). Tests first from the referenced acceptance criteria. Don't start a milestone until the previous gate passes (`07` §2).

## M0 — Foundation (0:00–0:20)

- [ ] **T0.1 Repo scaffold** — `backend/` (FastAPI, SQLModel, pytest), `frontend/` (Next.js + Tailwind), `scripts/`, `specs/`, `DECISIONS.md`. **Done when** `uvicorn app.main:app` serves `/health` and `pytest -q` passes.
- [ ] **T0.2 Seed data** — `scripts/seed_demo.py`: deterministic `orders.csv` (~2,000 rows; columns `order_id, created_at, region, product, list_price, amount, status`; ~8% `refunded`; ~1% missing `created_at`). Prints ground-truth answers (Q3 revenue excl./incl. refunds, top 3 products, rows). **Done when** two runs produce identical files and answers.
- [ ] **T0.3 LLM gateway + FakeLLM** — Refs MG-1, MG-2. **Done when** `test_fake_llm_scripted_order` passes and a one-line real call succeeds.

## M1 — Runtime (0:20–1:30)

- [ ] **T1.1 Tools on LocalSandbox** — Refs TL-1…TL-7. **Done when** `test_tools.py` passes.
- [ ] **T1.2 Runner** — Refs RT-1…RT-9. **Done when** `test_runner.py` passes (FakeLLM).
- [ ] **T1.3 DockerSandbox + image** — Refs SB-1, SB-2, SB-3, SB-5. `sandbox/Dockerfile`. **Done when** integration tests AC-RT-f, AC-RT-g pass.
- [ ] **T1.4 Smoke** — `scripts/smoke_runner.py`. **Gate M1.**

## M2 — Agents, versions, chat (1:30–2:15)

- [ ] **T2.1 Models + migrations** — Refs `01`. **Done when** DM tests pass.
- [ ] **T2.2 Agents/versions/deploy/templates/files API** — Refs CD-1, CD-2, CD-3, CD-10. **Done when** AC-CD-a, b pass.
- [ ] **T2.3 Conversations + background runs + SSE replay** — Refs CD-4…CD-8. **Done when** AC-CD-c, d, e pass and `smoke_chat.sh` passes. **Gate M2.**

## M3 — Feedback and evals (2:15–3:15)

- [ ] **T3.1 Feedback + draft-case** — Refs EV-1…EV-4. **Done when** AC-EV-a passes.
- [ ] **T3.2 Checks** — `contains`, `python_assert`, `llm_judge`. **Done when** each has a pass and a fail unit test; AC-EV-d passes.
- [ ] **T3.3 Eval executor** — Refs EV-5…EV-8. **Done when** AC-EV-b, e pass.
- [ ] **T3.4 Policy + verdict** — Refs EV-9. **Done when** `test_verdict.py` (5-row table) passes.
- [ ] **T3.5 Seed cases** — Add 6 starter cases to the seed agent (incl. pinned "top 3 products", "won't delete orders.csv"; refund case arrives via feedback during demo). **Gate M3** via `smoke_evals.py`.

## M4 — Improver (3:15–4:00)

- [ ] **T4.1 Prompt builder + `cases_for_improver`** — Refs IM-3, IM-4, EV-11. **Done when** AC-IM-a, b pass.
- [ ] **T4.2 Ops application + budget** — Refs IM-7, IM-10, IM-15. **Done when** AC-IM-e passes.
- [ ] **T4.3 Literal lint** — Refs IM-8, IM-9, IM-11. **Done when** AC-IM-c, d pass.
- [ ] **T4.4 Pipeline + proposal endpoints** — Refs IM-1, IM-2, IM-5, IM-6, IM-12…IM-14. **Done when** AC-IM-f, g pass and `e2e_improve.py` passes. **Gate M4.**

## M5 — Frontend (4:00–4:30)

Order = demo order, so a partial UI still demos.

- [ ] **T5.1 Share page** — UI-4, UI-R1…R3, R6.
- [ ] **T5.2 Evals page** — UI-5, UI-R4.
- [ ] **T5.3 Improve page** — UI-6, UI-R5.
- [ ] **T5.4 Agents + Studio + Versions** — UI-1, UI-2, UI-3, UI-R7, UI-R8.
- [ ] **T5.5 Validation V-1.** **Gate M5.**

## M6 — Only if M5 passed (4:30–5:00)

- [ ] T6.1 Sibling cases (EV-10) + generalization row
- [ ] T6.2 API keys + `/v1` endpoint (CD-9)
- [ ] T6.3 Demo reliability checklist (`07` §5) — **do this even if T6.1/T6.2 are skipped**

## Cut list if behind (decide at 3:15)

1. Drop all of M6 except T6.3.
2. Studio becomes read-only; edits happen only via proposals.
3. Versions screen becomes a list on Studio.
4. `python_assert` check deferred; seed cases use `contains` + `llm_judge`.

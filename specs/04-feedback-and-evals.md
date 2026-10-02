# 04 — Feedback, Eval Cases, Eval Executor, Policy, Verdict

## Purpose

Turn usage into measurement. Feedback becomes eval cases (with human confirmation); versions are scored on cases; proposals are judged against an owner-set policy.

## In scope
- Feedback capture, LLM-drafted cases, manual cases, three check types, N-trial executor, policy, deterministic verdict, sibling cases (P1).

## Out of scope
- Statistical significance testing, per-teammate weighting of feedback, auto-confirming cases, editing a case's history in the UI (re-create instead), eval result caching across identical versions.

## Endpoints

| Method | Path | P | Purpose |
|---|---|---|---|
| POST | `/runs/{id}/feedback` | P0 | `{rating, correction?}` (public; used by share page) |
| GET | `/agents/{id}/feedback?status=new` | P0 | Inbox |
| POST | `/feedback/{id}/draft-case` | P0 | LLM drafts a case → returns draft (status `draft`) |
| POST | `/agents/{id}/cases` | P0 | Create/confirm a case (manual or from draft) |
| PATCH | `/cases/{id}` | P0 | `pinned`, `axis`, `name`, `status` |
| POST | `/feedback/{id}/dismiss` | P0 | |
| GET | `/agents/{id}/cases` | P0 | Visible cases + latest results on deployed version |
| POST | `/agents/{id}/eval-runs` | P0 | `{version_id}` → runs all active cases |
| GET | `/eval-runs/{id}` | P0 | Status + per-case trial results |
| GET/PUT | `/agents/{id}/policy` | P0 | |

## Checks (`check_spec`)

| Type | Spec | Passes when |
|---|---|---|
| `contains` | `{"all": ["412,380"], "none": ["refunded orders included"]}` | Final answer (case-insensitive, commas and `$` stripped for numbers) contains every `all` item and no `none` item |
| `python_assert` | `{"code": "import pandas as pd; df = pd.read_csv('monthly_revenue.csv'); assert len(df) == 12"}` | Code exits 0 when run **in the trial's sandbox after the turn**, with the final answer available at `/workspace/.final_answer.txt` |
| `llm_judge` | `{"rubric": "Passes if the answer excludes orders with status 'refunded' …"}` | Judge model (temperature 0, JSON mode) returns `{"pass": true, "reason": "…"}`; judge sees rubric + user question + final answer only, **not** the trace |

## Requirements

| ID | P | Requirement |
|---|---|---|
| EV-1 | P0 | WHEN feedback is posted, THE SYSTEM SHALL store it with status `new` and link it to the run and conversation. |
| EV-2 | P0 | WHEN draft-case is called, THE SYSTEM SHALL build `history` = the conversation messages up to and including the user message of that run, and ask the LLM for `{name, axis, check_type: "llm_judge", rubric}` derived from the correction. |
| EV-3 | P0 | The drafted rubric SHALL describe a **property** of a correct answer, not a specific value, unless the correction states a value. (Prompt requirement; verified by inspection in V-1.) |
| EV-4 | P0 | THE SYSTEM SHALL NOT add a drafted case to evaluations until the owner confirms it (status `active`). |
| EV-5 | P0 | WHEN an eval run starts, THE SYSTEM SHALL run every `active` case (visible and hidden) `trials_per_case` times on the given version, each trial in a fresh sandbox, with at most 4 trials concurrently. |
| EV-6 | P0 | A case SHALL pass on a version IF passing trials ≥ `pass_threshold`. |
| EV-7 | P0 | A case SHALL be marked **flaky** on a version IF it has ≥1 passing and ≥1 failing trial. |
| EV-8 | P0 | IF a trial's run fails (`failed`/`step_limit`) or the check errors, THEN the trial SHALL count as failed with the reason recorded. |
| EV-9 | P0 | THE SYSTEM SHALL compute the verdict with a pure function `compute_verdict(base_results, cand_results, cases, policy)` (below). |
| EV-10 | P1 | WHEN a case created from feedback is confirmed, THE SYSTEM SHALL generate 2 hidden sibling cases: new user questions of the same kind (different time range / grouping / wording) with an `llm_judge` rubric testing the same property. |
| EV-11 | P0 | Cases with `hidden = true` SHALL be excluded from any data passed to the improver (enforced in one function, `cases_for_improver()`). |

## Verdict (pure function, fully unit-tested)

Inputs: per-case pass booleans for base and candidate, case metadata, policy. Visible = `hidden == false`.

```
fixed       = visible cases failing on base, passing on cand
regressed   = visible cases passing on base, failing on cand
avg_delta   = (pass_count(cand) - pass_count(base)) / n_visible * 100      # percentage points
by_axis     = {axis: count(regressed with that axis)}
axis_breach = {axis: n for axis, n in by_axis if n > policy.max_regressions.get(axis, 0)}
pinned_reg  = [c for c in regressed if c.pinned]
meets_policy = avg_delta >= policy.min_avg_improvement_pct and not axis_breach and not pinned_reg
generalization = {base: pass_count(hidden, base), cand: pass_count(hidden, cand), total: n_hidden}   # P1, reported, not gating
reasons = human-readable strings for every failed condition
```

Output JSON stored on the proposal: `{base_score, cand_score, n_visible, fixed, regressed, unchanged, avg_delta, by_axis, axis_breach, pinned_regressed, meets_policy, reasons, generalization}`.

## Acceptance criteria

- **AC-EV-a** Given feedback on run r in a 3-turn conversation where r answered turn 2, then the drafted case history ends with the user message of turn 2.
- **AC-EV-b** Given trials `[T, F, T]` and threshold 2, then the case passes and is flaky.
- **AC-EV-c** Verdict table tests:

| Case | Setup | Expected |
|---|---|---|
| 1 | base 5/8, cand 7/8, 1 format regression, limit 1 | `meets_policy = true` |
| 2 | same but regression is pinned | `false`, reason mentions pinned case name |
| 3 | 1 accuracy regression, limit 0 | `false`, `axis_breach = {"accuracy": 1}` |
| 4 | avg_delta = 0 | `false`, reason mentions minimum improvement |
| 5 | axis not in policy, 1 regression | `false` (missing axis defaults to 0) |

- **AC-EV-d** Given a `python_assert` that reads `.final_answer.txt`, then it sees the trial's final answer.
- **AC-EV-e** Given 8 cases × 3 trials, then no more than 4 sandboxes exist at once (assert via sandbox factory counter in test).
- **AC-EV-f** Given hidden cases exist, then `cases_for_improver()` returns none of them.

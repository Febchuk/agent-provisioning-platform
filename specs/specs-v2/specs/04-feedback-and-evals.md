# 04 — Eval Cases, Checks, Executor, Policy, Verdict (v2)

## Purpose

Measure whether a version is better. v2 separates this cleanly from **detection** (11): feedback now becomes a *signal* on an *issue*; confirmed issues produce cases. This spec covers cases, ground truth, checks, the eval executor and the verdict.

## Key v2 changes
- **Two splits** (D-33): `improve` cases — the improver may see their failures; `benchmark` cases — never shown to the improver, used only to judge. Replaces hidden siblings.
- **Creator ground truth** (D-33): `numeric` check with tolerance; ground-truth CSV import into the benchmark.
- **One target axis per proposal** (D-34); per-axis **floors** (D-35); **cost** limit (D-32).

## In scope
Cases (draft → active), four origins, five check types, ground-truth import, N-trial executor, policy, deterministic verdict, compare runs.

## Out of scope
Statistical significance tests, weighting feedback by teammate, editing case history in the UI, caching eval results across identical versions, automatic case confirmation.

## Endpoints

| Method | Path | P | Purpose |
|---|---|---|---|
| POST | `/runs/{id}/feedback` | P0 | `{rating, correction?}`; 👎 also creates a signal (SG-1) |
| GET | `/agents/{id}/issues` | P0 | See `11` |
| POST | `/issues/{id}/confirm` · `/dismiss` | P0 | See `11` |
| POST | `/issues/{id}/draft-case` | P0 | Draft an `improve` case from the issue's original conversation (EV-2) |
| POST | `/agents/{id}/cases` | P0 | Create / confirm a case (any origin) |
| PATCH | `/cases/{id}` | P0 | `pinned`, `axis`, `name`, `split`, `status` |
| POST | `/agents/{id}/cases/import` | P1 | **v2** Ground-truth CSV: `question, expected, tolerance?, axis?` → `numeric` (or `contains` if non-numeric) cases, `split = benchmark`, `origin = ground_truth`, `status = active` |
| GET | `/agents/{id}/cases` | P0 | Cases + latest results on the deployed version |
| POST | `/agents/{id}/eval-runs` | P0 | `{version_id, purpose}` |
| GET | `/eval-runs/{id}` | P0 | Status, progress, per-case trials, cost |
| GET/PUT | `/agents/{id}/policy` | P0 | |

## Checks (`check_spec`)

| Type | Spec | Passes when |
|---|---|---|
| `contains` | `{"all": [...], "none": [...]}` | Final answer (case-insensitive; `$` and `,` stripped) contains every `all` and no `none` item |
| `numeric` (**v2**) | `{"expected": 412380.0, "abs_tol": 1, "rel_tol": 0.001}` | Some numeric claim in the answer (QC-6 extraction) is within tolerance of `expected` |
| `python_assert` | `{"code": "..."}` | Code exits 0 in the trial's sandbox after the turn; final answer at `/workspace/.final_answer.txt` |
| `llm_judge` | `{"rubric": "..."}` | Judge (temperature 0, JSON) returns `{"pass": true}`; sees rubric + question + answer only |
| `grounded` (**v2**) | `{}` | Zero ungrounded numeric claims (QC-9) |

A case may combine checks: `check_spec` may be `{"all_of": [spec, spec]}`; it passes only if every sub-check passes (P0 — needed so a ground-truth case can also require grounding).

## Requirements

| ID | P | Requirement |
|---|---|---|
| EV-1 | P0 | Feedback is stored with its run and conversation; 👎 creates a signal (SG-1). |
| EV-2 | P0 | (**changed v2**) WHEN `draft-case` is called on a confirmed issue, THE SYSTEM SHALL build `history` from the issue's originating conversation up to and including the user message of the flagged run, and ask the LLM for `{name, rubric}` describing a **property** of a correct answer; axis = issue axis; `split = improve`, `origin = feedback`. |
| EV-3 | P0 | Drafted rubrics describe a property, not a value, unless the correction states a value. |
| EV-4 | P0 | Drafted cases are not evaluated until the owner confirms them (`active`). |
| EV-5 | P0 | An eval run executes every `active` case (both splits) `trials_per_case` times, fresh sandbox per trial, ≤ 4 concurrent trials, within IS-6 caps. |
| EV-6 | P0 | A case passes IF passing trials ≥ `pass_threshold`. |
| EV-7 | P0 | A case is **flaky** on a version IF it has ≥ 1 passing and ≥ 1 failing trial. |
| EV-8 | P0 | Run failure or check error counts as a failed trial with reason. |
| EV-9 | P0 | (**changed v2**) The verdict is computed by the pure function below. |
| EV-10 | — | **Removed v2** (siblings). Replaced by reproduction variants (SG-6…SG-9). |
| EV-11 | P0 | (**changed v2**) `cases_for_improver(agent, target_axis)` SHALL return only `active`, `split = improve` cases whose `axis = target_axis`. It is the only function that supplies cases to the improver. |
| EV-12 | P0 | (**v2**) Eval runs record `cost_usd` and per-trial average cost/latency (QC-3). |
| EV-13 | P1 | (**v2**) Compare run: evaluate two versions (or one version with reviewer on/off, QC-14) and show the standard verdict view without a proposal. |

## Verdict (pure function, fully unit-tested)

Inputs: per-case pass booleans for base and candidate, case metadata (axis, split, pinned), per-run average cost for each, `target_axis`, policy.

```
rate(set, v)        = passes(set, v) / |set| * 100          # undefined if |set| = 0

all                 = active cases (both splits)
T                   = cases in `all` with axis == target_axis
T_bench             = cases in T with split == benchmark

target_gain         = rate(T, cand) - rate(T, base)
bench_target_delta  = rate(T_bench, cand) - rate(T_bench, base)     # None if T_bench empty
fixed / regressed   = over `all`, as in v1
by_axis_regressions = {axis: count(regressed with axis)}
axis_breach         = {a: n for a, n in by_axis_regressions if n > policy.max_regressions.get(a, 0)}
floor_breach        = {a: rate(axis a, cand) for a in policy.axis_floors if rate(axis a, cand) < floor}
pinned_regressed    = [c in regressed if c.pinned]
cost_delta_pct      = (cand_avg_cost - base_avg_cost) / base_avg_cost * 100     # None if any cost is None

meets_policy =
      target_gain >= policy.min_target_gain_pct
  and (bench_target_delta is None or bench_target_delta >= 0)      # generalization gate
  and not axis_breach and not floor_breach and not pinned_regressed
  and (cost_delta_pct is None or cost_delta_pct <= policy.max_cost_increase_pct)

warnings: "No benchmark cases on <axis>: generalization not measured" when T_bench is empty;
          "Cost not measured" when cost_delta_pct is None.
reported (non-gating): overall score base→cand, per-axis rates, per-split scores.
```

Output JSON: `{target_axis, target_gain, bench_target_delta, overall: {base, cand}, by_axis: {...}, by_split: {...}, fixed, regressed, axis_breach, floor_breach, pinned_regressed, cost: {base, cand, delta_pct}, meets_policy, reasons, warnings}`.

## Acceptance criteria

- **AC-EV-a** Drafted case history ends with the user message of the flagged run (3-turn conversation, flag on turn 2).
- **AC-EV-b** Trials `[T, F, T]`, threshold 2 → passes and flaky.
- **AC-EV-c** Verdict table (target axis = accuracy unless stated):

| # | Setup | Expected |
|---|---|---|
| 1 | Target gain +25, bench delta +50, 1 format regression (limit 1), cost +10% | `meets_policy = true` |
| 2 | As 1, but the regressed case is pinned | false — pinned |
| 3 | 1 accuracy regression (limit 0) | false — `axis_breach` |
| 4 | Target gain +5 (min 10) | false — target gain |
| 5 | Improve-split target cases fixed, benchmark target delta −50 | false — generalization gate (**overfitting caught**) |
| 6 | One of 3 safety cases regresses (limit 0); safety rate 67% (floor 100) | false — `axis_breach` and `floor_breach` |
| 7 | Cost +40% (limit 25) | false — cost |
| 8 | No benchmark cases on target axis, otherwise passing | true, with warning |

- **AC-EV-d** `python_assert` sees `.final_answer.txt`.
- **AC-EV-e** 8 cases × 3 trials → never more than 4 live trial sandboxes.
- **AC-EV-f** `cases_for_improver` never returns benchmark cases or cases from other axes.
- **AC-EV-g** `numeric`: expected 412380, abs_tol 1; answer "$412,380" passes, "$412,000" fails; `all_of [numeric, grounded]` fails if the number wasn't computed.
- **AC-EV-h** (P1) Ground-truth import of 5 rows creates 5 active benchmark cases.

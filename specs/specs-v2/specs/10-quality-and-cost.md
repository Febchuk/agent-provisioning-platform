# 10 — Quality and Cost

## Purpose

Treat **cost to serve** as a first-class measurement next to quality, so every quality mechanism (prompt changes, a grounding check, a reviewer model, a cheaper model) has to show what it buys. Principle (D-32): *extra cost is fine when the evals show what it improves.*

## In scope
- Token, cost and latency tracking per run, per eval run, per proposal.
- A zero-model-cost hallucination guard for numeric answers (`grounded`).
- Cost as a policy limit in the verdict.
- Reviewer model (P1), justified by an on/off eval comparison.

## Out of scope
Billing, budgets/quotas per teammate, caching to reduce cost, automatic model routing.

## Requirements — usage and cost

| ID | P | Requirement |
|---|---|---|
| QC-1 | P0 | THE SYSTEM SHALL record per run: `prompt_tokens`, `completion_tokens`, `model_calls`, `latency_ms`, `cost_usd` (summed over all model calls, including reviewer and judge calls attributed to that run). |
| QC-2 | P0 | `cost_usd` SHALL be computed from `MODEL_PRICES` (env JSON: `{model: {input_per_mtok, output_per_mtok}}`); IF a model has no price, THEN cost is `null` and the UI shows "—" (never a guessed number). |
| QC-3 | P0 | Eval runs SHALL report average cost and latency **per case trial** and total cost. |
| QC-4 | P0 | Proposals SHALL report: base vs candidate average cost per trial, the percentage change, and the total cost spent producing the proposal (improver + evals). |
| QC-5 | P0 | The verdict SHALL fail IF candidate average cost per trial exceeds base by more than `policy.max_cost_increase_pct` (default 25). |

## Requirements — grounding (hallucination guard)

The common hallucination for a data agent is a number it never computed. Checking that is plain code.

| ID | P | Requirement |
|---|---|---|
| QC-6 | P0 | THE SYSTEM SHALL extract numeric claims from a final answer: tokens with ≥ 3 significant digits or a decimal point, after stripping `$`, `,`, `%`; years 1900–2100 standing alone are ignored. |
| QC-7 | P0 | A numeric claim SHALL be **grounded** IF some number in the run's tool outputs equals it after rounding the tool number to the claim's precision. |
| QC-8 | P0 | THE SYSTEM SHALL store `runs.grounding = {claims, ungrounded: [...]}` after every run. |
| QC-9 | P0 | Check type `grounded` SHALL pass IF the trial's final answer has zero ungrounded claims. |
| QC-10 | P0 | IF a live run has ungrounded claims, THEN THE SYSTEM SHALL emit a signal of type `ungrounded_numbers` (11). |
| QC-11 | P1 | The share page SHALL show a small "contains a number the agent didn't compute" notice on such answers. |

## Requirements — reviewer (P1)

| ID | P | Requirement |
|---|---|---|
| QC-12 | P1 | A version MAY enable `reviewer = {enabled, model}`. WHEN enabled, after the agent's final answer the reviewer receives the user question, tool outputs (truncated) and answer, and returns `{ok, issues[]}`. |
| QC-13 | P1 | IF `ok = false`, THEN THE SYSTEM SHALL give the agent one revision turn with the reviewer's issues; at most one revision per run. |
| QC-14 | P1 | THE SYSTEM SHALL offer "Compare with reviewer off/on": two eval runs of the same version differing only in the reviewer flag, shown with the standard verdict view (score change **and** cost change). |

## Acceptance criteria

- **AC-QC-a** Given FakeLLM responses with usage `{100, 20}` twice and a price table, then run `cost_usd` equals the computed value; with no price entry, `cost_usd is None`.
- **AC-QC-b** Grounding: tool output `412380.4567`, answer "$412,380" → grounded. Answer "$415,000" → ungrounded. Answer "in 2026" → no claim.
- **AC-QC-c** Verdict: base avg cost 0.010, candidate 0.013, limit 25% → fails with reason naming cost (+30%).
- **AC-QC-d** A live run with an ungrounded claim produces exactly one `ungrounded_numbers` signal.

## Talking points (not built)
- A smaller open-source model plus reviewer vs a larger model alone is answered by QC-14 run across two versions; that's the experiment to run with the GPU model.

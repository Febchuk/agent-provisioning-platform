# 05 — Improver (v2)

## Purpose

Propose a new version that fixes consistent failures **on one target axis** by generalizing a lesson, then prove it on cases the improver never saw. Nothing is deployed without the owner.

## What changed in v2
- Proposals have a **target axis** (from an issue, or chosen by the owner). Other axes are guardrails (D-34).
- The improver sees only **improve-split** failures on that axis; the **benchmark split** judges generalization (D-33). Hidden siblings are gone.
- Proposals are started by the owner from a **ready issue** or manually; one open proposal at a time; cooldown between accepted proposals (D-42, D-43).
- Proposals report what they cost (D-32).

## Threats and guards

| Threat | Guard | Req |
|---|---|---|
| Memorizing the case | Literal lint; benchmark gate on the target axis | IM-8, EV-9 (row 5) |
| Writing to the test | Improver never sees checks or benchmark cases | IM-3, EV-11 |
| Chasing noise | Flaky cases skipped; issues need repeated signals or reproduction | IM-2, SG-11 |
| Fixing everything at once (conflicting edits) | One target axis per proposal | IM-1 |
| Prompt bloat | ≤ 3 ops, size budget | IM-10 |
| Churn | One open proposal; cooldown | IM-17, IM-18 |
| Quality bought with hidden cost | Cost limit in verdict | QC-5 |

## In scope
Single-candidate pipeline: base eval → targets → diagnose+propose → apply ops → lint → candidate eval → verdict.

## Out of scope (P0)
System-prompt edits by the improver; multiple candidates (P1, IM-16); consolidation proposals; learning from thumbs-up; changing tools, model or reviewer settings; auto-starting proposals.

## Endpoints

| Method | Path | P | Purpose |
|---|---|---|---|
| POST | `/agents/{id}/proposals` | P0 | `{issue_id?, target_axis?, override_note?}` → `{proposal_id}`; 409 if one is open; 429 during cooldown unless `override_note` |
| GET | `/proposals/{id}` | P0 | Status (+ step name), ops, diff, lint, diagnoses, verdict, cost |
| POST | `/proposals/{id}/accept` | P0 | `{deploy: bool, note?}` |
| POST | `/proposals/{id}/reject` | P0 | `{note?}` |

## Pipeline

```
0 Start      target_axis = issue.axis | request.target_axis | axis with most consistent improve-split failures
1 Base eval  reuse a base run for the deployed version + current active case set, else run one
2 Targets    cases_for_improver(agent, target_axis) failing 0/N on base; flaky → skipped
3 Propose    one LLM call → diagnoses, ops, skipped (schema below)
4 Apply      ops → guidelines → candidate version (source = proposal)
5 Lint       deterministic; violating ops dropped and recorded
6 Cand eval  same case set (both splits, all axes), same trials
7 Verdict    compute_verdict(..., target_axis) → status ready
```

## Requirements

| ID | P | Requirement |
|---|---|---|
| IM-1 | P0 | (**changed v2**) A proposal SHALL have exactly one `target_axis`, taken from the issue if given, else the request, else the axis with the most improve-split cases failing 0/N on base. |
| IM-2 | P0 | (**changed v2**) Targets are `cases_for_improver(agent, target_axis)` that fail all trials on base. Flaky failing cases go to `skipped` with reason `flaky`. IF there are no targets, THEN the proposal ends `ready` with "nothing consistent to fix on <axis>". |
| IM-3 | P0 | Improver input per target: case history, run trace (tool calls + truncated outputs), final answer, the issue's lesson and the original correction text. NOT: `check_spec`, rubrics, expected values, benchmark cases, other axes' cases. |
| IM-4 | P0 | Improver input also includes current guidelines (with ids), the system prompt (read-only), the target axis, and the **names** of currently passing cases on all axes ("don't break these"). |
| IM-5 | P0 | JSON output per schema; on parse failure retry once with the error appended, then `failed`. |
| IM-6 | P0 | Zero ops → `ready`, no candidate, diagnoses shown ("no change recommended"). |
| IM-7 | P0 | Apply `add` / `replace` / `delete`; unknown `rule_id` → op dropped, recorded in `lint`. |
| IM-8 | P0 | Literal lint rejects an op whose text contains (a) a number of ≥ 2 digits, a year, a quarter token or a month name that also appears in the target case's history, the correction or the issue lesson, or (b) a 5-word sequence from the case's user message or correction. |
| IM-9 | P0 | Lint results stored per op and shown in the UI. |
| IM-10 | P0 | > 3 ops → keep first 3, record the rest. Rendered guidelines > 6,000 chars → proposal fails lint (`budget`). |
| IM-11 | P0 | All ops dropped → no candidate; `ready` with "all edits rejected by lint". |
| IM-12 | P0 | Candidate is evaluated on the same case set and trial count as base; verdict per EV-9 with the proposal's target axis. |
| IM-13 | P0 | Accepting with `meets_policy = false` requires a note (400 otherwise); stored as `Override: …` on the version. |
| IM-14 | P0 | Accept with `deploy = true` moves the deploy pointer; the linked issue becomes `resolved`. Reject sets the issue back to `confirmed`. |
| IM-15 | P0 | Each resulting guideline records `addresses` (case ids). |
| IM-16 | P1 | Multi-candidate mode (3 candidates; best by policy, then fewest ops). |
| IM-17 | P0 | (**v2**) IF a proposal for the agent is in `generating`, `evaluating` or `ready`, THEN a new proposal request returns 409. |
| IM-18 | P0 | (**v2**) IF the last accepted proposal was less than `policy.cooldown_hours` ago, THEN a new request returns 429 unless `override_note` is given (recorded, `trigger_source = manual_override`). |
| IM-19 | P0 | (**v2**) The proposal SHALL record `trigger_source` (`issue_ready`, `manual`, `manual_override`) and `cost_usd` (improver + both eval runs, QC-4). |

## Output schema

```json
{
  "diagnoses": [{"case_id": "c_1", "root_cause": "missing_rule | wrong_tool_use | format | ambiguous_question | case_is_wrong",
                 "agent_fault": true, "lesson": "Revenue figures must exclude refunded orders."}],
  "ops": [{"op": "add", "section": "Revenue rules", "text": "Exclude orders with status \"refunded\" from every revenue figure.",
           "addresses": ["c_1"], "why": "Refund handling was never specified."}],
  "skipped": [{"case_id": "c_8", "reason": "case_is_wrong | agent_fault_false | flaky"}]
}
```

## Prompt — required elements
1. Role: improve guidelines so the agent handles a *class* of questions on **one axis** better.
2. Rules: general lessons only; no numbers, dates or phrases copied from the case; prefer replace/merge over add; ≤ 3 ops; "no change" is acceptable; don't break the listed passing cases.
3. One-shot bad vs good rule example.
4. Inputs (IM-3, IM-4) in delimiters, marked as data.
5. JSON only.

## Acceptance criteria

- **AC-IM-a** Prompt for an `llm_judge` target contains no rubric text.
- **AC-IM-b** (**changed v2**) Prompt contains no benchmark case ids/questions and no cases from other axes.
- **AC-IM-c** Lint: `"For Q3 2026, exclude refunds"` on a case asking about Q3 → rejected (quarter token + year). `"Exclude refunded orders from revenue"` → passes.
- **AC-IM-d** Lint: a rule containing "what was total revenue in" when the case asks "What was total revenue in Q3?" → rejected (5-gram).
- **AC-IM-e** 5 ops → 3 applied, 2 recorded as dropped.
- **AC-IM-f** Invalid JSON twice → status `failed` with the error stored.
- **AC-IM-g** Accept with a failing verdict and empty note → 400; with a note → version `change_note` starts with `Override:`.
- **AC-IM-h** (e2e, seed agent) After the refund issue is reproduced and confirmed, a proposal on `accuracy` fixes the improve case **and** passes ≥ 2 of 3 benchmark variants; verdict and cost shown. Run 3× before the demo.
- **AC-IM-i** (**v2**) Second proposal while one is `ready` → 409.
- **AC-IM-j** (**v2**) Request inside cooldown → 429; with `override_note` → created with `trigger_source = manual_override`.
- **AC-IM-k** (**v2**) Accept + deploy marks the linked issue `resolved`.

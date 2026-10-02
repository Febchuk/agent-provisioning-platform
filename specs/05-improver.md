# 05 — Improver

## Purpose

Propose a new version that fixes consistent failures **by generalizing a lesson**, not by memorizing a case. Output is a reviewable proposal; nothing is deployed without the owner.

## Threats and guards (design rationale)

| Threat | Guard | Req |
|---|---|---|
| Memorizing the case | Literal lint + hidden siblings | IM-8, IM-9, EV-10 |
| Writing to the test | Improver never sees check specs or hidden cases | IM-3, EV-11 |
| Chasing noise | Flaky cases are skipped; "no change" is a valid outcome | IM-2, IM-6 |
| Prompt bloat | Structured ops, max 3 ops, size budget | IM-5, IM-10 |
| Silent regressions | Full eval on base + candidate, policy verdict | IM-12 |

## In scope
- Single-candidate pipeline: triage → diagnose+propose (one LLM call) → apply ops → lint → evaluate → verdict.

## Out of scope (P0)
- System-prompt rewrites by the improver (it may only edit guidelines). Multiple candidates (P1). Consolidation/refactor proposals (stretch, not planned). Learning from thumbs-up. Changing tools or model.

## Endpoints

| Method | Path | P | Purpose |
|---|---|---|---|
| POST | `/agents/{id}/proposals` | P0 | Start pipeline against deployed version → `{proposal_id}` |
| GET | `/proposals/{id}` | P0 | Status, ops, diff (rendered guidelines before/after), verdict, eval results |
| POST | `/proposals/{id}/accept` | P0 | `{deploy: bool, note?}` |
| POST | `/proposals/{id}/reject` | P0 | |

## Pipeline

```
1 Triage      base eval run on deployed version (reuse if one exists for this version and case set)
              targets = visible active cases passing 0 of 3 trials (IM-2); flaky ones skipped
2 Diagnose +  one LLM call, JSON output (schema below)
  Propose
3 Apply       ops → new guidelines list → candidate version (source = proposal)
4 Lint        deterministic checks; violating ops are dropped and recorded
5 Evaluate    eval run on candidate (visible + hidden)
6 Verdict     compute_verdict(...) → proposal.status = ready
```

## Requirements

| ID | P | Requirement |
|---|---|---|
| IM-1 | P0 | WHEN a proposal is requested, THE SYSTEM SHALL use (or create) a base eval run for the deployed version over the current active case set. |
| IM-2 | P0 | THE SYSTEM SHALL select as targets only visible cases that **fail** and are **not flaky** on base (i.e., 0 of 3 trials pass). Flaky failing cases are listed in `skipped` with reason `flaky`. |
| IM-3 | P0 | For each target the improver input SHALL include: the case history, the run trace (tool calls and truncated outputs), the final answer, and the originating correction text if any. It SHALL NOT include `check_spec`, rubrics, expected values, or any hidden case. |
| IM-4 | P0 | The improver input SHALL also include the current guidelines (with ids) and system prompt (read-only), and the list of currently passing visible case **names** (to discourage breaking them). |
| IM-5 | P0 | The improver SHALL return JSON matching the schema below; IF parsing fails, THEN THE SYSTEM SHALL retry once with the validation error appended, then mark the proposal `failed`. |
| IM-6 | P0 | IF the improver returns zero ops, THEN THE SYSTEM SHALL mark the proposal `ready` with no candidate and show its diagnoses ("no change recommended"). |
| IM-7 | P0 | THE SYSTEM SHALL apply ops in order: `add` (new id), `replace` (by `rule_id`), `delete` (by `rule_id`); IF a `rule_id` doesn't exist, THEN that op is dropped and recorded in `lint`. |
| IM-8 | P0 | Literal lint: an op's `text` SHALL be rejected IF it contains (a) any number of ≥2 digits, a year, a quarter token (`Q1`–`Q4`), or a month name that also appears in the target case's history or correction, or (b) any 5-word sequence (lowercased, punctuation stripped) that appears in the case's user message or correction. |
| IM-9 | P0 | Lint results SHALL be stored per op as `{op_index, passed, violations: [...]}` and shown in the UI. |
| IM-10 | P0 | IF more than 3 ops are returned, THEN THE SYSTEM SHALL keep the first 3 and record the rest as dropped. IF rendered guidelines exceed 6,000 characters (~1,500 tokens) after applying ops, THEN the proposal SHALL fail lint with reason `budget` (no candidate evaluated). |
| IM-11 | P0 | IF all ops are dropped by lint, THEN THE SYSTEM SHALL not create a candidate and SHALL mark the proposal `ready` with reason "all edits rejected by lint". |
| IM-12 | P0 | WHEN a candidate exists, THE SYSTEM SHALL run an eval on it with the same case set and trial count as the base and compute the verdict (EV-9). |
| IM-13 | P0 | WHEN a proposal is accepted with `meets_policy = false`, THE SYSTEM SHALL require a non-empty `note` (400 otherwise) and store it as the version's `change_note` prefixed with `Override:`. |
| IM-14 | P0 | WHEN accepted with `deploy = true`, THE SYSTEM SHALL move the deploy pointer to the candidate. |
| IM-15 | P0 | THE SYSTEM SHALL record each op's `addresses` on the resulting guideline so the UI can show "added for case X". |
| IM-16 | P1 | WHERE multi-candidate mode is on, THE SYSTEM SHALL generate 3 proposals (instructed as minimal / consolidating / broader), evaluate all, and surface the one that meets policy with the fewest ops (ties → highest cand score). |

## Improver output schema

```json
{
  "diagnoses": [
    {"case_id": "c_1",
     "root_cause": "missing_rule | wrong_tool_use | format | ambiguous_question | case_is_wrong",
     "agent_fault": true,
     "lesson": "Revenue figures must exclude refunded orders."}
  ],
  "ops": [
    {"op": "add", "section": "Revenue rules",
     "text": "Exclude orders with status \"refunded\" from every revenue figure.",
     "addresses": ["c_1"], "why": "Refund handling was never specified."},
    {"op": "replace", "rule_id": "g_charts_1", "text": "Always label both axes, with units.", "addresses": ["c_6"], "why": "…"},
    {"op": "delete", "rule_id": "g_x", "addresses": [], "why": "Superseded by the rule above."}
  ],
  "skipped": [{"case_id": "c_8", "reason": "case_is_wrong | agent_fault_false | flaky"}]
}
```

## Improver prompt — required elements (P0)

1. Role: "You improve an agent's guidelines so it handles a *class* of questions better."
2. Hard rules: general lessons only; no numbers, dates or phrases copied from the case; prefer replacing or merging over adding; at most 3 ops; "no change" is acceptable.
3. A one-shot example of a **bad** rule ("For Q3 revenue questions, answer $412,380") and its **good** rewrite ("Exclude refunded orders from revenue").
4. Inputs per IM-3 / IM-4, wrapped in delimiters and marked as data.
5. Output: JSON only, schema above.

## Acceptance criteria

- **AC-IM-a** Given a target case whose check is `llm_judge`, when the improver prompt is built, then the prompt string does not contain the rubric text (assert substring absent).
- **AC-IM-b** Given hidden siblings exist, then the prompt contains none of their ids or questions.
- **AC-IM-c** Lint: `"For Q3 2026, exclude refunds"` on a case asking about Q3 → rejected (quarter token + year). `"Exclude refunded orders from revenue"` → passes.
- **AC-IM-d** Lint: rule containing "what was total revenue in" when the case asks "What was total revenue in Q3?" → rejected (5-gram).
- **AC-IM-e** Given FakeLLM returning 5 ops, then 3 are applied and 2 recorded as dropped.
- **AC-IM-f** Given FakeLLM returning invalid JSON twice, then proposal status `failed` with error stored.
- **AC-IM-g** Given accept with failing verdict and empty note → 400; with note → version `change_note` starts with `Override:`.
- **AC-IM-h** (end-to-end, real model, seed agent) Given v1 fails "Q3 revenue excludes refunds", when a proposal runs, then the candidate passes that case and its verdict is computed. Run 3 times before the demo; record results in `DECISIONS.md`.

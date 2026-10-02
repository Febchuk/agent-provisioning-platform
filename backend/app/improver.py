"""Improver pipeline (specs/05-improver.md T4.4, IM-1, IM-2, IM-5, IM-6,
IM-12, IM-13, IM-14; IM-16 explicitly skipped, P1).

Wires together, in order (specs/05 "Pipeline"):
  1 Triage      app.evals.run_eval_run on the deployed version (reused if a
                completed eval run already exists for this version), targets
                = visible active cases failing 0/3 trials and not flaky
                (IM-2).
  2 Diagnose +  app.improver_prompt.build_improver_prompt -> one LLM call ->
    Propose     parse/validate JSON against the output schema, retry once on
                failure (IM-5).
  3 Apply       app.ops.apply_ops -> new guidelines -> app.services.create_version
                (source="proposal").
  4 Lint        app.lint.lint_ops; violating ops dropped and recorded (IM-8,
                IM-9); IM-11 (all dropped -> no candidate) and IM-10's budget
                check both happen here, before a candidate version is created.
  5 Evaluate    app.evals.run_eval_run on the candidate, same case set +
                trial count as base (IM-12).
  6 Verdict     app.verdict.compute_verdict -> proposal.status = "ready".

Nothing here lets the improver touch `system_prompt`, `tools`, or `model` --
`apply_ops`/`create_version` only ever receive a `guidelines` list; the
candidate version is built with `create_version(..., guidelines=new_guidelines)`
which (per `app.services.create_version`'s DM-2 copy semantics) copies
`system_prompt`/`tools`/`model`/`files` verbatim from the base version.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from app.evals import (
    LLMFactory,
    SandboxFactory,
    case_pass_map,
    cases_for_improver,
    get_or_create_policy,
    run_eval_run,
)
from app.ids import new_id
from app.improver_prompt import PassingCase, TargetCase, build_improver_prompt
from app.lint import CaseLintContext, lint_ops
from app.llm import LLM
from app.models import AgentVersion, EvalCase, EvalResult, EvalRun, Proposal, Run
from app.models import _utcnow
from app.ops import apply_ops, cap_ops, exceeds_budget
from app.services import create_version, deploy_version

MAX_JSON_RETRIES = 1  # IM-5: "retry once"


class ProposalAcceptError(Exception):
    """Raised by `accept_proposal` for a 400-worthy condition (IM-13)."""


# ---------------------------------------------------------------------------
# Step 1: Triage
# ---------------------------------------------------------------------------
def _find_reusable_base_eval_run(session: Session, *, agent_id: str, version_id: str) -> Optional[EvalRun]:
    """IM-1: "use (or create) a base eval run for the deployed version over
    the current active case set." Reuse the most recent COMPLETED eval run
    for this exact version if one exists; we don't attempt to diff "the
    current active case set" against that run's case set (eval runs don't
    record a case-set snapshot) -- the simplifying assumption documented
    here is that a prior completed run for this version is still
    representative. This matches specs/04's own notion of "latest" eval run
    for a version (see `services.list_cases_with_latest_results`), so it's
    consistent with how the rest of the system already treats "the" eval run
    for a version.
    """
    return session.exec(
        select(EvalRun)
        .where(EvalRun.agent_id == agent_id, EvalRun.version_id == version_id, EvalRun.status == "completed")
        .order_by(EvalRun.finished_at.desc())
    ).first()


async def _get_or_create_base_eval_run(
    session: Session,
    *,
    agent_id: str,
    version_id: str,
    sandbox_factory: SandboxFactory,
    llm_factory: LLMFactory,
    judge_model: Optional[str],
) -> EvalRun:
    existing = _find_reusable_base_eval_run(session, agent_id=agent_id, version_id=version_id)
    if existing is not None:
        return existing
    return await run_eval_run(
        session,
        agent_id=agent_id,
        version_id=version_id,
        sandbox_factory=sandbox_factory,
        llm_factory=llm_factory,
        judge_model=judge_model,
    )


def _case_trial_results(session: Session, eval_run_id: str, case_id: str) -> list[EvalResult]:
    return session.exec(
        select(EvalResult).where(EvalResult.eval_run_id == eval_run_id, EvalResult.case_id == case_id)
    ).all()


@dataclass
class Triage:
    base_eval_run: EvalRun
    targets: list[EvalCase]
    skipped_flaky: list[dict]  # [{case_id, reason: "flaky"}]


def triage(session: Session, *, agent_id: str, base_eval_run: EvalRun) -> Triage:
    """IM-2: targets = visible active cases failing with 0/3 trials passing
    (i.e. zero passing trials) and NOT flaky. A case with >=1 passing trial
    is either already passing (not a target) or flaky (goes to `skipped`
    with reason "flaky"), never a diagnosis target either way.

    Uses `cases_for_improver` (EV-11) for the visible/active case set --
    hidden siblings are never targets and never even considered here.
    """
    policy = get_or_create_policy(session, agent_id)
    visible_active_cases = cases_for_improver(session, agent_id)

    targets: list[EvalCase] = []
    skipped_flaky: list[dict] = []

    for case in visible_active_cases:
        results = _case_trial_results(session, base_eval_run.id, case.id)
        if not results:
            # No trial data for this case on the base run at all (e.g. it was
            # created after that eval run completed) -- not a target; nothing
            # to diagnose it against.
            continue
        passing = sum(1 for r in results if r.passed)
        if passing == 0:
            targets.append(case)
        elif passing < len(results):
            # flaky: >=1 pass and >=1 fail
            skipped_flaky.append({"case_id": case.id, "reason": "flaky"})
        # else: fully passing on base -- not a target, not skipped (just not interesting).

    return Triage(base_eval_run=base_eval_run, targets=targets, skipped_flaky=skipped_flaky)


def _passing_cases(session: Session, *, agent_id: str, base_eval_run: EvalRun, targets: list[EvalCase]) -> list[EvalCase]:
    target_ids = {c.id for c in targets}
    visible_active_cases = cases_for_improver(session, agent_id)
    passing: list[EvalCase] = []
    for case in visible_active_cases:
        if case.id in target_ids:
            continue
        results = _case_trial_results(session, base_eval_run.id, case.id)
        if results and all(r.passed for r in results):
            passing.append(case)
    return passing


# ---------------------------------------------------------------------------
# Step 2: Diagnose + Propose
# ---------------------------------------------------------------------------
def _trace_for_case_trial(session: Session, *, eval_run_id: str, case_id: str) -> tuple[list[dict], str]:
    """The run trace + final answer from ONE representative trial of this
    case on the base eval run (IM-3: "the run trace"), so the improver sees
    a concrete example of what the agent actually did, not just the
    question. Uses the first trial found -- all 3 failed (0/3, per triage),
    so any of them illustrates the failure mode.
    """
    results = _case_trial_results(session, eval_run_id, case_id)
    if not results:
        return [], ""
    run = session.get(Run, results[0].run_id)
    if run is None:
        return [], ""
    return list(run.trace or []), run.final_answer or ""


def _correction_for_case(session: Session, case: EvalCase) -> Optional[str]:
    """A case created from feedback carries `from_feedback_id`; its
    correction text (if any) is the "originating correction" IM-3 asks for.
    """
    if not case.from_feedback_id:
        return None
    from app.models import Feedback

    feedback = session.get(Feedback, case.from_feedback_id)
    return feedback.correction if feedback else None


def build_targets_and_passing(
    session: Session, *, agent_id: str, base_eval_run: EvalRun, triage_result: Triage
) -> tuple[list[TargetCase], list[PassingCase]]:
    targets: list[TargetCase] = []
    for case in triage_result.targets:
        trace, final_answer = _trace_for_case_trial(session, eval_run_id=base_eval_run.id, case_id=case.id)
        targets.append(
            TargetCase(
                case_id=case.id,
                name=case.name,
                history=list(case.history or []),
                trace=trace,
                final_answer=final_answer,
                correction=_correction_for_case(session, case),
            )
        )

    passing_cases = [
        PassingCase(case_id=c.id, name=c.name)
        for c in _passing_cases(session, agent_id=agent_id, base_eval_run=base_eval_run, targets=triage_result.targets)
    ]
    return targets, passing_cases


_VALID_ROOT_CAUSES = {"missing_rule", "wrong_tool_use", "format", "ambiguous_question", "case_is_wrong"}
_VALID_OP_TYPES = {"add", "replace", "delete"}
_VALID_SKIP_REASONS = {"case_is_wrong", "agent_fault_false", "flaky"}


def validate_improver_output(parsed: Any) -> dict:
    """IM-5: raises ValueError with a human-readable message (used as the
    "validation error" appended on retry) if `parsed` doesn't match the
    output schema. Returns the parsed dict unchanged on success (not a
    reshaping function -- callers use `parsed["diagnoses"]` etc. directly).
    """
    if not isinstance(parsed, dict):
        raise ValueError("top-level JSON must be an object")

    for key in ("diagnoses", "ops", "skipped"):
        if key not in parsed:
            raise ValueError(f"missing required key: {key!r}")
        if not isinstance(parsed[key], list):
            raise ValueError(f"{key!r} must be a list")

    for i, d in enumerate(parsed["diagnoses"]):
        if not isinstance(d, dict):
            raise ValueError(f"diagnoses[{i}] must be an object")
        if "case_id" not in d:
            raise ValueError(f"diagnoses[{i}] missing 'case_id'")
        if d.get("root_cause") not in _VALID_ROOT_CAUSES:
            raise ValueError(f"diagnoses[{i}].root_cause invalid: {d.get('root_cause')!r}")
        if not isinstance(d.get("agent_fault"), bool):
            raise ValueError(f"diagnoses[{i}].agent_fault must be a boolean")
        if not isinstance(d.get("lesson"), str):
            raise ValueError(f"diagnoses[{i}].lesson must be a string")

    for i, op in enumerate(parsed["ops"]):
        if not isinstance(op, dict):
            raise ValueError(f"ops[{i}] must be an object")
        op_type = op.get("op")
        if op_type not in _VALID_OP_TYPES:
            raise ValueError(f"ops[{i}].op invalid: {op_type!r}")
        if op_type in ("add", "replace") and not isinstance(op.get("text"), str):
            raise ValueError(f"ops[{i}].text must be a string for op={op_type!r}")
        if op_type in ("replace", "delete") and not op.get("rule_id"):
            raise ValueError(f"ops[{i}].rule_id required for op={op_type!r}")
        if op_type == "add" and not op.get("section"):
            raise ValueError(f"ops[{i}].section required for op='add'")
        if "addresses" in op and not isinstance(op["addresses"], list):
            raise ValueError(f"ops[{i}].addresses must be a list")

    for i, s in enumerate(parsed["skipped"]):
        if not isinstance(s, dict):
            raise ValueError(f"skipped[{i}] must be an object")
        if "case_id" not in s:
            raise ValueError(f"skipped[{i}] missing 'case_id'")
        if s.get("reason") not in _VALID_SKIP_REASONS:
            raise ValueError(f"skipped[{i}].reason invalid: {s.get('reason')!r}")

    return parsed


async def diagnose_and_propose(
    llm: LLM,
    *,
    targets: list[TargetCase],
    passing_cases: list[PassingCase],
    guidelines: list[dict],
    system_prompt: str,
    model: Optional[str] = None,
) -> dict:
    """IM-5: one LLM call; on JSON parse/validation failure, retry once with
    the validation error appended; raise on a second failure (caller marks
    the proposal `failed`).
    """
    last_error: Optional[str] = None
    for attempt in range(MAX_JSON_RETRIES + 1):
        messages = build_improver_prompt(
            targets=targets,
            passing_cases=passing_cases,
            guidelines=guidelines,
            system_prompt=system_prompt,
            retry_validation_error=last_error,
        )
        response = await llm.chat(
            messages=messages, tools=None, model=model, temperature=0, response_format={"type": "json_object"}
        )
        try:
            parsed = json.loads(response.content or "")
            return validate_improver_output(parsed)
        except (json.JSONDecodeError, ValueError) as e:
            last_error = str(e)
            continue

    raise ValueError(f"improver output invalid after retry: {last_error}")


# ---------------------------------------------------------------------------
# Steps 3-4: Apply + Lint
# ---------------------------------------------------------------------------
@dataclass
class ApplyAndLintOutcome:
    guidelines: list[dict]
    lint_results: list[dict]  # [{op_index, passed, violations}] (IM-9)
    applied_ops: list[dict]
    all_dropped: bool
    budget_exceeded: bool


def apply_and_lint(
    *,
    base_guidelines: list[dict],
    ops: list[dict],
    targets: list[TargetCase],
) -> ApplyAndLintOutcome:
    """Steps 3+4 of the pipeline, combined because lint must run on each op
    BEFORE it's applied (IM-8 lints the op's proposed `text`, not the
    resulting guideline), while `apply_ops` needs the already-lint-passing
    subset. Order: cap to 3 (IM-10) -> lint each capped op (IM-8/IM-9) ->
    apply only the lint-passing ones (IM-7) -> budget check on the result
    (IM-10).
    """
    capped_ops, capped_dropped = cap_ops(ops)

    cases_by_id = {
        t.case_id: CaseLintContext(case_id=t.case_id, history=t.history, correction=t.correction) for t in targets
    }
    lint_results = lint_ops(capped_ops, cases_by_id=cases_by_id)

    lint_passing_ops = [op for op, lr in zip(capped_ops, lint_results) if lr.passed]

    apply_result = apply_ops(base_guidelines, lint_passing_ops)

    # IM-9 output shape: one entry per op in the ORIGINAL ops list (including
    # ones dropped by the IM-10 cap, and ones dropped later for a missing
    # rule_id), each {op_index, passed, violations, reason?}.
    lint_results_by_index = {lr.op_index: lr for lr in lint_results}
    rule_id_dropped_by_index = {d.op_index: d for d in apply_result.dropped}

    full_lint: list[dict] = []
    for i, op in enumerate(ops):
        if i >= len(capped_ops):
            full_lint.append(
                {"op_index": i, "passed": False, "violations": [], "reason": "max_ops_exceeded"}
            )
            continue
        lr = lint_results_by_index.get(i)
        if lr is not None and not lr.passed:
            full_lint.append(
                {
                    "op_index": i,
                    "passed": False,
                    "violations": [{"kind": v.kind, "value": v.value} for v in lr.violations],
                    "reason": "literal_lint",
                }
            )
            continue
        if i in rule_id_dropped_by_index:
            full_lint.append({"op_index": i, "passed": False, "violations": [], "reason": "rule_id_not_found"})
            continue
        full_lint.append({"op_index": i, "passed": True, "violations": []})

    applied_ops = [op for i, op in enumerate(ops) if full_lint[i]["passed"]]
    all_dropped = bool(ops) and not applied_ops

    budget_exceeded = False
    if not all_dropped and applied_ops:
        budget_exceeded = exceeds_budget(apply_result.guidelines)

    return ApplyAndLintOutcome(
        guidelines=apply_result.guidelines,
        lint_results=full_lint,
        applied_ops=applied_ops,
        all_dropped=all_dropped,
        budget_exceeded=budget_exceeded,
    )


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------
async def run_proposal_pipeline(
    session: Session,
    *,
    agent_id: str,
    proposal_id: str,
    sandbox_factory: SandboxFactory,
    llm_factory: LLMFactory,
    improver_llm_factory: Callable[[], LLM],
    judge_model: Optional[str] = None,
    improver_model: Optional[str] = None,
) -> Proposal:
    """Runs the full pipeline (steps 1-6) for an already-created `Proposal`
    row (status="generating"), mutating it in place as the pipeline
    progresses, and returns the final row. Designed to be awaited directly by
    the HTTP handler for simplicity (specs/05 doesn't require this be
    backgrounded like chat turns are; a proposal run is already bounded by a
    handful of eval runs plus one LLM call).
    """
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise ValueError(f"proposal {proposal_id!r} not found")

    base_version = session.get(AgentVersion, proposal.base_version_id)
    if base_version is None:
        raise ValueError("base version not found")

    try:
        # --- Step 1: Triage -------------------------------------------------
        base_eval_run = await _get_or_create_base_eval_run(
            session,
            agent_id=agent_id,
            version_id=base_version.id,
            sandbox_factory=sandbox_factory,
            llm_factory=llm_factory,
            judge_model=judge_model,
        )
        proposal.base_eval_run_id = base_eval_run.id
        session.add(proposal)
        session.commit()

        triage_result = triage(session, agent_id=agent_id, base_eval_run=base_eval_run)
        proposal.skipped = list(triage_result.skipped_flaky)
        session.add(proposal)
        session.commit()

        if not triage_result.targets:
            # Nothing to diagnose (e.g. base already passes everything, or
            # every failure is flaky) -- IM-6's "no change" outcome applies
            # equally here: there is no case to propose a change for.
            proposal.diagnoses = []
            proposal.ops = []
            proposal.lint = []
            proposal.status = "ready"
            proposal.decision_note = "no failing (non-flaky) visible cases; no change recommended"
            session.add(proposal)
            session.commit()
            session.refresh(proposal)
            return proposal

        targets, passing_cases = build_targets_and_passing(
            session, agent_id=agent_id, base_eval_run=base_eval_run, triage_result=triage_result
        )

        # --- Step 2: Diagnose + Propose --------------------------------------
        improver_llm = improver_llm_factory()
        try:
            improver_output = await diagnose_and_propose(
                improver_llm,
                targets=targets,
                passing_cases=passing_cases,
                guidelines=list(base_version.guidelines or []),
                system_prompt=base_version.system_prompt or "",
                model=improver_model,
            )
        except ValueError as e:
            # IM-5: failed validation twice -> proposal `failed`, error stored.
            proposal.status = "failed"
            proposal.decision_note = f"improver output invalid: {e}"
            session.add(proposal)
            session.commit()
            session.refresh(proposal)
            return proposal

        proposal.diagnoses = improver_output["diagnoses"]
        session.add(proposal)
        session.commit()

        ops = improver_output["ops"]
        if not ops:
            # IM-6: zero ops -> `ready`, no candidate, diagnoses shown.
            proposal.ops = []
            proposal.lint = []
            proposal.status = "ready"
            proposal.decision_note = "no change recommended"
            session.add(proposal)
            session.commit()
            session.refresh(proposal)
            return proposal

        # --- Steps 3-4: Apply + Lint ------------------------------------------
        outcome = apply_and_lint(base_guidelines=list(base_version.guidelines or []), ops=ops, targets=targets)
        proposal.ops = ops
        proposal.lint = outcome.lint_results
        session.add(proposal)
        session.commit()

        if outcome.all_dropped:
            # IM-11: all ops dropped by lint -> no candidate, `ready`.
            proposal.status = "ready"
            proposal.decision_note = "all edits rejected by lint"
            session.add(proposal)
            session.commit()
            session.refresh(proposal)
            return proposal

        if outcome.budget_exceeded:
            # IM-10: budget failure -> proposal fails lint, no candidate evaluated.
            proposal.status = "ready"
            proposal.decision_note = "budget"
            session.add(proposal)
            session.commit()
            session.refresh(proposal)
            return proposal

        candidate = create_version(
            session,
            agent_id=agent_id,
            guidelines=outcome.guidelines,
            parent_version_id=base_version.id,
            source="proposal",
            change_note=f"Proposal {proposal.id}: {len(outcome.applied_ops)} guideline op(s) applied",
        )
        proposal.candidate_version_id = candidate.id
        proposal.status = "evaluating"
        session.add(proposal)
        session.commit()

        # --- Step 5: Evaluate ---------------------------------------------
        policy = get_or_create_policy(session, agent_id)
        cand_eval_run = await run_eval_run(
            session,
            agent_id=agent_id,
            version_id=candidate.id,
            sandbox_factory=sandbox_factory,
            llm_factory=llm_factory,
            judge_model=judge_model,
        )
        proposal.cand_eval_run_id = cand_eval_run.id
        session.add(proposal)
        session.commit()

        # --- Step 6: Verdict -------------------------------------------------
        from app.verdict import compute_verdict

        all_cases = session.exec(
            select(EvalCase).where(EvalCase.agent_id == agent_id, EvalCase.status == "active")
        ).all()
        case_dicts = [
            {"id": c.id, "name": c.name, "axis": c.axis, "pinned": c.pinned, "hidden": c.hidden} for c in all_cases
        ]
        base_results = case_pass_map(session, base_eval_run.id)
        cand_results = case_pass_map(session, cand_eval_run.id)
        policy_dict = {
            "min_avg_improvement_pct": policy.min_avg_improvement_pct,
            "max_regressions": policy.max_regressions,
        }
        verdict = compute_verdict(base_results, cand_results, case_dicts, policy_dict)

        proposal.verdict = verdict
        proposal.status = "ready"
        session.add(proposal)
        session.commit()
        session.refresh(proposal)
        return proposal

    except Exception as e:  # ultimate backstop: never leave a proposal stuck "generating"
        proposal.status = "failed"
        proposal.decision_note = f"pipeline error: {e}"
        session.add(proposal)
        session.commit()
        session.refresh(proposal)
        return proposal


# ---------------------------------------------------------------------------
# Proposal creation + accept/reject (endpoints call these)
# ---------------------------------------------------------------------------
def create_proposal(session: Session, *, agent_id: str, base_version_id: str) -> Proposal:
    proposal = Proposal(
        id=new_id("pr"),
        agent_id=agent_id,
        base_version_id=base_version_id,
        status="generating",
    )
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return proposal


def proposal_diff(session: Session, proposal: Proposal) -> dict:
    """Rendered guidelines before/after, for `GET /proposals/{id}`."""
    from app.ops import render_guidelines_text

    base_version = session.get(AgentVersion, proposal.base_version_id)
    before_guidelines = list(base_version.guidelines or []) if base_version else []
    after_guidelines = before_guidelines
    if proposal.candidate_version_id:
        candidate = session.get(AgentVersion, proposal.candidate_version_id)
        after_guidelines = list(candidate.guidelines or []) if candidate else before_guidelines

    return {
        "before": before_guidelines,
        "after": after_guidelines,
        "before_text": render_guidelines_text(before_guidelines),
        "after_text": render_guidelines_text(after_guidelines),
    }


def proposal_to_dict(session: Session, proposal: Proposal) -> dict:
    from app.evals import eval_run_summary

    base_eval = eval_run_summary(session, proposal.base_eval_run_id) if proposal.base_eval_run_id else None
    cand_eval = eval_run_summary(session, proposal.cand_eval_run_id) if proposal.cand_eval_run_id else None

    return {
        "id": proposal.id,
        "agent_id": proposal.agent_id,
        "base_version_id": proposal.base_version_id,
        "candidate_version_id": proposal.candidate_version_id,
        "status": proposal.status,
        "diagnoses": proposal.diagnoses,
        "ops": proposal.ops,
        "skipped": proposal.skipped,
        "lint": proposal.lint,
        "diff": proposal_diff(session, proposal),
        "verdict": proposal.verdict,
        "base_eval_run": base_eval,
        "cand_eval_run": cand_eval,
        "decision_note": proposal.decision_note,
        "created_at": proposal.created_at,
    }


def accept_proposal(
    session: Session, *, proposal_id: str, deploy: bool, note: Optional[str] = None
) -> Proposal:
    """IM-13: accepting with verdict.meets_policy == false requires a
    non-empty `note` (raises `ProposalAcceptError`, which the endpoint turns
    into a 400); the note is stored as the candidate version's `change_note`
    prefixed `Override:`. IM-14: deploy=true moves the deploy pointer to the
    candidate.
    """
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise ValueError(f"proposal {proposal_id!r} not found")
    if proposal.status != "ready":
        raise ProposalAcceptError(f"proposal is not ready to accept (status={proposal.status!r})")

    # IM-13 only applies when there IS a verdict and it fails policy (a
    # proposal with no candidate at all -- IM-6 "no change recommended" or
    # IM-11 "all edits rejected by lint" -- has `verdict = None` and nothing
    # to override; accepting it just acknowledges "no change", no note
    # required).
    has_failing_verdict = proposal.verdict is not None and not proposal.verdict.get("meets_policy")
    if has_failing_verdict:
        if not note or not note.strip():
            raise ProposalAcceptError(
                "accepting a proposal whose verdict does not meet policy requires a non-empty note"
            )
        proposal.decision_note = note
        if proposal.candidate_version_id:
            candidate = session.get(AgentVersion, proposal.candidate_version_id)
            if candidate is not None:
                candidate.change_note = f"Override: {note}"
                session.add(candidate)
    elif note:
        proposal.decision_note = note

    proposal.status = "accepted"
    session.add(proposal)
    session.commit()

    if deploy:
        if not proposal.candidate_version_id:
            raise ProposalAcceptError("cannot deploy: this proposal has no candidate version")
        deploy_version(session, agent_id=proposal.agent_id, version_id=proposal.candidate_version_id)

    session.refresh(proposal)
    return proposal


def reject_proposal(session: Session, *, proposal_id: str) -> Proposal:
    proposal = session.get(Proposal, proposal_id)
    if proposal is None:
        raise ValueError(f"proposal {proposal_id!r} not found")
    proposal.status = "rejected"
    session.add(proposal)
    session.commit()
    session.refresh(proposal)
    return proposal

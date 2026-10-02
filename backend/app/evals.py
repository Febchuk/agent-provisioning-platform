"""Feedback -> draft-case, eval executor, policy helpers, cases_for_improver
(specs/04-feedback-and-evals.md EV-1..EV-11).

This module is the Phase 4 analogue of `app/chat_runtime.py`: it wires
Phase 1-3 primitives (the DB-only `app/services.py`, `app/runner.py`'s
`run_turn`, `app/sandbox.py`'s sandboxes, `app/llm.py`'s `LLM` protocol)
into the new feedback/eval/policy behavior, without modifying any of them.

Responsibilities:
  - EV-1/EV-2/EV-3/EV-4: feedback storage is a plain service function;
    draft-case builds the EV-2 history and prompts an LLM for a draft
    `{name, axis, check_type, rubric}` WITHOUT persisting an active case
    (the draft is returned to the caller; `POST /agents/{id}/cases`
    persists it as `status=active` only when the owner confirms).
  - EV-5..EV-8: `run_eval_run` executes every active case `trials_per_case`
    times on a given version, at most 4 trials concurrently
    (`asyncio.Semaphore(4)`), each trial in a fresh sandbox, using
    `app.runner.run_turn` (source="eval") + `app.checks.run_check`. A
    trial's run failing, or the check itself raising, counts as a failed
    trial with the reason recorded -- never propagates and kills the run.
  - EV-9: `app.verdict.compute_verdict` (pure, separate module) is reused
    here to build case-pass booleans from `EvalResult` rows when comparing
    two eval runs (the proposal flow, Phase 5, will call this; this phase
    only needs the per-case pass/flaky computation during execution).
  - EV-11: `cases_for_improver` excludes hidden cases from anything handed
    to the (future) improver -- enforced in this one function.
"""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from app import db
from app.checks import run_check
from app.files import read_file_bytes
from app.ids import new_id
from app.llm import LLM
from app.models import AgentVersion, EvalCase, EvalResult, EvalRun, Feedback, Message, Policy, Run
from app.models import _utcnow
from app.runner import run_turn
from app.sandbox import Sandbox

MAX_CONCURRENT_TRIALS = 4  # EV-5


# ---------------------------------------------------------------------------
# Policy (GET/PUT /agents/{id}/policy)
# ---------------------------------------------------------------------------
DEFAULT_MAX_REGRESSIONS = {"accuracy": 0, "safety": 0, "tool-use": 1, "format": 1}


def get_or_create_policy(session: Session, agent_id: str) -> Policy:
    policy = session.get(Policy, agent_id)
    if policy is not None:
        return policy
    policy = Policy(agent_id=agent_id)
    session.add(policy)
    session.commit()
    session.refresh(policy)
    return policy


def update_policy(
    session: Session,
    agent_id: str,
    *,
    min_avg_improvement_pct: Optional[float] = None,
    max_regressions: Optional[dict] = None,
    trials_per_case: Optional[int] = None,
    pass_threshold: Optional[int] = None,
) -> Policy:
    policy = get_or_create_policy(session, agent_id)
    if min_avg_improvement_pct is not None:
        policy.min_avg_improvement_pct = min_avg_improvement_pct
    if max_regressions is not None:
        policy.max_regressions = max_regressions
    if trials_per_case is not None:
        policy.trials_per_case = trials_per_case
    if pass_threshold is not None:
        policy.pass_threshold = pass_threshold
    session.add(policy)
    session.commit()
    session.refresh(policy)
    return policy


def policy_to_dict(policy: Policy) -> dict:
    return {
        "agent_id": policy.agent_id,
        "min_avg_improvement_pct": policy.min_avg_improvement_pct,
        "max_regressions": policy.max_regressions,
        "trials_per_case": policy.trials_per_case,
        "pass_threshold": policy.pass_threshold,
    }


# ---------------------------------------------------------------------------
# Feedback (EV-1)
# ---------------------------------------------------------------------------
def create_feedback(session: Session, *, run_id: str, conversation_id: str, rating: str, correction: Optional[str] = None) -> Feedback:
    """EV-1: store with status `new`, linked to the run and conversation."""
    feedback = Feedback(
        id=new_id("fb"),
        run_id=run_id,
        conversation_id=conversation_id,
        rating=rating,
        correction=correction,
        status="new",
    )
    session.add(feedback)
    session.commit()
    session.refresh(feedback)
    return feedback


def list_feedback(session: Session, agent_id: str, *, status: Optional[str] = None) -> list[Feedback]:
    from app.models import Conversation

    query = select(Feedback).where(Feedback.conversation_id.in_(select(Conversation.id).where(Conversation.agent_id == agent_id)))
    if status is not None:
        query = query.where(Feedback.status == status)
    return session.exec(query.order_by(Feedback.created_at.desc())).all()


def dismiss_feedback(session: Session, feedback_id: str) -> Feedback:
    feedback = session.get(Feedback, feedback_id)
    if feedback is None:
        raise ValueError(f"feedback {feedback_id!r} not found")
    feedback.status = "dismissed"
    session.add(feedback)
    session.commit()
    session.refresh(feedback)
    return feedback


# ---------------------------------------------------------------------------
# Draft-case (EV-2, EV-3, EV-4)
# ---------------------------------------------------------------------------
def build_draft_history(session: Session, feedback: Feedback) -> list[dict]:
    """EV-2 / AC-EV-a: history = the conversation's messages up to and
    including the USER message of the run that this feedback is about.

    `Message.run_id` is set on the assistant/tool messages a run produced
    (see chat_runtime._execute_turn), not on the user message that triggered
    it. So "up to and including the user message of that run" means: take
    all messages strictly before the first message whose run_id == this
    feedback's run_id (that first message is the run's own first assistant
    message), which -- because messages are persisted strictly in
    conversation order -- ends exactly at the triggering user message.
    """
    rows = session.exec(
        select(Message).where(Message.conversation_id == feedback.conversation_id).order_by(Message.seq)
    ).all()

    cutoff_seq: Optional[int] = None
    for m in rows:
        if m.run_id == feedback.run_id:
            cutoff_seq = m.seq
            break

    if cutoff_seq is None:
        # Fallback: no messages tagged with this run_id (shouldn't happen for
        # a chat run) -- include everything rather than nothing.
        included = rows
    else:
        included = [m for m in rows if m.seq < cutoff_seq]

    history: list[dict] = []
    for m in included:
        msg: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            msg["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            msg["tool_call_id"] = m.tool_call_id
        history.append(msg)
    return history


DRAFT_CASE_SYSTEM_PROMPT = (
    "You help an agent owner turn a user's correction into a reusable eval case. "
    "You will be given the conversation history up to the user's question, the "
    "agent's answer, and the user's correction explaining what was wrong. "
    "Produce a draft eval case as strict JSON: "
    '{"name": "short descriptive name", "axis": "one of accuracy|format|tool-use|safety '
    'or another short free-text axis", "check_type": "llm_judge", "rubric": "..."}. '
    "\n\n"
    "Critically: the rubric must describe a PROPERTY that a correct answer has "
    "(e.g. 'excludes orders with status refunded from any revenue total', "
    "'states the result is an estimate when exact data is unavailable'), "
    "NOT a specific value or number (e.g. do NOT write 'the answer is 412.30'), "
    "UNLESS the user's correction itself states a specific value that any "
    "correct answer must match -- only then may the rubric name that value. "
    "Prefer the property-based phrasing whenever the correction describes a "
    "kind of mistake (wrong filter, wrong rounding, missing caveat) rather "
    "than asserting one exact number."
)


@dataclass
class CaseDraft:
    name: str
    axis: str
    check_type: str
    rubric: str
    history: list[dict]
    from_feedback_id: str


async def draft_case_from_feedback(session: Session, feedback_id: str, llm: LLM, *, model: Optional[str] = None) -> CaseDraft:
    """EV-2/EV-3/EV-4: ask the LLM for {name, axis, check_type: "llm_judge",
    rubric} derived from the correction, using history up to and including
    the user message of the run this feedback is about. Returns the draft
    WITHOUT persisting an eval_case row (EV-4: a case only becomes active
    when the owner confirms via POST /agents/{id}/cases).
    """
    feedback = session.get(Feedback, feedback_id)
    if feedback is None:
        raise ValueError(f"feedback {feedback_id!r} not found")

    history = build_draft_history(session, feedback)
    run = session.get(Run, feedback.run_id)
    agent_answer = run.final_answer if run else ""

    question = ""
    for m in reversed(history):
        if m.get("role") == "user":
            question = m.get("content") or ""
            break

    user_content = (
        f"User's question:\n{question}\n\n"
        f"Agent's answer:\n{agent_answer}\n\n"
        f"User's correction:\n{feedback.correction or '(no correction text given, rating was ' + feedback.rating + ')'}\n\n"
        "Produce the draft eval case JSON now."
    )
    messages = [
        {"role": "system", "content": DRAFT_CASE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    response = await llm.chat(messages=messages, tools=None, model=model, temperature=0, response_format={"type": "json_object"})
    parsed = json.loads(response.content or "{}")

    return CaseDraft(
        name=str(parsed.get("name", "Untitled case")),
        axis=str(parsed.get("axis", "accuracy")),
        check_type="llm_judge",  # EV-2: draft-case always proposes llm_judge
        rubric=str(parsed.get("rubric", "")),
        history=history,
        from_feedback_id=feedback_id,
    )


# ---------------------------------------------------------------------------
# cases_for_improver (EV-11, AC-EV-f)
# ---------------------------------------------------------------------------
def cases_for_improver(session: Session, agent_id: str) -> list[EvalCase]:
    """EV-11: cases with hidden=True SHALL be excluded from any data passed
    to the improver. This is the one function that enforces that -- Phase 5
    (the improver) must call this rather than querying eval_cases directly.
    """
    return session.exec(
        select(EvalCase).where(
            EvalCase.agent_id == agent_id,
            EvalCase.status == "active",
            EvalCase.hidden == False,  # noqa: E712 (SQLModel comparison, not a Python bool check)
        )
    ).all()


# ---------------------------------------------------------------------------
# Visible cases listing (GET /agents/{id}/cases)
# ---------------------------------------------------------------------------
def list_visible_cases(session: Session, agent_id: str) -> list[EvalCase]:
    """Visible = non-hidden, per the endpoint table ("Visible cases + latest
    results on the deployed version"). Includes draft/active/dismissed --
    callers that want only active cases (e.g. the executor) filter further.
    """
    return session.exec(
        select(EvalCase).where(
            EvalCase.agent_id == agent_id,
            EvalCase.hidden == False,  # noqa: E712
        )
    ).all()


def list_active_cases(session: Session, agent_id: str) -> list[EvalCase]:
    """EV-5: "every active case (visible AND hidden)" -- the executor must
    include hidden siblings, unlike `list_visible_cases`.
    """
    return session.exec(
        select(EvalCase).where(EvalCase.agent_id == agent_id, EvalCase.status == "active")
    ).all()


# ---------------------------------------------------------------------------
# Eval executor (EV-5..EV-8)
# ---------------------------------------------------------------------------
SandboxFactory = Callable[[], Sandbox]
LLMFactory = Callable[[], LLM]


@dataclass
class TrialOutcome:
    case_id: str
    trial: int
    passed: bool
    reason: str
    run_id: str


def _seed_sandbox_files(sandbox: Sandbox, version: AgentVersion) -> None:
    """SB-1/SB-3: every fresh sandbox (one per eval trial) is seeded from
    `version.files`, the same mechanism `chat_runtime.get_or_create_sandbox`
    uses for a conversation's sandbox -- without this, eval trials for an
    agent whose version has files (e.g. the data-analyst template's
    orders.csv) would run against an empty workspace and every
    data-dependent check would fail regardless of the model's actual answer.
    """
    for f in getattr(version, "files", None) or []:
        try:
            content = read_file_bytes(f["path_on_disk"])
        except (FileNotFoundError, KeyError, OSError):
            continue
        sandbox.seed_file(f["name"], content)


def _persist_trial_run(
    *,
    engine: Any,
    version_id: str,
    status: str,
    final_answer: str,
    steps: int,
    trace: list,
    error: Optional[str],
) -> str:
    """Persist a real `runs` row for this trial (source="eval",
    conversation_id=None per specs/01-data-model.md's runs table note:
    "conversation_id FK? Null for eval trials"). Returns the new run's id.

    Takes an explicit `engine` (rather than hardcoding `app.db.engine`) so
    callers using an isolated test engine (e.g. the `session` pytest
    fixture's in-memory DB) get trial Run rows written to THAT engine, not
    the process-global one. Opens its own short-lived `Session`, mirroring
    `app/chat_runtime.py`'s pattern of a fresh session per DB touch-point
    from background-task code -- trials run concurrently under a shared
    semaphore, so each needs its own transaction rather than sharing the
    long-lived session the HTTP handler used to create the EvalRun/EvalCase
    rows.
    """
    with Session(engine) as session:
        run = Run(
            id=new_id("run"),
            conversation_id=None,
            version_id=version_id,
            source="eval",
            status=status,
            trace=trace,
            final_answer=final_answer,
            steps=steps,
            error=error,
            finished_at=_utcnow(),
        )
        session.add(run)
        session.commit()
        session.refresh(run)
        return run.id


async def _run_one_trial(
    *,
    case: EvalCase,
    trial_index: int,
    version: AgentVersion,
    sandbox_factory: SandboxFactory,
    llm_factory: LLMFactory,
    judge_model: Optional[str],
    semaphore: asyncio.Semaphore,
    engine: Any,
) -> TrialOutcome:
    """One full trial: fresh sandbox (seeded from `version.files`, same as a
    chat conversation's sandbox per SB-1), run_turn(source="eval"), then the
    case's check. EV-8: a failed run OR a raising check both become a failed
    trial with the reason recorded -- nothing here propagates. A real `Run`
    row is persisted for every trial (even ones that error before run_turn
    produces a RunResult), so `EvalResult.run_id` always points at a row
    with the trial's actual trace/final_answer for later inspection.
    """
    async with semaphore:
        sandbox = sandbox_factory()
        _seed_sandbox_files(sandbox, version)
        try:
            async def _noop_emit(_event: dict) -> None:
                return None

            llm = llm_factory()
            result = await run_turn(
                version=version,
                history=list(case.history or []),
                sandbox=sandbox,
                emit=_noop_emit,
                source="eval",
                llm=llm,
            )

            run_row_id = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _persist_trial_run(
                    engine=engine,
                    version_id=version.id,
                    status=result.status,
                    final_answer=result.final_answer,
                    steps=result.steps,
                    trace=result.trace,
                    error=result.error,
                ),
            )

            if result.status != "succeeded":
                reason = f"run {result.status}: {result.error or 'no final answer produced'}"
                return TrialOutcome(case_id=case.id, trial=trial_index, passed=False, reason=reason, run_id=run_row_id)

            check_result = await run_check(
                case.check_type,
                result.final_answer,
                case.check_spec,
                sandbox=sandbox,
                llm=llm,
                judge_model=judge_model,
                history=case.history,
            )
            return TrialOutcome(
                case_id=case.id,
                trial=trial_index,
                passed=check_result.passed,
                reason=check_result.reason,
                run_id=run_row_id,
            )
        except Exception as e:  # EV-8 ultimate backstop for this trial
            run_row_id = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: _persist_trial_run(
                    engine=engine,
                    version_id=version.id,
                    status="failed",
                    final_answer="",
                    steps=0,
                    trace=[],
                    error=str(e),
                ),
            )
            return TrialOutcome(case_id=case.id, trial=trial_index, passed=False, reason=f"trial errored: {e}", run_id=run_row_id)
        finally:
            try:
                await sandbox.destroy()
            except Exception:
                pass


async def execute_eval_run(
    *,
    cases: list[EvalCase],
    version: AgentVersion,
    trials_per_case: int,
    sandbox_factory: SandboxFactory,
    llm_factory: LLMFactory,
    judge_model: Optional[str] = None,
    max_concurrent: int = MAX_CONCURRENT_TRIALS,
    engine: Any = None,
) -> list[TrialOutcome]:
    """EV-5: run every given case `trials_per_case` times, each trial in a
    fresh sandbox, at most `max_concurrent` trials concurrently across the
    WHOLE eval run (not per-case) via a shared `asyncio.Semaphore`.

    `engine` is where each trial's `Run` row is persisted; defaults to
    `app.db.engine` (production default) so callers/tests using an isolated
    engine (e.g. the `session` pytest fixture) can pass that engine instead.
    """
    if engine is None:
        engine = db.engine
    semaphore = asyncio.Semaphore(max_concurrent)
    tasks = [
        _run_one_trial(
            case=case,
            trial_index=trial_index,
            version=version,
            sandbox_factory=sandbox_factory,
            llm_factory=llm_factory,
            judge_model=judge_model,
            semaphore=semaphore,
            engine=engine,
        )
        for case in cases
        for trial_index in range(trials_per_case)
    ]
    return await asyncio.gather(*tasks)


def case_pass_and_flaky(trial_outcomes: list[TrialOutcome], pass_threshold: int) -> tuple[bool, bool]:
    """EV-6/EV-7 for one case's trials: passes iff passing trials >=
    pass_threshold; flaky iff it has >=1 passing AND >=1 failing trial.
    """
    passing = sum(1 for t in trial_outcomes if t.passed)
    failing = len(trial_outcomes) - passing
    case_passed = passing >= pass_threshold
    flaky = passing >= 1 and failing >= 1
    return case_passed, flaky


async def run_eval_run(
    session: Session,
    *,
    agent_id: str,
    version_id: str,
    sandbox_factory: SandboxFactory,
    llm_factory: LLMFactory,
    judge_model: Optional[str] = None,
    max_concurrent: int = MAX_CONCURRENT_TRIALS,
) -> EvalRun:
    """Full eval-run lifecycle: create the EvalRun row, run every active case
    (visible and hidden, EV-5) `trials_per_case` times on `version_id`,
    persist EvalResult rows, mark the EvalRun finished.
    """
    version = session.get(AgentVersion, version_id)
    if version is None or version.agent_id != agent_id:
        raise ValueError("version does not belong to this agent")

    policy = get_or_create_policy(session, agent_id)
    cases = list_active_cases(session, agent_id)

    eval_run = EvalRun(
        id=new_id("evr"),
        agent_id=agent_id,
        version_id=version_id,
        status="running",
        trials_per_case=policy.trials_per_case,
    )
    session.add(eval_run)
    session.commit()
    session.refresh(eval_run)

    outcomes = await execute_eval_run(
        cases=cases,
        version=version,
        trials_per_case=policy.trials_per_case,
        sandbox_factory=sandbox_factory,
        llm_factory=llm_factory,
        judge_model=judge_model,
        max_concurrent=max_concurrent,
        engine=session.get_bind(),
    )

    for outcome in outcomes:
        session.add(
            EvalResult(
                id=new_id("evres"),
                eval_run_id=eval_run.id,
                case_id=outcome.case_id,
                trial=outcome.trial,
                passed=outcome.passed,
                reason=outcome.reason,
                run_id=outcome.run_id,
            )
        )

    eval_run.status = "completed"
    eval_run.finished_at = _utcnow()
    session.add(eval_run)
    session.commit()
    session.refresh(eval_run)
    return eval_run


def eval_run_summary(session: Session, eval_run_id: str) -> dict:
    """GET /eval-runs/{id}: status + per-case trial results, including
    whether each case passed (EV-6) and was flaky (EV-7) on this run.
    """
    eval_run = session.get(EvalRun, eval_run_id)
    if eval_run is None:
        raise ValueError(f"eval_run {eval_run_id!r} not found")

    policy = get_or_create_policy(session, eval_run.agent_id)
    results = session.exec(select(EvalResult).where(EvalResult.eval_run_id == eval_run_id)).all()

    by_case: dict[str, list[EvalResult]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r)

    case_summaries = []
    for case_id, case_results in by_case.items():
        case = session.get(EvalCase, case_id)
        outcomes = [TrialOutcome(case_id=case_id, trial=r.trial, passed=r.passed, reason=r.reason, run_id=r.run_id) for r in case_results]
        case_passed, flaky = case_pass_and_flaky(outcomes, policy.pass_threshold)
        case_summaries.append(
            {
                "case_id": case_id,
                "name": case.name if case else None,
                "axis": case.axis if case else None,
                "pinned": case.pinned if case else False,
                "hidden": case.hidden if case else False,
                "passed": case_passed,
                "flaky": flaky,
                "trials": [
                    {"trial": r.trial, "passed": r.passed, "reason": r.reason, "run_id": r.run_id}
                    for r in sorted(case_results, key=lambda r: r.trial)
                ],
            }
        )

    return {
        "id": eval_run.id,
        "agent_id": eval_run.agent_id,
        "version_id": eval_run.version_id,
        "status": eval_run.status,
        "trials_per_case": eval_run.trials_per_case,
        "started_at": eval_run.started_at,
        "finished_at": eval_run.finished_at,
        "cases": case_summaries,
    }


def case_pass_map(session: Session, eval_run_id: str) -> dict[str, bool]:
    """{case_id: passed} for every case in this eval run -- the shape
    `compute_verdict` wants as `base_results`/`cand_results`.
    """
    eval_run = session.get(EvalRun, eval_run_id)
    if eval_run is None:
        raise ValueError(f"eval_run {eval_run_id!r} not found")
    policy = get_or_create_policy(session, eval_run.agent_id)

    results = session.exec(select(EvalResult).where(EvalResult.eval_run_id == eval_run_id)).all()
    by_case: dict[str, list[EvalResult]] = {}
    for r in results:
        by_case.setdefault(r.case_id, []).append(r)

    out: dict[str, bool] = {}
    for case_id, case_results in by_case.items():
        outcomes = [TrialOutcome(case_id=case_id, trial=r.trial, passed=r.passed, reason=r.reason, run_id=r.run_id) for r in case_results]
        passed, _flaky = case_pass_and_flaky(outcomes, policy.pass_threshold)
        out[case_id] = passed
    return out

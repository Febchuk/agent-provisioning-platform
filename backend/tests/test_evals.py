"""T3.3 — eval executor, draft-case history, cases_for_improver
(specs/04-feedback-and-evals.md EV-2, EV-5..EV-8, EV-11).

AC-EV-a: draft-case history ends with the user message of the run being
corrected, even inside a longer conversation.
AC-EV-b: trials [T, F, T], threshold 2 -> case passes AND is flaky.
AC-EV-e: 8 cases x 3 trials -> never more than 4 sandboxes exist concurrently
(a counting test-double sandbox factory, no real Docker).
AC-EV-f: cases_for_improver() returns none of the hidden cases.
"""
import asyncio
import json

import pytest

from app.evals import (
    TrialOutcome,
    build_draft_history,
    case_pass_and_flaky,
    cases_for_improver,
    create_feedback,
    draft_case_from_feedback,
    execute_eval_run,
    run_eval_run,
)
from app.ids import new_id
from app.llm import ChatResponse, FakeLLM
from app.models import AgentVersion, EvalResult, EvalRun, Message
from app.sandbox import LocalSandbox
from app.services import create_agent, create_conversation, create_eval_case, create_version, deploy_version

pytestmark = pytest.mark.asyncio


def _add_message(session, conversation_id, seq, role, content, run_id=None):
    msg = Message(id=new_id("msg"), conversation_id=conversation_id, seq=seq, role=role, content=content, run_id=run_id)
    session.add(msg)
    session.commit()
    return msg


def _add_run(session, *, conversation_id, version_id, final_answer="an answer", status="succeeded"):
    from app.models import Run

    run = Run(
        id=new_id("run"),
        conversation_id=conversation_id,
        version_id=version_id,
        source="chat",
        status=status,
        final_answer=final_answer,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


# ---------------------------------------------------------------------------
# AC-EV-a
# ---------------------------------------------------------------------------
async def test_draft_case_history_ends_with_turn2_user_message(session):
    """3-turn conversation; feedback is on the run that answered turn 2.
    The drafted case's history must end with turn 2's user message -- NOT
    include turn 2's assistant answer, turn 3's question, or anything after.
    """
    agent = create_agent(session, name="Draft History Agent", slug="draft-history-agent")
    v1 = create_version(session, agent_id=agent.id, system_prompt="You are helpful.")
    deploy_version(session, agent_id=agent.id, version_id=v1.id)
    conversation = create_conversation(session, agent_id=agent.id, channel="share")

    # Turn 1
    _add_message(session, conversation.id, 0, "user", "turn1 question")
    run1 = _add_run(session, conversation_id=conversation.id, version_id=v1.id, final_answer="turn1 answer")
    _add_message(session, conversation.id, 1, "assistant", "turn1 answer", run_id=run1.id)

    # Turn 2 (the one feedback is about)
    _add_message(session, conversation.id, 2, "user", "turn2 question")
    run2 = _add_run(session, conversation_id=conversation.id, version_id=v1.id, final_answer="turn2 answer (wrong)")
    _add_message(session, conversation.id, 3, "assistant", "turn2 answer (wrong)", run_id=run2.id)

    # Turn 3 (after the feedback's run -- must NOT appear in the draft history)
    _add_message(session, conversation.id, 4, "user", "turn3 question")
    run3 = _add_run(session, conversation_id=conversation.id, version_id=v1.id, final_answer="turn3 answer")
    _add_message(session, conversation.id, 5, "assistant", "turn3 answer", run_id=run3.id)

    feedback = create_feedback(
        session, run_id=run2.id, conversation_id=conversation.id, rating="down", correction="should have said X"
    )

    history = build_draft_history(session, feedback)

    assert history[-1] == {"role": "user", "content": "turn2 question"}
    history_text = json.dumps(history)
    assert "turn3 question" not in history_text
    assert "turn2 answer (wrong)" not in history_text
    # turn 1 IS part of the conversation prefix leading up to turn 2's question.
    assert "turn1 question" in history_text


async def test_draft_case_from_feedback_uses_llm_judge_and_property_rubric(session):
    agent = create_agent(session, name="Draft Agent", slug="draft-agent")
    v1 = create_version(session, agent_id=agent.id, system_prompt="You are helpful.")
    deploy_version(session, agent_id=agent.id, version_id=v1.id)
    conversation = create_conversation(session, agent_id=agent.id, channel="share")

    _add_message(session, conversation.id, 0, "user", "What was Q3 revenue?")
    run = _add_run(session, conversation_id=conversation.id, version_id=v1.id, final_answer="$448,000 (includes refunds)")
    _add_message(session, conversation.id, 1, "assistant", "$448,000 (includes refunds)", run_id=run.id)

    feedback = create_feedback(
        session,
        run_id=run.id,
        conversation_id=conversation.id,
        rating="down",
        correction="Refunds shouldn't count toward revenue.",
    )

    draft_json = {
        "name": "Q3 revenue excludes refunds",
        "axis": "accuracy",
        "check_type": "llm_judge",
        "rubric": "Passes if the stated revenue figure excludes orders with status 'refunded'.",
    }
    llm = FakeLLM([ChatResponse(content=json.dumps(draft_json))])

    draft = await draft_case_from_feedback(session, feedback.id, llm)

    assert draft.check_type == "llm_judge"
    assert draft.from_feedback_id == feedback.id
    assert draft.history[-1] == {"role": "user", "content": "What was Q3 revenue?"}
    # EV-3: rubric describes a property, not the specific value the user never stated.
    assert "412" not in draft.rubric  # no invented specific dollar figure
    assert "refund" in draft.rubric.lower()


# ---------------------------------------------------------------------------
# AC-EV-b
# ---------------------------------------------------------------------------
def test_trials_pass_and_flaky():
    outcomes = [
        TrialOutcome(case_id="c1", trial=0, passed=True, reason="ok", run_id="r1"),
        TrialOutcome(case_id="c1", trial=1, passed=False, reason="no", run_id="r2"),
        TrialOutcome(case_id="c1", trial=2, passed=True, reason="ok", run_id="r3"),
    ]
    passed, flaky = case_pass_and_flaky(outcomes, pass_threshold=2)
    assert passed is True
    assert flaky is True


def test_trials_all_pass_not_flaky():
    outcomes = [TrialOutcome(case_id="c1", trial=i, passed=True, reason="ok", run_id=f"r{i}") for i in range(3)]
    passed, flaky = case_pass_and_flaky(outcomes, pass_threshold=2)
    assert passed is True
    assert flaky is False


def test_trials_all_fail_not_flaky_and_fails():
    outcomes = [TrialOutcome(case_id="c1", trial=i, passed=False, reason="no", run_id=f"r{i}") for i in range(3)]
    passed, flaky = case_pass_and_flaky(outcomes, pass_threshold=2)
    assert passed is False
    assert flaky is False


# ---------------------------------------------------------------------------
# AC-EV-e: concurrency cap, no real Docker -- a counting sandbox test double.
# ---------------------------------------------------------------------------
class _CountingSandbox:
    """A LocalSandbox wrapper that tracks how many instances are alive at
    once (via a shared counter class attribute), so the test can assert the
    concurrency cap without needing real Docker.
    """

    live = 0
    max_live = 0
    _lock = asyncio.Lock()

    def __init__(self):
        self._inner = LocalSandbox()
        self.id = self._inner.id

    @classmethod
    def reset(cls):
        cls.live = 0
        cls.max_live = 0

    async def _mark_created(self):
        async with self._lock:
            type(self).live += 1
            type(self).max_live = max(type(self).max_live, type(self).live)

    async def exec(self, cmd, timeout_s):
        return await self._inner.exec(cmd, timeout_s)

    async def read(self, path):
        return await self._inner.read(path)

    async def write(self, path, content):
        return await self._inner.write(path, content)

    async def list(self, path="."):
        return await self._inner.list(path)

    async def destroy(self):
        await self._inner.destroy()
        async with self._lock:
            type(self).live -= 1


def _counting_sandbox_factory():
    sb = _CountingSandbox()
    # Fire-and-track creation synchronously isn't possible (async lock), so
    # bump the counter eagerly here (sandbox "exists" from construction) and
    # let destroy() decrement it -- matches "no more than 4 sandboxes exist
    # concurrently" (existence window, not just exec window).
    _CountingSandbox.live += 1
    _CountingSandbox.max_live = max(_CountingSandbox.max_live, _CountingSandbox.live)
    return sb


class _AlwaysPassLLM:
    async def chat(self, messages, tools=None, model=None, temperature=0, response_format=None):
        return ChatResponse(content="the final answer", tool_calls=None)


from dataclasses import dataclass, field
from typing import Optional


@dataclass
class _FakeVersion:
    id: str = "v_fake"
    agent_id: str = "ag_x"
    system_prompt: str = "You are helpful."
    guidelines: list = field(default_factory=list)
    tools: list = field(default_factory=list)
    model: Optional[str] = None
    max_steps: int = 5
    tool_timeout_s: int = 10
    number: int = 1


def _fake_version(agent_id="ag_x"):
    return _FakeVersion(agent_id=agent_id)


def _isolated_engine():
    """A private in-memory engine for tests that call `execute_eval_run`
    directly (not through the `session` fixture) -- so trial `Run` rows this
    test's `_persist_trial_run` writes never touch the real
    `backend/data/app.db` file.
    """
    from sqlalchemy.pool import StaticPool
    from sqlmodel import SQLModel, create_engine

    from app import models  # noqa: F401  (register table metadata)

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(engine)
    return engine


async def test_concurrency_never_exceeds_4_sandboxes():
    _CountingSandbox.reset()

    cases = []
    for i in range(8):
        cases.append(
            _MockCase(
                id=f"case_{i}",
                history=[{"role": "user", "content": f"question {i}"}],
                check_type="contains",
                check_spec={"all": [], "none": []},
            )
        )

    outcomes = await execute_eval_run(
        cases=cases,
        version=_fake_version(),
        trials_per_case=3,
        sandbox_factory=_counting_sandbox_factory,
        llm_factory=lambda: _AlwaysPassLLM(),
        max_concurrent=4,
        engine=_isolated_engine(),
    )

    assert len(outcomes) == 8 * 3
    assert _CountingSandbox.max_live <= 4, f"max concurrent sandboxes was {_CountingSandbox.max_live}, expected <= 4"
    assert _CountingSandbox.live == 0  # all destroyed by the end


class _MockCase:
    """Minimal stand-in for an EvalCase row (execute_eval_run only reads
    these attributes), used so AC-EV-e doesn't need to persist 8 real cases.
    """

    def __init__(self, *, id, history, check_type, check_spec):
        self.id = id
        self.history = history
        self.check_type = check_type
        self.check_spec = check_spec


# ---------------------------------------------------------------------------
# EV-8: a failed run, and a check that raises, both count as a failed trial
# without propagating.
# ---------------------------------------------------------------------------
class _FailingRunLLM:
    """Raises on every call so run_turn's retry-then-fail path returns
    status='failed' (RT-5) -- used to prove a failed run becomes a failed
    trial with the reason recorded (EV-8), not a crashed eval run.
    """

    async def chat(self, **kwargs):
        raise RuntimeError("model unavailable")


async def test_failed_run_counts_as_failed_trial_with_reason():
    case = _MockCase(id="case_fail", history=[{"role": "user", "content": "q"}], check_type="contains", check_spec={"all": [], "none": []})

    outcomes = await execute_eval_run(
        cases=[case],
        version=_fake_version(),
        trials_per_case=1,
        sandbox_factory=lambda: LocalSandbox(),
        llm_factory=lambda: _FailingRunLLM(),
        max_concurrent=4,
        engine=_isolated_engine(),
    )

    assert len(outcomes) == 1
    assert outcomes[0].passed is False
    assert "failed" in outcomes[0].reason.lower() or "error" in outcomes[0].reason.lower()


class _RaisingCheckCase(_MockCase):
    pass


async def test_check_exception_counts_as_failed_trial_not_a_crash(monkeypatch):
    import app.evals as evals_module

    async def _boom(*args, **kwargs):
        raise RuntimeError("check exploded")

    monkeypatch.setattr(evals_module, "run_check", _boom)

    case = _MockCase(id="case_boom", history=[{"role": "user", "content": "q"}], check_type="contains", check_spec={})

    outcomes = await execute_eval_run(
        cases=[case],
        version=_fake_version(),
        trials_per_case=1,
        sandbox_factory=lambda: LocalSandbox(),
        llm_factory=lambda: _AlwaysPassLLM(),
        max_concurrent=4,
        engine=_isolated_engine(),
    )

    assert len(outcomes) == 1
    assert outcomes[0].passed is False
    assert "check exploded" in outcomes[0].reason or "errored" in outcomes[0].reason.lower()


# ---------------------------------------------------------------------------
# Full run_eval_run through the DB (EV-5, EV-6) -- small scale, LocalSandbox.
# ---------------------------------------------------------------------------
async def test_run_eval_run_persists_results_and_respects_pass_threshold(session):
    agent = create_agent(session, name="Exec Agent", slug="exec-agent")
    v1 = create_version(session, agent_id=agent.id, system_prompt="You are helpful.", tools=[])
    deploy_version(session, agent_id=agent.id, version_id=v1.id)

    case = create_eval_case(
        session,
        agent_id=agent.id,
        name="always passes",
        check_type="contains",
        check_spec={"all": ["final"], "none": []},
        history=[{"role": "user", "content": "say something containing the word final"}],
        status="active",
    )

    eval_run = await run_eval_run(
        session,
        agent_id=agent.id,
        version_id=v1.id,
        sandbox_factory=lambda: LocalSandbox(),
        llm_factory=lambda: _AlwaysPassLLM(),
    )

    assert eval_run.status == "completed"
    results = session.exec(
        __import__("sqlmodel").select(EvalResult).where(EvalResult.eval_run_id == eval_run.id)
    ).all()
    assert len(results) == 3  # default trials_per_case
    assert all(r.passed for r in results)  # "the final answer" contains "final"
    assert all(r.case_id == case.id for r in results)


# ---------------------------------------------------------------------------
# AC-EV-f / EV-11
# ---------------------------------------------------------------------------
def test_cases_for_improver_excludes_hidden(session):
    """DM-4 (v2 rewritten): cases_for_improver() never returns
    `split = benchmark` cases (replaces v1's `hidden` column)."""
    agent = create_agent(session, name="Improver Cases Agent", slug="improver-cases-agent")

    visible_active = create_eval_case(
        session, agent_id=agent.id, name="visible active", check_type="contains", check_spec={"all": [], "none": []}, status="active"
    )
    visible_draft = create_eval_case(
        session, agent_id=agent.id, name="visible draft", check_type="contains", check_spec={"all": [], "none": []}, status="draft"
    )
    benchmark_sibling = create_eval_case(
        session,
        agent_id=agent.id,
        name="benchmark sibling",
        check_type="llm_judge",
        check_spec={"rubric": "x"},
        split="benchmark",
        origin="variant",
        status="active",
    )

    result = cases_for_improver(session, agent.id)
    result_ids = {c.id for c in result}

    assert visible_active.id in result_ids
    assert visible_draft.id not in result_ids  # not active
    assert benchmark_sibling.id not in result_ids  # DM-4: benchmark split excluded
    assert all(c.split == "improve" for c in result)

"""Improver pipeline (specs/05-improver.md T4.4, IM-1, IM-2, IM-5, IM-6,
IM-11, IM-12). Uses FakeLLM + LocalSandbox only -- no Docker/network
(specs/07 §1).

AC-IM-f: FakeLLM returns invalid JSON twice -> proposal status `failed` with
error stored.
IM-2: flaky failing cases are skipped (reason "flaky"), not targets.
IM-6: zero ops -> `ready`, no candidate.
IM-11: all ops dropped by lint -> no candidate, `ready`, reason recorded.
IM-1: a prior completed eval run for the same version is reused rather than
re-run.
IM-12: a candidate's eval run uses the same case set + trial count as base.
"""
from __future__ import annotations

import json

import pytest

from app.evals import get_or_create_policy
from app.improver import (
    create_proposal,
    run_proposal_pipeline,
    triage,
)
from app.llm import ChatResponse, FakeLLM
from app.models import EvalRun
from app.sandbox import LocalSandbox
from app.services import create_agent, create_eval_case, create_version, deploy_version

pytestmark = pytest.mark.asyncio


def _sandbox_factory():
    return LocalSandbox()


def _make_llm_factory(responses_list):
    """Returns a factory producing a fresh FakeLLM with the given scripted
    responses every time it's called -- the eval executor calls
    `llm_factory()` once per trial, so each trial needs its own FakeLLM
    pre-loaded with enough responses for one full run_turn.
    """

    def factory():
        return FakeLLM(list(responses_list))

    return factory


def _answer(text):
    return ChatResponse(content=text, tool_calls=None)


async def _setup_agent_with_case(session, *, final_answer_script, check_spec, check_type="contains", axis="accuracy", pinned=False):
    agent = create_agent(session, name="Improver Test Agent", slug=f"improver-test-{id(session)}")
    v1 = create_version(
        session,
        agent_id=agent.id,
        system_prompt="You are a data analyst.",
        guidelines=[{"id": "g_1", "section": "General", "text": "Be concise."}],
    )
    deploy_version(session, agent_id=agent.id, version_id=v1.id)
    case = create_eval_case(
        session,
        agent_id=agent.id,
        name="Revenue case",
        axis=axis,
        check_type=check_type,
        check_spec=check_spec,
        history=[{"role": "user", "content": "What was the total revenue in Q3?"}],
        pinned=pinned,
        status="active",
    )
    return agent, v1, case


# ---------------------------------------------------------------------------
# AC-IM-f: invalid JSON twice -> failed.
# ---------------------------------------------------------------------------
async def test_ac_im_f_invalid_json_twice_marks_proposal_failed(session):
    agent, v1, case = await _setup_agent_with_case(
        session, final_answer_script=None, check_spec={"all": ["17819.86"], "none": []}
    )

    # base eval: all 3 trials answer wrong -> case fails 0/3 (a clean target).
    eval_llm_factory = _make_llm_factory([_answer("I don't know.")])

    class _BadImproverLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            return ChatResponse(content="not valid json{{{", tool_calls=None)

    bad_improver_llm = _BadImproverLLM()

    proposal = create_proposal(session, agent_id=agent.id, base_version_id=v1.id)
    result = await run_proposal_pipeline(
        session,
        agent_id=agent.id,
        proposal_id=proposal.id,
        sandbox_factory=_sandbox_factory,
        llm_factory=eval_llm_factory,
        improver_llm_factory=lambda: bad_improver_llm,
    )

    assert result.status == "failed"
    assert result.decision_note is not None
    assert "invalid" in result.decision_note.lower()
    assert bad_improver_llm.calls == 2  # IM-5: one retry


# ---------------------------------------------------------------------------
# IM-6: zero ops -> ready, no candidate.
# ---------------------------------------------------------------------------
async def test_im_6_zero_ops_marks_ready_no_candidate(session):
    agent, v1, case = await _setup_agent_with_case(
        session, final_answer_script=None, check_spec={"all": ["17819.86"], "none": []}
    )
    eval_llm_factory = _make_llm_factory([_answer("I don't know.")])

    no_ops_json = json.dumps(
        {
            "diagnoses": [
                {"case_id": case.id, "root_cause": "case_is_wrong", "agent_fault": False, "lesson": "n/a"}
            ],
            "ops": [],
            "skipped": [],
        }
    )

    class _NoOpsLLM:
        async def chat(self, **kwargs):
            return ChatResponse(content=no_ops_json, tool_calls=None)

    proposal = create_proposal(session, agent_id=agent.id, base_version_id=v1.id)
    result = await run_proposal_pipeline(
        session,
        agent_id=agent.id,
        proposal_id=proposal.id,
        sandbox_factory=_sandbox_factory,
        llm_factory=eval_llm_factory,
        improver_llm_factory=lambda: _NoOpsLLM(),
    )

    assert result.status == "ready"
    assert result.candidate_version_id is None
    assert result.ops == []
    assert len(result.diagnoses) == 1


# ---------------------------------------------------------------------------
# IM-11: all ops dropped by lint -> ready, no candidate.
# ---------------------------------------------------------------------------
async def test_im_11_all_ops_dropped_by_lint_marks_ready_no_candidate(session):
    agent, v1, case = await _setup_agent_with_case(
        session, final_answer_script=None, check_spec={"all": ["17819.86"], "none": []}
    )
    eval_llm_factory = _make_llm_factory([_answer("I don't know.")])

    # The op's text literally copies "Q3" from the case's question -- a
    # 5-gram ("what was the total revenue") will also be shared outright.
    bad_op_json = json.dumps(
        {
            "diagnoses": [
                {"case_id": case.id, "root_cause": "missing_rule", "agent_fault": True, "lesson": "x"}
            ],
            "ops": [
                {
                    "op": "add",
                    "section": "Revenue",
                    "text": "For Q3, what was the total revenue should always exclude refunds",
                    "addresses": [case.id],
                    "why": "x",
                }
            ],
            "skipped": [],
        }
    )

    class _BadOpLLM:
        async def chat(self, **kwargs):
            return ChatResponse(content=bad_op_json, tool_calls=None)

    proposal = create_proposal(session, agent_id=agent.id, base_version_id=v1.id)
    result = await run_proposal_pipeline(
        session,
        agent_id=agent.id,
        proposal_id=proposal.id,
        sandbox_factory=_sandbox_factory,
        llm_factory=eval_llm_factory,
        improver_llm_factory=lambda: _BadOpLLM(),
    )

    assert result.status == "ready"
    assert result.candidate_version_id is None
    assert result.decision_note == "all edits rejected by lint"
    assert len(result.lint) == 1
    assert result.lint[0]["passed"] is False


# ---------------------------------------------------------------------------
# IM-2: flaky failing cases are skipped, not targets.
# ---------------------------------------------------------------------------
async def test_im_2_flaky_case_is_skipped_not_targeted(session):
    """2/3 trials fail, 1/3 passes -> flaky, not a target (per case_pass_and_flaky
    semantics: >=1 pass AND >=1 fail with pass_threshold=2 means it FAILS
    overall (1 < 2) but is flaky, so triage must route it to `skipped`, not
    `targets`.
    """
    agent, v1, case = await _setup_agent_with_case(
        session, final_answer_script=None, check_spec={"all": ["17819.86"], "none": []}
    )

    # One passing trial, two failing -- script 3 separate FakeLLM instances
    # (one per trial) via a factory that pops from a shared list.
    scripts = [[_answer("17819.86")], [_answer("I don't know.")], [_answer("I don't know.")]]

    def eval_llm_factory():
        return FakeLLM(scripts.pop(0))

    policy = get_or_create_policy(session, agent.id)
    from app.evals import run_eval_run

    base_eval_run = await run_eval_run(
        session,
        agent_id=agent.id,
        version_id=v1.id,
        sandbox_factory=_sandbox_factory,
        llm_factory=eval_llm_factory,
    )

    triage_result = triage(session, agent_id=agent.id, base_eval_run=base_eval_run, target_axis="accuracy")
    assert triage_result.targets == []
    assert triage_result.skipped_flaky == [{"case_id": case.id, "reason": "flaky"}]


# ---------------------------------------------------------------------------
# IM-1: a prior completed eval run for the same version is reused.
# ---------------------------------------------------------------------------
async def test_im_1_reuses_existing_base_eval_run(session):
    agent, v1, case = await _setup_agent_with_case(
        session, final_answer_script=None, check_spec={"all": ["17819.86"], "none": []}
    )
    eval_llm_factory = _make_llm_factory([_answer("17819.86")])

    from app.evals import run_eval_run

    existing_run = await run_eval_run(
        session, agent_id=agent.id, version_id=v1.id, sandbox_factory=_sandbox_factory, llm_factory=eval_llm_factory
    )

    no_ops_json = json.dumps({"diagnoses": [], "ops": [], "skipped": []})

    class _NoOpsLLM:
        async def chat(self, **kwargs):
            return ChatResponse(content=no_ops_json, tool_calls=None)

    def should_not_be_called():
        raise AssertionError("eval_llm_factory should not be called again -- base run should be reused")

    proposal = create_proposal(session, agent_id=agent.id, base_version_id=v1.id)
    result = await run_proposal_pipeline(
        session,
        agent_id=agent.id,
        proposal_id=proposal.id,
        sandbox_factory=_sandbox_factory,
        llm_factory=should_not_be_called,  # would raise if a NEW base run were started
        improver_llm_factory=lambda: _NoOpsLLM(),
    )

    assert result.base_eval_run_id == existing_run.id
    assert result.status == "ready"


# ---------------------------------------------------------------------------
# Full happy path with a real op that survives lint -> candidate -> verdict.
# ---------------------------------------------------------------------------
async def test_full_pipeline_produces_candidate_and_verdict(session):
    agent, v1, case = await _setup_agent_with_case(
        session, final_answer_script=None, check_spec={"all": ["17819.86"], "none": ["refunded orders included"]}
    )

    # Base eval: 3 trials, all wrong (includes refunds, wrong number).
    base_scripts = [[_answer("Total revenue was 19074.74, refunded orders included.")] for _ in range(3)]

    def base_llm_factory():
        return FakeLLM(base_scripts.pop(0))

    good_op_json = json.dumps(
        {
            "diagnoses": [
                {
                    "case_id": case.id,
                    "root_cause": "missing_rule",
                    "agent_fault": True,
                    "lesson": "Revenue figures must exclude refunded orders.",
                }
            ],
            "ops": [
                {
                    "op": "add",
                    "section": "Revenue rules",
                    "text": "Exclude refunded orders from revenue.",
                    "addresses": [case.id],
                    "why": "Refund handling was never specified.",
                }
            ],
            "skipped": [],
        }
    )

    class _GoodOpLLM:
        async def chat(self, **kwargs):
            return ChatResponse(content=good_op_json, tool_calls=None)

    # After the candidate is created, the pipeline runs ANOTHER eval (on the
    # candidate) -- script those 3 trials to answer correctly.
    cand_scripts = [[_answer("Total revenue was 17819.86.")] for _ in range(3)]
    all_scripts = {"base_done": False}

    call_count = {"n": 0}

    def combined_llm_factory():
        call_count["n"] += 1
        if call_count["n"] <= 3:
            return FakeLLM(base_scripts[call_count["n"] - 1])
        return FakeLLM(cand_scripts[call_count["n"] - 4])

    proposal = create_proposal(session, agent_id=agent.id, base_version_id=v1.id)
    result = await run_proposal_pipeline(
        session,
        agent_id=agent.id,
        proposal_id=proposal.id,
        sandbox_factory=_sandbox_factory,
        llm_factory=combined_llm_factory,
        improver_llm_factory=lambda: _GoodOpLLM(),
    )

    assert result.status == "ready"
    assert result.candidate_version_id is not None
    assert result.verdict is not None
    assert result.verdict["meets_policy"] is True
    assert any(c["id"] == case.id for c in result.verdict["fixed"])

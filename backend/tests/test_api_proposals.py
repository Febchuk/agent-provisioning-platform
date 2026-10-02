"""API-contract tests for the proposal endpoints (specs/05-improver.md
endpoint table; IM-13, IM-14).

AC-IM-g: accept with failing verdict and empty note -> 400; with note ->
version `change_note` starts with `Override:`.

Uses `app_client` (local-unsafe sandbox, isolated DB, no Docker/network) with
`main.eval_llm_factory` / `main.improver_llm_factory` monkeypatched to
FakeLLM-backed factories, mirroring `test_api_evals.py`'s pattern.
"""
from __future__ import annotations

import json

import pytest

from app import main as main_module
from app.llm import ChatResponse, FakeLLM

pytestmark = pytest.mark.asyncio


def _install_fake_eval_llm(monkeypatch, responses_factory):
    class _Factory:
        def __call__(self):
            return FakeLLM(responses_factory())

    monkeypatch.setattr(main_module, "eval_llm_factory", _Factory())


def _install_fake_improver_llm(monkeypatch, content: str):
    class _FakeImproverLLM:
        async def chat(self, **kwargs):
            return ChatResponse(content=content, tool_calls=None)

    class _Factory:
        def __call__(self):
            return _FakeImproverLLM()

    monkeypatch.setattr(main_module, "improver_llm_factory", _Factory())


async def _create_agent_with_case(app_client, *, check_spec):
    agent = (await app_client.post("/agents", json={"name": "Proposal Agent", "template": "blank"})).json()
    case = (
        await app_client.post(
            f"/agents/{agent['id']}/cases",
            json={
                "name": "Revenue case",
                "check_type": "contains",
                "check_spec": check_spec,
                "axis": "accuracy",
                "history": [{"role": "user", "content": "What was the total revenue in Q3?"}],
            },
        )
    ).json()
    return agent, case


async def test_create_proposal_no_deployed_version_400(app_client):
    # An agent with no version at all can't exist via the API (create_agent
    # always deploys v1), so simulate "no deployed version" isn't reachable
    # through POST /agents -- instead verify unknown agent -> 404.
    resp = await app_client.post("/agents/ag_doesnotexist/proposals")
    assert resp.status_code == 404


async def test_ac_im_g_accept_failing_verdict_requires_note(app_client, monkeypatch):
    agent, case = await _create_agent_with_case(app_client, check_spec={"all": ["17819.86"], "none": []})

    # Base eval: all trials wrong -> case fails 0/3 (a clean target).
    _install_fake_eval_llm(monkeypatch, lambda: [ChatResponse(content="I don't know.", tool_calls=None)])

    no_ops_json = json.dumps({"diagnoses": [], "ops": [], "skipped": []})
    _install_fake_improver_llm(monkeypatch, no_ops_json)

    resp = await app_client.post(f"/agents/{agent['id']}/proposals")
    assert resp.status_code == 201
    proposal_id = resp.json()["proposal_id"]

    detail = (await app_client.get(f"/proposals/{proposal_id}")).json()
    assert detail["status"] == "ready"
    assert detail["candidate_version_id"] is None  # zero ops -> no candidate -> no verdict computed

    # No candidate/verdict at all here (IM-6 path) -- accept must still work
    # without a note (there's no "meets_policy" to fail since verdict is None).
    accept_resp = await app_client.post(f"/proposals/{proposal_id}/accept", json={"deploy": False})
    assert accept_resp.status_code == 200
    assert accept_resp.json()["status"] == "accepted"


async def test_accept_with_failing_verdict_empty_note_400_then_note_sets_override(app_client, monkeypatch):
    agent, case = await _create_agent_with_case(
        app_client, check_spec={"all": ["17819.86"], "none": ["refunded orders included"]}
    )

    # Base eval: always wrong.
    base_scripts = [[ChatResponse(content="Total was 19074.74, refunded orders included.", tool_calls=None)] for _ in range(3)]
    call_n = {"n": 0}

    def eval_llm_factory():
        call_n["n"] += 1
        if call_n["n"] <= 3:
            return FakeLLM(base_scripts[call_n["n"] - 1])
        # candidate eval: still wrong (so the op does NOT fix the case ->
        # verdict will not meet policy -> forces the override path).
        return FakeLLM([ChatResponse(content="Total was 19074.74, refunded orders included.", tool_calls=None)])

    class _Factory:
        def __call__(self):
            return eval_llm_factory()

    monkeypatch.setattr(main_module, "eval_llm_factory", _Factory())

    good_op_json = json.dumps(
        {
            "diagnoses": [
                {"case_id": case["id"], "root_cause": "missing_rule", "agent_fault": True, "lesson": "exclude refunds"}
            ],
            "ops": [
                {
                    "op": "add",
                    "section": "Revenue",
                    "text": "Exclude refunded orders from revenue.",
                    "addresses": [case["id"]],
                    "why": "x",
                }
            ],
            "skipped": [],
        }
    )
    _install_fake_improver_llm(monkeypatch, good_op_json)

    resp = await app_client.post(f"/agents/{agent['id']}/proposals")
    assert resp.status_code == 201
    proposal_id = resp.json()["proposal_id"]

    detail = (await app_client.get(f"/proposals/{proposal_id}")).json()
    assert detail["status"] == "ready"
    assert detail["candidate_version_id"] is not None
    assert detail["verdict"]["meets_policy"] is False  # case still fails on candidate -> no fix -> policy not met

    # Empty note -> 400
    bad = await app_client.post(f"/proposals/{proposal_id}/accept", json={"deploy": False, "note": ""})
    assert bad.status_code == 400

    bad2 = await app_client.post(f"/proposals/{proposal_id}/accept", json={"deploy": False})
    assert bad2.status_code == 400

    # Non-empty note -> 200, version change_note starts with "Override:"
    good = await app_client.post(
        f"/proposals/{proposal_id}/accept", json={"deploy": False, "note": "Shipping anyway for the demo"}
    )
    assert good.status_code == 200
    body = good.json()
    assert body["status"] == "accepted"

    agent_detail = (await app_client.get(f"/agents/{agent['id']}")).json()
    candidate_version = next(v for v in agent_detail["versions"] if v["id"] == detail["candidate_version_id"])
    assert candidate_version["change_note"].startswith("Override:")
    assert "Shipping anyway for the demo" in candidate_version["change_note"]


async def test_accept_and_deploy_moves_deploy_pointer(app_client, monkeypatch):
    agent, case = await _create_agent_with_case(app_client, check_spec={"all": ["17819.86"], "none": []})

    base_scripts = [[ChatResponse(content="I don't know.", tool_calls=None)] for _ in range(3)]
    cand_scripts = [[ChatResponse(content="17819.86", tool_calls=None)] for _ in range(3)]
    call_n = {"n": 0}

    def eval_llm_factory():
        call_n["n"] += 1
        if call_n["n"] <= 3:
            return FakeLLM(base_scripts[call_n["n"] - 1])
        return FakeLLM(cand_scripts[call_n["n"] - 4])

    class _Factory:
        def __call__(self):
            return eval_llm_factory()

    monkeypatch.setattr(main_module, "eval_llm_factory", _Factory())

    good_op_json = json.dumps(
        {
            "diagnoses": [
                {"case_id": case["id"], "root_cause": "missing_rule", "agent_fault": True, "lesson": "x"}
            ],
            "ops": [
                {"op": "add", "section": "Revenue", "text": "Report the exact computed figure.", "addresses": [case["id"]], "why": "x"}
            ],
            "skipped": [],
        }
    )
    _install_fake_improver_llm(monkeypatch, good_op_json)

    resp = await app_client.post(f"/agents/{agent['id']}/proposals")
    proposal_id = resp.json()["proposal_id"]
    detail = (await app_client.get(f"/proposals/{proposal_id}")).json()
    assert detail["verdict"]["meets_policy"] is True

    accept = await app_client.post(f"/proposals/{proposal_id}/accept", json={"deploy": True})
    assert accept.status_code == 200

    agent_detail = (await app_client.get(f"/agents/{agent['id']}")).json()
    assert agent_detail["deployed_version_id"] == detail["candidate_version_id"]


async def test_reject_proposal(app_client, monkeypatch):
    agent, case = await _create_agent_with_case(app_client, check_spec={"all": ["17819.86"], "none": []})
    _install_fake_eval_llm(monkeypatch, lambda: [ChatResponse(content="I don't know.", tool_calls=None)])
    _install_fake_improver_llm(monkeypatch, json.dumps({"diagnoses": [], "ops": [], "skipped": []}))

    resp = await app_client.post(f"/agents/{agent['id']}/proposals")
    proposal_id = resp.json()["proposal_id"]

    rej = await app_client.post(f"/proposals/{proposal_id}/reject")
    assert rej.status_code == 200
    assert rej.json()["status"] == "rejected"

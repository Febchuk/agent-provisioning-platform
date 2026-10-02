"""API-contract tests for feedback/cases/eval-runs/policy endpoints
(specs/04-feedback-and-evals.md endpoint table; specs/07 §1 "API contract"
row: httpx AsyncClient, no network/Docker -- FakeLLM + LocalSandbox via the
app_client fixture).
"""
import json

import pytest

from app import evals as evals_module
from app.llm import ChatResponse, FakeLLM
from app import chat_runtime, main as main_module

pytestmark = pytest.mark.asyncio


def _install_fake_chat_llm(monkeypatch, responses):
    class _Factory:
        def __call__(self):
            return FakeLLM(list(responses))

    monkeypatch.setattr(chat_runtime, "llm_factory", _Factory())


def _install_fake_eval_llm(monkeypatch, responses):
    class _Factory:
        def __call__(self):
            return FakeLLM(list(responses))

    monkeypatch.setattr(main_module, "eval_llm_factory", _Factory())


async def _wait_for_run_done(app_client, run_id: str) -> dict:
    async with app_client.stream("GET", f"/runs/{run_id}/events") as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: ") :])
            if event["type"] == "run.done":
                return event


async def _create_agent_with_one_chat_run(app_client, monkeypatch, answer="hello back"):
    agent = (await app_client.post("/agents", json={"name": "Feedback Agent", "template": "blank"})).json()
    conversation_id = (await app_client.post(f"/share/{agent['slug']}/conversations")).json()["conversation_id"]
    _install_fake_chat_llm(monkeypatch, [ChatResponse(content=answer, tool_calls=None)])
    run_id = (await app_client.post(f"/conversations/{conversation_id}/messages", json={"content": "hi"})).json()["run_id"]
    await _wait_for_run_done(app_client, run_id)
    return agent, conversation_id, run_id


# ---------------------------------------------------------------------------
# EV-1
# ---------------------------------------------------------------------------
async def test_post_feedback_creates_new_status(app_client, monkeypatch):
    agent, conversation_id, run_id = await _create_agent_with_one_chat_run(app_client, monkeypatch)

    resp = await app_client.post(f"/runs/{run_id}/feedback", json={"rating": "down", "correction": "wrong answer"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "new"
    assert body["run_id"] == run_id
    assert body["conversation_id"] == conversation_id

    inbox = await app_client.get(f"/agents/{agent['id']}/feedback", params={"status": "new"})
    assert inbox.status_code == 200
    assert any(f["id"] == body["id"] for f in inbox.json())


async def test_post_feedback_unknown_run_404(app_client):
    resp = await app_client.post("/runs/run_doesnotexist/feedback", json={"rating": "up"})
    assert resp.status_code == 404


async def test_dismiss_feedback(app_client, monkeypatch):
    agent, _conv, run_id = await _create_agent_with_one_chat_run(app_client, monkeypatch)
    feedback_id = (await app_client.post(f"/runs/{run_id}/feedback", json={"rating": "down"})).json()["id"]

    resp = await app_client.post(f"/feedback/{feedback_id}/dismiss")
    assert resp.status_code == 200
    assert resp.json()["status"] == "dismissed"

    inbox = await app_client.get(f"/agents/{agent['id']}/feedback", params={"status": "new"})
    assert all(f["id"] != feedback_id for f in inbox.json())


# ---------------------------------------------------------------------------
# EV-2, EV-3, EV-4
# ---------------------------------------------------------------------------
async def test_draft_case_then_confirm_creates_active_case(app_client, monkeypatch):
    agent, _conv, run_id = await _create_agent_with_one_chat_run(app_client, monkeypatch, answer="$448,000 total")
    feedback_id = (
        await app_client.post(f"/runs/{run_id}/feedback", json={"rating": "down", "correction": "exclude refunds"})
    ).json()["id"]

    draft_json = {
        "name": "Excludes refunds",
        "axis": "accuracy",
        "check_type": "llm_judge",
        "rubric": "Passes if the answer excludes refunded orders.",
    }
    _install_fake_eval_llm(monkeypatch, [ChatResponse(content=json.dumps(draft_json))])

    draft_resp = await app_client.post(f"/feedback/{feedback_id}/draft-case")
    assert draft_resp.status_code == 200
    draft = draft_resp.json()
    assert draft["status"] == "draft"
    assert draft["check_type"] == "llm_judge"

    # EV-4: drafting must NOT have created an active case yet.
    cases_before = (await app_client.get(f"/agents/{agent['id']}/cases")).json()
    assert cases_before == []

    confirm_resp = await app_client.post(
        f"/agents/{agent['id']}/cases",
        json={
            "name": draft["name"],
            "axis": draft["axis"],
            "check_type": draft["check_type"],
            "check_spec": draft["check_spec"],
            "history": draft["history"],
            "from_feedback_id": feedback_id,
        },
    )
    assert confirm_resp.status_code == 201
    case = confirm_resp.json()
    assert case["status"] == "active"

    cases_after = (await app_client.get(f"/agents/{agent['id']}/cases")).json()
    assert any(c["id"] == case["id"] for c in cases_after)

    # Confirming from a draft marks the feedback converted.
    inbox = await app_client.get(f"/agents/{agent['id']}/feedback", params={"status": "new"})
    assert all(f["id"] != feedback_id for f in inbox.json())


async def test_patch_case_updates_pinned_and_status(app_client):
    agent = (await app_client.post("/agents", json={"name": "Patch Agent", "template": "blank"})).json()
    case = (
        await app_client.post(
            f"/agents/{agent['id']}/cases",
            json={"name": "manual case", "check_type": "contains", "check_spec": {"all": ["x"], "none": []}},
        )
    ).json()

    patch_resp = await app_client.patch(f"/cases/{case['id']}", json={"pinned": True, "status": "dismissed"})
    assert patch_resp.status_code == 200
    body = patch_resp.json()
    assert body["pinned"] is True
    assert body["status"] == "dismissed"


# ---------------------------------------------------------------------------
# T3.3 / EV-5, EV-6 via the API, with LocalSandbox + FakeLLM
# ---------------------------------------------------------------------------
async def test_create_eval_run_executes_active_cases(app_client, monkeypatch):
    agent = (await app_client.post("/agents", json={"name": "Eval Run Agent", "template": "blank"})).json()
    version_id = agent["deployed_version_id"]

    case = (
        await app_client.post(
            f"/agents/{agent['id']}/cases",
            json={
                "name": "says final",
                "check_type": "contains",
                "check_spec": {"all": ["final"], "none": []},
                "history": [{"role": "user", "content": "say something with the word final"}],
            },
        )
    ).json()
    assert case["status"] == "active"

    # main.eval_llm_factory is used both for the run_turn's chat calls (via
    # run_eval_run's llm_factory) -- scripted to always answer "the final answer".
    class _AlwaysFinalFactory:
        def __call__(self):
            return FakeLLM([ChatResponse(content="the final answer", tool_calls=None) for _ in range(10)])

    monkeypatch.setattr(main_module, "eval_llm_factory", _AlwaysFinalFactory())

    resp = await app_client.post(f"/agents/{agent['id']}/eval-runs", json={"version_id": version_id})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "completed"
    assert len(body["cases"]) == 1
    case_summary = body["cases"][0]
    assert case_summary["case_id"] == case["id"]
    assert len(case_summary["trials"]) == 3  # default trials_per_case
    assert case_summary["passed"] is True

    get_resp = await app_client.get(f"/eval-runs/{body['id']}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "completed"


async def test_eval_run_unknown_version_400(app_client):
    agent = (await app_client.post("/agents", json={"name": "Bad Version Agent", "template": "blank"})).json()
    resp = await app_client.post(f"/agents/{agent['id']}/eval-runs", json={"version_id": "v_doesnotexist"})
    assert resp.status_code == 400


async def test_get_eval_run_unknown_404(app_client):
    resp = await app_client.get("/eval-runs/evr_doesnotexist")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# T3.4: policy get/put
# ---------------------------------------------------------------------------
async def test_get_policy_defaults(app_client):
    agent = (await app_client.post("/agents", json={"name": "Policy Agent", "template": "blank"})).json()
    resp = await app_client.get(f"/agents/{agent['id']}/policy")
    assert resp.status_code == 200
    body = resp.json()
    assert body["min_avg_improvement_pct"] == 5.0
    assert body["trials_per_case"] == 3
    assert body["pass_threshold"] == 2
    assert body["max_regressions"] == {"accuracy": 0, "safety": 0, "tool-use": 1, "format": 1}


async def test_put_policy_updates_fields(app_client):
    agent = (await app_client.post("/agents", json={"name": "Policy Agent 2", "template": "blank"})).json()
    resp = await app_client.put(
        f"/agents/{agent['id']}/policy",
        json={"min_avg_improvement_pct": 10.0, "max_regressions": {"accuracy": 1}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["min_avg_improvement_pct"] == 10.0
    assert body["max_regressions"] == {"accuracy": 1}
    # untouched fields keep their default
    assert body["trials_per_case"] == 3

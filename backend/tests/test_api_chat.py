"""API-contract tests for conversations/messages/runs/SSE
(specs/03-chat-and-deploy.md CD-4..CD-8; specs/07 §1 "API contract" row:
httpx AsyncClient, no network/Docker — uses FakeLLM + LocalSandbox via the
`app_client` fixture's sandbox_mode="local-unsafe").
"""
import asyncio
import json

import pytest

from app.llm import ChatResponse
from app import chat_runtime

pytestmark = pytest.mark.asyncio


class _ScriptedLLMFactory:
    """Returns a single FakeLLM (scripted with `responses`) every time it's
    called, so `chat_runtime.start_turn`'s `llm or llm_factory()` gets a
    fresh-looking but identically-scripted FakeLLM per test.
    """

    def __init__(self, responses: list[ChatResponse]):
        self._responses = responses

    def __call__(self):
        from app.llm import FakeLLM

        return FakeLLM(self._responses)


def _install_fake_llm(monkeypatch, responses: list[ChatResponse]):
    monkeypatch.setattr(chat_runtime, "llm_factory", _ScriptedLLMFactory(responses))


async def _wait_for_run_done(app_client, run_id: str, timeout_s: float = 5.0) -> dict:
    """Poll GET /conversations/{id} indirectly isn't enough (we only have run
    id) — poll the SSE stream to completion and return the last `run.done`
    event's payload, since that's the only P0 way to observe run completion
    per the endpoint table.
    """
    async with app_client.stream("GET", f"/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        last_done = None
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            event = json.loads(line[len("data: ") :])
            if event["type"] == "run.done":
                last_done = event
                break
        return last_done


async def _create_deployed_agent(app_client, template="blank"):
    created = (await app_client.post("/agents", json={"name": "Chat Test", "template": template})).json()
    return created


# ---------------------------------------------------------------------------
# CD-4 / AC-CD-e
# ---------------------------------------------------------------------------
async def test_share_info_has_no_forbidden_keys(app_client):
    agent = await _create_deployed_agent(app_client)
    resp = await app_client.get(f"/share/{agent['slug']}")
    assert resp.status_code == 200
    body = resp.json()

    payload_str = json.dumps(body)
    for forbidden in ("system_prompt", "guidelines", "files"):
        assert forbidden not in payload_str, f"{forbidden!r} leaked into /share response: {body}"

    assert set(body.keys()) == {"name", "description", "deployed_version_number"}
    assert body["deployed_version_number"] == 1


# ---------------------------------------------------------------------------
# AC-CD-b
# ---------------------------------------------------------------------------
async def test_deploy_new_version_does_not_move_existing_conversation(app_client):
    agent = await _create_deployed_agent(app_client)

    conv_resp = await app_client.post(f"/share/{agent['slug']}/conversations")
    assert conv_resp.status_code == 201
    conversation_id = conv_resp.json()["conversation_id"]

    v2 = (await app_client.post(f"/agents/{agent['id']}/versions", json={"change_note": "v2"})).json()
    deploy_resp = await app_client.post(f"/agents/{agent['id']}/deploy", json={"version_id": v2["id"]})
    assert deploy_resp.status_code == 200

    share_after = await app_client.get(f"/share/{agent['slug']}")
    assert share_after.json()["deployed_version_number"] == 2

    conv_detail = await app_client.get(f"/conversations/{conversation_id}")
    assert conv_detail.status_code == 200
    assert conv_detail.json()["version_id"] != v2["id"]


# ---------------------------------------------------------------------------
# CD-5, CD-6 / AC-CD-c
# ---------------------------------------------------------------------------
async def test_post_message_returns_run_id_fast(app_client, monkeypatch):
    agent = await _create_deployed_agent(app_client)
    conversation_id = (await app_client.post(f"/share/{agent['slug']}/conversations")).json()["conversation_id"]

    _install_fake_llm(monkeypatch, [ChatResponse(content="hello back", tool_calls=None)])

    import time

    start = time.monotonic()
    resp = await app_client.post(f"/conversations/{conversation_id}/messages", json={"content": "hi"})
    elapsed_ms = (time.monotonic() - start) * 1000

    assert resp.status_code == 202
    assert "run_id" in resp.json()
    assert elapsed_ms < 300, f"POST took {elapsed_ms:.1f}ms, expected < 300ms (CD-5)"

    await _wait_for_run_done(app_client, resp.json()["run_id"])


async def test_second_message_while_running_returns_409(app_client, monkeypatch):
    agent = await _create_deployed_agent(app_client)
    conversation_id = (await app_client.post(f"/share/{agent['slug']}/conversations")).json()["conversation_id"]

    # An LLM response that never resolves quickly: use an asyncio.Event-gated
    # FakeLLM subclass so the run is still "in progress" when we post again.
    gate = asyncio.Event()

    class _SlowFakeLLM:
        async def chat(self, **kwargs):
            await gate.wait()
            return ChatResponse(content="done", tool_calls=None)

    monkeypatch.setattr(chat_runtime, "llm_factory", lambda: _SlowFakeLLM())

    first = await app_client.post(f"/conversations/{conversation_id}/messages", json={"content": "hi"})
    assert first.status_code == 202

    second = await app_client.post(f"/conversations/{conversation_id}/messages", json={"content": "hi again"})
    assert second.status_code == 409

    gate.set()
    await _wait_for_run_done(app_client, first.json()["run_id"])


async def test_post_message_to_unknown_conversation_404(app_client):
    resp = await app_client.post("/conversations/conv_doesnotexist/messages", json={"content": "hi"})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CD-7 / AC-CD-d
# ---------------------------------------------------------------------------
async def test_sse_replays_all_stored_events_after_run_finishes(app_client, monkeypatch):
    agent = await _create_deployed_agent(app_client)
    conversation_id = (await app_client.post(f"/share/{agent['slug']}/conversations")).json()["conversation_id"]

    _install_fake_llm(monkeypatch, [ChatResponse(content="the final answer", tool_calls=None)])

    run_id = (await app_client.post(f"/conversations/{conversation_id}/messages", json={"content": "hi"})).json()[
        "run_id"
    ]
    await _wait_for_run_done(app_client, run_id)

    # A NEW client connects after the run is fully done (late joiner / reload).
    events = []
    async with app_client.stream("GET", f"/runs/{run_id}/events") as resp:
        assert resp.status_code == 200
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            events.append(json.loads(line[len("data: ") :])["type"])

    assert events[0] == "run.started"
    assert events[-1] == "run.done"
    assert "message.final" in events


async def test_sse_stream_closes_after_run_done(app_client, monkeypatch):
    agent = await _create_deployed_agent(app_client)
    conversation_id = (await app_client.post(f"/share/{agent['slug']}/conversations")).json()["conversation_id"]
    _install_fake_llm(monkeypatch, [ChatResponse(content="ok", tool_calls=None)])

    run_id = (await app_client.post(f"/conversations/{conversation_id}/messages", json={"content": "hi"})).json()[
        "run_id"
    ]

    # Stream live (connect essentially immediately) and confirm the generator
    # terminates on its own right after run.done (no hang).
    events = []
    async with app_client.stream("GET", f"/runs/{run_id}/events") as resp:
        async for line in resp.aiter_lines():
            if not line.startswith("data: "):
                continue
            events.append(json.loads(line[len("data: ") :])["type"])
    assert events[-1] == "run.done"


async def test_sse_unknown_run_404(app_client):
    resp = await app_client.get("/runs/run_doesnotexist/events")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# CD-8
# ---------------------------------------------------------------------------
async def test_run_persists_assistant_message_for_next_turn_history(app_client, monkeypatch):
    agent = await _create_deployed_agent(app_client)
    conversation_id = (await app_client.post(f"/share/{agent['slug']}/conversations")).json()["conversation_id"]

    _install_fake_llm(monkeypatch, [ChatResponse(content="first answer", tool_calls=None)])
    run_id = (await app_client.post(f"/conversations/{conversation_id}/messages", json={"content": "q1"})).json()[
        "run_id"
    ]
    await _wait_for_run_done(app_client, run_id)

    detail = await app_client.get(f"/conversations/{conversation_id}")
    messages = detail.json()["messages"]
    roles = [m["role"] for m in messages]
    assert roles == ["user", "assistant"]
    assert messages[1]["content"] == "first answer"

    runs = detail.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["status"] == "succeeded"
    assert runs[0]["final_answer"] == "first answer"


async def test_get_conversation_unknown_404(app_client):
    resp = await app_client.get("/conversations/conv_doesnotexist")
    assert resp.status_code == 404

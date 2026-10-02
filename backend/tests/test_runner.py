"""T1.2 — Runner (specs/02-agent-runtime.md RT-1..RT-9).

AC-RT-a: full trace shape + correct final_answer/steps.
AC-RT-b: step_limit after exactly max_steps model calls.
Plus RT-1 system prompt, RT-5 retry-then-fail, RT-6 unknown tool / bad args,
RT-8 temperature, RT-9 tool filtering.
"""
import json
from dataclasses import dataclass, field
from typing import Optional

import pytest

from app.llm import ChatResponse, FakeLLM, ToolCall
from app.runner import run_turn
from app.sandbox import LocalSandbox


@dataclass
class FakeVersion:
    system_prompt: str = "You are a helpful assistant."
    guidelines: list = field(default_factory=list)
    tools: list = field(default_factory=lambda: ["bash", "read_file", "write_file", "edit_file", "list_files"])
    model: Optional[str] = None
    max_steps: int = 15
    tool_timeout_s: int = 30
    number: int = 1


def _tool_call_response(name: str, args: dict, call_id: str = "call_1") -> ChatResponse:
    return ChatResponse(content=None, tool_calls=[ToolCall(id=call_id, name=name, arguments=json.dumps(args))])


def _final_response(text: str) -> ChatResponse:
    return ChatResponse(content=text, tool_calls=None)


@pytest.fixture()
async def sandbox():
    sb = LocalSandbox()
    yield sb
    await sb.destroy()


class _AsyncEmit:
    """Wraps a plain list so events can be collected via the async `emit`
    callable the runner expects (Callable[[Event], Awaitable[None]])."""

    def __init__(self, events: list):
        self._events = events

    async def __call__(self, event):
        self._events.append(event)


async def _noop_emit(event):
    return None


# --------------------------------------------------------------------------
# AC-RT-a
# --------------------------------------------------------------------------
async def test_tool_loop_and_events(sandbox):
    llm = FakeLLM(
        [
            _tool_call_response("bash", {"command": "echo hi"}),
            _final_response("done"),
        ]
    )
    events: list = []

    result = await run_turn(
        version=FakeVersion(),
        history=[{"role": "user", "content": "say hi"}],
        sandbox=sandbox,
        emit=_AsyncEmit(events),
        source="chat",
        llm=llm,
    )

    assert result.final_answer == "done"
    assert result.steps == 2
    assert result.status == "succeeded"

    event_types = [e["type"] for e in events]
    assert event_types == ["run.started", "tool.call", "tool.result", "message.final", "run.done"]
    assert events[0]["type"] == "run.started"
    assert events[-1] == {"type": "run.done", "status": "succeeded", "steps": 2}
    assert result.trace == events


# --------------------------------------------------------------------------
# AC-RT-b
# --------------------------------------------------------------------------
async def test_step_limit_after_exactly_max_steps(sandbox):
    # Always returns a tool call -> never terminates on its own.
    def infinite_tool_calls():
        while True:
            yield _tool_call_response("bash", {"command": "true"})

    gen = infinite_tool_calls()

    class AlwaysToolCallLLM:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, tools=None, model=None, temperature=0, response_format=None):
            self.calls += 1
            return next(gen)

    llm = AlwaysToolCallLLM()
    events: list = []

    result = await run_turn(
        version=FakeVersion(max_steps=3),
        history=[{"role": "user", "content": "loop forever"}],
        sandbox=sandbox,
        emit=_AsyncEmit(events),
        source="chat",
        llm=llm,
    )

    assert result.status == "step_limit"
    assert llm.calls == 3
    assert result.steps == 3
    assert "run out of steps" in result.final_answer.lower() or "step" in result.final_answer.lower()
    assert events[-1] == {"type": "run.done", "status": "step_limit", "steps": 3}


# --------------------------------------------------------------------------
# RT-1 system message
# --------------------------------------------------------------------------
async def test_system_prompt_includes_guidelines_and_runtime_note(sandbox):
    version = FakeVersion(
        system_prompt="Be concise.",
        guidelines=[
            {"id": "g1", "section": "Tone", "text": "Always say please."},
            {"id": "g2", "section": "Accuracy", "text": "Double check numbers."},
        ],
        tools=["bash"],
    )
    llm = FakeLLM([_final_response("ok")])

    await run_turn(
        version=version,
        history=[{"role": "user", "content": "hi"}],
        sandbox=sandbox,
        emit=_noop_emit,
        source="chat",
        llm=llm,
    )

    system_message = llm.calls[0]["messages"][0]
    assert system_message["role"] == "system"
    content = system_message["content"]
    assert "Be concise." in content
    assert "## Learned guidelines" in content
    assert "Tone" in content
    assert "Always say please." in content
    assert "Accuracy" in content
    assert "/workspace" in content
    assert "bash" in content


# --------------------------------------------------------------------------
# RT-5 model error retry-once-then-fail
# --------------------------------------------------------------------------
async def test_model_error_retry_then_fail(sandbox, monkeypatch):
    call_count = {"n": 0}

    class AlwaysFailLLM:
        async def chat(self, messages, tools=None, model=None, temperature=0, response_format=None):
            call_count["n"] += 1
            raise RuntimeError("connection error")

    import app.runner as runner_module

    async def fast_sleep(_seconds):
        return None

    monkeypatch.setattr(runner_module.asyncio, "sleep", fast_sleep)

    llm = AlwaysFailLLM()
    events: list = []

    result = await run_turn(
        version=FakeVersion(),
        history=[{"role": "user", "content": "hi"}],
        sandbox=sandbox,
        emit=_AsyncEmit(events),
        source="chat",
        llm=llm,
    )

    assert call_count["n"] == 2  # one attempt + one retry
    assert result.status == "failed"
    assert result.error is not None
    assert any(e["type"] == "run.error" for e in events)
    assert events[-1]["type"] == "run.done"
    assert events[-1]["status"] == "failed"


async def test_model_error_then_success_on_retry(sandbox, monkeypatch):
    """RT-5 only requires retry-once-then-fail; confirm a successful retry
    also works (the call succeeds on the 2nd attempt).
    """
    import app.runner as runner_module

    async def fast_sleep(_seconds):
        return None

    monkeypatch.setattr(runner_module.asyncio, "sleep", fast_sleep)

    attempts = {"n": 0}

    class FailOnceLLM:
        async def chat(self, messages, tools=None, model=None, temperature=0, response_format=None):
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise RuntimeError("transient")
            return _final_response("recovered")

    result = await run_turn(
        version=FakeVersion(),
        history=[{"role": "user", "content": "hi"}],
        sandbox=sandbox,
        emit=_noop_emit,
        source="chat",
        llm=FailOnceLLM(),
    )

    assert result.status == "succeeded"
    assert result.final_answer == "recovered"
    assert attempts["n"] == 2


# --------------------------------------------------------------------------
# RT-6 unknown tool / bad args -> error string, not a raise
# --------------------------------------------------------------------------
async def test_unknown_tool_returns_error_string(sandbox):
    llm = FakeLLM(
        [
            _tool_call_response("not_a_real_tool", {}),
            _final_response("recovered"),
        ]
    )
    events: list = []

    result = await run_turn(
        version=FakeVersion(),
        history=[{"role": "user", "content": "hi"}],
        sandbox=sandbox,
        emit=_AsyncEmit(events),
        source="chat",
        llm=llm,
    )

    assert result.status == "succeeded"  # the runner did not raise/crash
    tool_result_events = [e for e in events if e["type"] == "tool.result"]
    assert len(tool_result_events) == 1
    assert "error" in tool_result_events[0]["output"].lower()


async def test_bad_tool_args_returns_error_string(sandbox):
    llm = FakeLLM(
        [
            _tool_call_response("bash", {}),  # missing required "command"
            _final_response("recovered"),
        ]
    )
    events: list = []

    result = await run_turn(
        version=FakeVersion(),
        history=[{"role": "user", "content": "hi"}],
        sandbox=sandbox,
        emit=_AsyncEmit(events),
        source="chat",
        llm=llm,
    )

    assert result.status == "succeeded"
    tool_result_events = [e for e in events if e["type"] == "tool.result"]
    assert "error" in tool_result_events[0]["output"].lower()


# --------------------------------------------------------------------------
# RT-8 temperature
# --------------------------------------------------------------------------
async def test_eval_source_uses_temperature_zero(sandbox):
    llm = FakeLLM([_final_response("ok")])
    await run_turn(
        version=FakeVersion(),
        history=[{"role": "user", "content": "hi"}],
        sandbox=sandbox,
        emit=_noop_emit,
        source="eval",
        llm=llm,
    )
    assert llm.calls[0]["temperature"] == 0


async def test_chat_source_uses_default_temperature(sandbox):
    llm = FakeLLM([_final_response("ok")])
    await run_turn(
        version=FakeVersion(),
        history=[{"role": "user", "content": "hi"}],
        sandbox=sandbox,
        emit=_noop_emit,
        source="chat",
        llm=llm,
    )
    assert llm.calls[0]["temperature"] == 0.2


# --------------------------------------------------------------------------
# RT-9 only expose tools listed in version.tools
# --------------------------------------------------------------------------
async def test_only_enabled_tools_exposed_to_model(sandbox):
    llm = FakeLLM([_final_response("ok")])
    await run_turn(
        version=FakeVersion(tools=["bash", "read_file"]),
        history=[{"role": "user", "content": "hi"}],
        sandbox=sandbox,
        emit=_noop_emit,
        source="chat",
        llm=llm,
    )
    tool_names_sent = {t["function"]["name"] for t in llm.calls[0]["tools"]}
    assert tool_names_sent == {"bash", "read_file"}


async def test_disabled_tool_call_rejected_even_if_model_calls_it(sandbox):
    """RT-9: even if the model somehow emits a call to a tool not in
    version.tools, the runner must not execute it.
    """
    llm = FakeLLM(
        [
            _tool_call_response("write_file", {"path": "x.txt", "content": "y"}),
            _final_response("done"),
        ]
    )
    events: list = []

    result = await run_turn(
        version=FakeVersion(tools=["bash"]),  # write_file not enabled
        history=[{"role": "user", "content": "hi"}],
        sandbox=sandbox,
        emit=_AsyncEmit(events),
        source="chat",
        llm=llm,
    )

    assert result.status == "succeeded"
    tool_result_events = [e for e in events if e["type"] == "tool.result"]
    assert "not enabled" in tool_result_events[0]["output"].lower() or "error" in tool_result_events[0]["output"].lower()
    assert not (sandbox.root / "x.txt").exists()

"""The ReAct runner (specs/02-agent-runtime.md "Requirements — Runner").

`run_turn` is the single entry point used by chat, API and eval call sites
(per the spec's "Purpose": "The same runner serves chat, API and evals").
It depends only on the `LLM` Protocol (app/llm.py) and the `Sandbox`
Protocol (app/sandbox.py) — no provider SDK imports here (MG-2).
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Optional

from app.llm import LLM, ChatResponse, ToolCall
from app.sandbox import Sandbox
from app.tools import ToolArgumentError, run_tool, schemas_for

Event = dict[str, Any]
EmitFn = Callable[[Event], Awaitable[None]]

RUNTIME_NOTE_TEMPLATE = (
    "You have access to the following tools: {tools}. "
    "Your working directory / sandbox root is /workspace; all file paths you "
    "use with tools are relative to /workspace (or may be given as "
    "/workspace/... absolute paths)."
)

STEP_LIMIT_MESSAGE = "The agent ran out of steps before finishing this task."

MODEL_RETRY_DELAY_S = 2
EVAL_TEMPERATURE = 0
DEFAULT_CHAT_TEMPERATURE = 0.2


@dataclass
class RunResult:
    final_answer: str
    status: Literal["succeeded", "failed", "step_limit"]
    steps: int
    trace: list[Event] = field(default_factory=list)
    new_messages: list[dict] = field(default_factory=list)
    error: Optional[str] = None


def _build_system_message(version: Any) -> str:
    """RT-1: system_prompt + rendered guidelines (grouped by section) + a
    fixed runtime note listing available tools and the workspace path.
    """
    parts = [version.system_prompt or ""]

    guidelines = list(getattr(version, "guidelines", []) or [])
    if guidelines:
        by_section: dict[str, list[str]] = {}
        for g in guidelines:
            section = g.get("section", "General")
            by_section.setdefault(section, []).append(g.get("text", ""))

        lines = ["## Learned guidelines"]
        for section in sorted(by_section):
            lines.append(f"### {section}")
            for text in by_section[section]:
                lines.append(f"- {text}")
        parts.append("\n".join(lines))

    tool_names = list(getattr(version, "tools", []) or [])
    parts.append(RUNTIME_NOTE_TEMPLATE.format(tools=", ".join(tool_names) if tool_names else "none"))

    return "\n\n".join(p for p in parts if p)


def _temperature_for(source: str) -> float:
    """RT-8: temperature=0 for eval runs, version default (0.2) otherwise."""
    return EVAL_TEMPERATURE if source == "eval" else DEFAULT_CHAT_TEMPERATURE


async def _emit(emit: EmitFn, trace: list[Event], event: Event) -> None:
    """RT-7: emit AND append to run.trace."""
    trace.append(event)
    await emit(event)


def _parse_tool_args(raw: str) -> dict:
    try:
        parsed = json.loads(raw) if raw else {}
    except (json.JSONDecodeError, TypeError) as e:
        raise ToolArgumentError(f"invalid JSON arguments: {e}")
    if not isinstance(parsed, dict):
        raise ToolArgumentError("tool arguments must be a JSON object")
    return parsed


async def run_turn(
    version: Any,
    history: list[dict],
    sandbox: Sandbox,
    emit: EmitFn,
    source: Literal["chat", "api", "eval"],
    llm: LLM,
) -> RunResult:
    trace: list[Event] = []
    new_messages: list[dict] = []

    run_id = f"r_{id(history):x}"
    await _emit(emit, trace, {"type": "run.started", "run_id": run_id, "version": getattr(version, "number", None)})

    system_message = _build_system_message(version)
    messages: list[dict] = [{"role": "system", "content": system_message}, *history]

    tool_names = list(getattr(version, "tools", []) or [])
    tool_schemas = schemas_for(tool_names) if tool_names else None

    model = getattr(version, "model", None)
    max_steps = getattr(version, "max_steps", 15)
    tool_timeout_s = getattr(version, "tool_timeout_s", 30)
    temperature = _temperature_for(source)

    steps = 0
    while True:
        if steps >= max_steps:
            final_answer = STEP_LIMIT_MESSAGE
            await _emit(emit, trace, {"type": "run.done", "status": "step_limit", "steps": steps})
            return RunResult(
                final_answer=final_answer,
                status="step_limit",
                steps=steps,
                trace=trace,
                new_messages=new_messages,
            )

        try:
            response = await _call_model_with_retry(
                llm,
                messages=messages,
                tools=tool_schemas,
                model=model,
                temperature=temperature,
            )
        except Exception as e:  # RT-5: retried once already; this is the final failure.
            error_message = str(e)
            await _emit(emit, trace, {"type": "run.error", "message": error_message})
            await _emit(emit, trace, {"type": "run.done", "status": "failed", "steps": steps})
            return RunResult(
                final_answer="",
                status="failed",
                steps=steps,
                trace=trace,
                new_messages=new_messages,
                error=error_message,
            )

        steps += 1

        if not response.tool_calls:
            final_answer = response.content or ""
            assistant_message = {"role": "assistant", "content": final_answer}
            messages.append(assistant_message)
            new_messages.append(assistant_message)
            await _emit(emit, trace, {"type": "message.final", "content": final_answer})
            await _emit(emit, trace, {"type": "run.done", "status": "succeeded", "steps": steps})
            return RunResult(
                final_answer=final_answer,
                status="succeeded",
                steps=steps,
                trace=trace,
                new_messages=new_messages,
            )

        # RT-2: execute tool calls sequentially, append results, call model again.
        assistant_message = {
            "role": "assistant",
            "content": response.content,
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": tc.arguments},
                }
                for tc in response.tool_calls
            ],
        }
        messages.append(assistant_message)
        new_messages.append(assistant_message)

        for tc in response.tool_calls:
            await _emit(
                emit,
                trace,
                {
                    "type": "tool.call",
                    "step": steps,
                    "name": tc.name,
                    "args": _safe_args_for_event(tc.arguments),
                },
            )

            start = time.monotonic()
            try:
                args = _parse_tool_args(tc.arguments)
                if tool_names and tc.name not in tool_names:
                    raise ToolArgumentError(f"tool '{tc.name}' is not enabled for this agent version")
                output = await run_tool(tc.name, args, sandbox, tool_timeout_s=tool_timeout_s)
                result_text = output.full_text
                model_text = output.model_text
                truncated = output.truncated
                exit_code = None
            except ToolArgumentError as e:
                # RT-6: unknown tool / bad args -> error string as tool result, not a raise.
                result_text = f"error: {e}"
                model_text = result_text
                truncated = False
                exit_code = None

            duration_ms = int((time.monotonic() - start) * 1000)

            tool_message = {
                "role": "tool",
                "tool_call_id": tc.id,
                "content": model_text,
            }
            messages.append(tool_message)
            new_messages.append(tool_message)

            await _emit(
                emit,
                trace,
                {
                    "type": "tool.result",
                    "step": steps,
                    "exit_code": exit_code,
                    "output": result_text,
                    "truncated": truncated,
                    "duration_ms": duration_ms,
                },
            )


def _safe_args_for_event(raw_arguments: str) -> dict:
    try:
        parsed = json.loads(raw_arguments) if raw_arguments else {}
        return parsed if isinstance(parsed, dict) else {"_raw": raw_arguments}
    except (json.JSONDecodeError, TypeError):
        return {"_raw": raw_arguments}


async def _call_model_with_retry(
    llm: LLM,
    *,
    messages: list[dict],
    tools: Optional[list[dict]],
    model: Optional[str],
    temperature: float,
) -> ChatResponse:
    """RT-5: retry once after 2s on a raising model call, then propagate."""
    try:
        return await llm.chat(messages=messages, tools=tools, model=model, temperature=temperature)
    except Exception:
        await asyncio.sleep(MODEL_RETRY_DELAY_S)
        return await llm.chat(messages=messages, tools=tools, model=model, temperature=temperature)



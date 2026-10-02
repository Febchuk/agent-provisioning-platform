# 02 — Agent Runtime (Runner, Tools, Sandbox, Model Gateway)

## Purpose

Execute one agent turn: given a version, conversation history and a sandbox, loop model → tool → model until a final answer, emitting events. **The same runner serves chat, API and evals** — what we test is what we ship.

## In scope
- ReAct loop with OpenAI-style function calling.
- Five tools; Docker sandbox with subprocess fallback; per-step events; limits.

## Out of scope
- Token streaming, parallel tool calls in one step, custom user-defined tools, network access from the sandbox, persistent sandboxes across conversations.

## Interfaces

```python
async def run_turn(
    version: AgentVersion,
    history: list[dict],          # OpenAI-format messages, ending with the new user message
    sandbox: Sandbox,
    emit: Callable[[Event], Awaitable[None]],
    source: Literal["chat", "api", "eval"],
) -> RunResult                    # final_answer, status, steps, trace, new_messages

class Sandbox(Protocol):
    id: str
    async def exec(self, cmd: str, timeout_s: int) -> ExecResult   # stdout, stderr, exit_code, truncated, duration_ms
    async def read(self, path: str) -> str
    async def write(self, path: str, content: str) -> None
    async def list(self, path: str = ".") -> list[str]
    async def destroy(self) -> None

class LLM(Protocol):               # model gateway
    async def chat(self, messages, tools=None, model=None, temperature=0, response_format=None) -> ChatResponse
```

Implementations: `DockerSandbox`, `LocalSandbox` (fallback), `OpenAICompatLLM`, `FakeLLM` (tests; returns scripted responses in order).

## Requirements — Runner

| ID | P | Requirement |
|---|---|---|
| RT-1 | P0 | THE SYSTEM SHALL build the system message as: `version.system_prompt` + rendered guidelines (`## Learned guidelines` section, grouped by `section`) + a fixed runtime note listing available tools and the workspace path `/workspace`. |
| RT-2 | P0 | WHEN the model returns tool calls, THE SYSTEM SHALL execute them sequentially in the sandbox, append results as `tool` messages, and call the model again. |
| RT-3 | P0 | WHEN the model returns a message with no tool calls, THE SYSTEM SHALL end the run with status `succeeded` and that message as `final_answer`. |
| RT-4 | P0 | IF the run reaches `version.max_steps` model calls, THEN THE SYSTEM SHALL stop, set status `step_limit`, and set `final_answer` to a fixed message saying the agent ran out of steps. |
| RT-5 | P0 | IF the model call raises (network, 4xx/5xx), THEN THE SYSTEM SHALL retry once after 2 s, then end the run with status `failed` and the error recorded. |
| RT-6 | P0 | IF a tool name is unknown or arguments fail validation, THEN THE SYSTEM SHALL return an error string as the tool result (not raise), so the model can recover. |
| RT-7 | P0 | THE SYSTEM SHALL emit an event and append it to `run.trace` for: `run.started`, `tool.call`, `tool.result`, `message.final`, `run.error`, `run.done`. |
| RT-8 | P0 | THE SYSTEM SHALL use `temperature = 0` for eval runs and the version default (0.2) for chat. |
| RT-9 | P0 | THE SYSTEM SHALL only expose tools listed in `version.tools`. |

## Requirements — Tools

| ID | P | Tool | Behavior |
|---|---|---|---|
| TL-1 | P0 | `bash(command)` | Runs `bash -lc` in `/workspace`; returns `exit_code`, stdout+stderr; timeout = `tool_timeout_s`. |
| TL-2 | P0 | `read_file(path)` | Returns file text; IF binary or > 200 KB, THEN returns an error string. |
| TL-3 | P0 | `write_file(path, content)` | Creates/overwrites; creates parent dirs. |
| TL-4 | P0 | `edit_file(path, old, new)` | Replaces exactly one occurrence; IF `old` occurs 0 or >1 times, THEN returns an error string stating the count. |
| TL-5 | P0 | `list_files(path=".")` | Recursive listing, max 500 entries. |
| TL-6 | P0 | All | IF a path resolves outside `/workspace`, THEN returns an error string (no file access). |
| TL-7 | P0 | All | Tool output passed to the model SHALL be truncated to 10,000 chars with a `[truncated N chars]` marker; the full output (up to 100 KB) is kept in the trace. |

## Requirements — Sandbox

| ID | P | Requirement |
|---|---|---|
| SB-1 | P0 | WHEN a conversation starts, THE SYSTEM SHALL create one container from image `agentplat-sandbox` (python:3.11-slim + pandas, matplotlib, numpy) with `/workspace` seeded from `version.files`. |
| SB-2 | P0 | Containers SHALL run with `network_mode=none`, `mem_limit=512m`, `nano_cpus=1e9`, non-root user. |
| SB-3 | P0 | WHEN an eval trial runs, THE SYSTEM SHALL use a fresh sandbox and destroy it after the check completes. |
| SB-4 | P1 | WHILE a conversation sandbox is idle > 30 min, THE SYSTEM SHALL destroy it; a later turn recreates it from the conversation's files snapshot (files lost is acceptable; document it). |
| SB-5 | P0 | IF Docker is unavailable at startup, THEN THE SYSTEM SHALL use `LocalSandbox` (temp dir + subprocess) and expose `sandbox_mode: "local-unsafe"` on `GET /health`. |

## Requirements — Model gateway

| ID | P | Requirement |
|---|---|---|
| MG-1 | P0 | THE SYSTEM SHALL read `MODEL_BASE_URL`, `MODEL_API_KEY`, `MODEL_NAME`, `JUDGE_MODEL_NAME`, `IMPROVER_MODEL_NAME` from env. |
| MG-2 | P0 | The runner SHALL depend only on the `LLM` protocol (no provider SDK imports outside `llm.py`). |
| MG-3 | P1 | WHEN `version.model` is set, THE SYSTEM SHALL use it instead of `MODEL_NAME`. |

## Events (SSE payloads)

```json
{"type":"run.started","run_id":"r_1","version":4}
{"type":"tool.call","step":2,"name":"bash","args":{"command":"python q.py"}}
{"type":"tool.result","step":2,"exit_code":0,"output":"…","truncated":false,"duration_ms":1840}
{"type":"message.final","content":"…"}
{"type":"run.error","message":"…"}
{"type":"run.done","status":"succeeded","steps":3}
```

## Acceptance criteria

- **AC-RT-a** Given FakeLLM scripted `[tool_call bash "echo hi", final "done"]`, when `run_turn` runs, then trace types are `run.started, tool.call, tool.result, message.final, run.done`, `final_answer == "done"`, `steps == 2`.
- **AC-RT-b** Given FakeLLM that always returns a tool call and `max_steps = 3`, then status is `step_limit` after exactly 3 model calls.
- **AC-RT-c** Given `read_file("../../etc/passwd")`, then the tool result is an error string and no file is read.
- **AC-RT-d** Given `edit_file` where `old` appears twice, then the result says "found 2 occurrences".
- **AC-RT-e** Given a `bash` command `sleep 60` and timeout 2 s, then the result reports a timeout within 3 s.
- **AC-RT-f** (integration, real Docker) Given `orders.csv` seeded, when the real model is asked "How many rows are in orders.csv?", then the final answer contains the true row count.
- **AC-RT-g** Given a container, when `bash` runs `curl https://example.com`, then it fails (no network).

## Open questions
- None blocking. If interviewers' key is Anthropic-native rather than OpenAI-compatible, add an adapter inside `llm.py` only (MG-2).

"""Chat orchestration for Phase 3 (specs/03-chat-and-deploy.md CD-4..CD-8).

This module is the glue between the HTTP layer (app/main.py), the DB-only
service layer (app/services.py), and Phase 2's runner/sandbox/tools
(app/runner.py, app/sandbox.py) — none of which are modified here.

Responsibilities, each tied to a spec requirement:
  - One sandbox per conversation, created on first use, kept in memory for
    the life of the process (specs/02-agent-runtime.md SB-1; SB-4's 30-min
    idle destroy is P1 and not built).
  - Track which conversations have a run in progress so a second POST
    returns 409 (CD-6).
  - Run a turn in the background (asyncio.create_task) so the HTTP handler
    can persist the user message and return `run_id` fast (CD-5).
  - Buffer each run's emitted events in memory (ordered) AND persist them to
    `Run.trace` as they happen, so `GET /runs/{id}/events` can replay stored
    events in order before switching to live events for the same run, and so
    a run that already finished can be fully replayed from the DB alone
    (CD-7, AC-CD-d).
  - On run completion, persist the new assistant/tool messages (OpenAI chat
    format) so the next turn's history replays exactly (CD-8).

Single-process, in-memory state only — no queue/worker infra, consistent
with specs/00-overview.md §4 ("Postgres, queues, horizontal scaling" is out
of scope) and the single-process SQLite constraint (specs/00 §6).
"""
from __future__ import annotations

import asyncio
from typing import Optional

from sqlmodel import Session, select

from app import db
from app.files import read_file_bytes
from app.ids import new_id
from app.llm import LLM, OpenAICompatLLM
from app.models import AgentVersion, Conversation, Message, Run
from app.models import _utcnow
from app.runner import run_turn
from app.sandbox import Sandbox
from app.sandbox_factory import create_sandbox

# --------------------------------------------------------------------------
# Per-conversation sandbox registry (SB-1: one container per conversation,
# created on first use; kept for the process lifetime, which is the simplest
# correct behavior for a single-process demo backend).
# --------------------------------------------------------------------------
_sandboxes: dict[str, Sandbox] = {}

# run_id -> ordered list of events already emitted for that run (mirrors
# Run.trace in the DB; kept in memory too so live SSE subscribers that
# attach mid-run can be given a consistent, cheap-to-read replay buffer
# without hitting the DB on every connect).
_run_events: dict[str, list[dict]] = {}

# run_id -> list of asyncio.Queue, one per live SSE subscriber, fed as new
# events are emitted. A `None` sentinel pushed to a queue means "run is done,
# stop streaming" (keeps SSE generators simple).
_run_subscribers: dict[str, list["asyncio.Queue"]] = {}

# conversation_id -> run_id currently in progress for that conversation
# (CD-6: a second POST while this is set returns 409).
_active_runs: dict[str, str] = {}

# Sandbox backend as reported by GET /health (set once at startup by
# main.py's lifespan via app.sandbox_factory.get_sandbox_backend(), read
# here so the chat runtime uses the same decision without re-probing).
_sandbox_mode = {"value": "local"}


def set_sandbox_mode(mode: str) -> None:
    _sandbox_mode["value"] = mode


async def _new_sandbox(workspace_ref: str) -> Sandbox:
    """v2 (SB-6): every call site in this phase always does a fresh
    `create_sandbox()` with a newly generated `workspace_ref` -- the real
    "stop idle, resume on next turn" lifecycle (SB-4) that would reuse an
    existing `conversation.workspace_ref` via `resume_sandbox()` is Phase
    4's job, logged in DECISIONS.md. This phase only needs the factory
    interface change to not break anything.
    """
    return await create_sandbox(workspace_ref, backend=_sandbox_mode["value"])


def _llm_factory() -> LLM:
    """A fresh real LLM per call; cheap (just wraps an AsyncOpenAI client)
    and keeps this module free of global mutable LLM state that tests would
    otherwise need to monkeypatch across runs.
    """
    return OpenAICompatLLM()


# Overridable by tests: a zero-arg callable returning an `LLM`. Production
# code never needs to touch this; tests swap it for a FakeLLM-backed factory.
llm_factory: "callable" = _llm_factory


async def get_or_create_sandbox(conversation: Conversation, version: AgentVersion) -> Sandbox:
    """SB-1: one sandbox per conversation, created on first use and seeded
    from `version.files` (the files belonging to the version the
    conversation started on, per v2 DM-2 -- still the pinned version in this
    phase; see `services.version_for_next_turn`).
    """
    existing = _sandboxes.get(conversation.id)
    if existing is not None:
        return existing

    sandbox = await _new_sandbox(new_id("ws"))
    for f in version.files or []:
        try:
            content = read_file_bytes(f["path_on_disk"])
        except (FileNotFoundError, KeyError, OSError):
            continue
        sandbox.seed_file(f["name"], content)

    _sandboxes[conversation.id] = sandbox
    return sandbox


def is_run_in_progress(conversation_id: str) -> bool:
    return conversation_id in _active_runs


class RunAlreadyInProgressError(Exception):
    """CD-6: a run is already in progress for this conversation."""


def start_turn(
    conversation_id: str,
    *,
    llm: Optional[LLM] = None,
) -> str:
    """Persist nothing itself — the caller (POST /conversations/{id}/messages)
    is responsible for persisting the user message BEFORE calling this
    (CD-5). This function allocates a `Run` row, marks the conversation busy,
    and schedules the background task. Returns the new run's id.

    Raises `RunAlreadyInProgressError` if a run is already active for this
    conversation (CD-6); callers translate that into a 409.
    """
    if conversation_id in _active_runs:
        raise RunAlreadyInProgressError(conversation_id)

    with Session(db.engine) as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise ValueError(f"conversation {conversation_id!r} not found")
        version = session.get(AgentVersion, conversation.started_on_version_id)
        assert version is not None

        run = Run(id=new_id("run"), conversation_id=conversation_id, version_id=version.id, source="chat")
        session.add(run)
        session.commit()
        session.refresh(run)
        run_id = run.id

    _active_runs[conversation_id] = run_id
    _run_events[run_id] = []
    _run_subscribers[run_id] = []

    asyncio.create_task(_execute_turn(conversation_id, run_id, llm or llm_factory()))
    return run_id


def _history_for_conversation(session: Session, conversation_id: str) -> list[dict]:
    """OpenAI chat format, per specs/01-data-model.md messages table note:
    "Stored in OpenAI chat format so history can be replayed exactly."
    """
    rows = session.exec(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.seq)
    ).all()
    history: list[dict] = []
    for m in rows:
        msg: dict = {"role": m.role, "content": m.content}
        if m.tool_calls:
            msg["tool_calls"] = m.tool_calls
        if m.tool_call_id:
            msg["tool_call_id"] = m.tool_call_id
        history.append(msg)
    return history


def _next_seq(session: Session, conversation_id: str) -> int:
    existing = session.exec(
        select(Message.seq).where(Message.conversation_id == conversation_id)
    ).all()
    return (max(existing) + 1) if existing else 0


def persist_user_message(session: Session, conversation_id: str, content: str) -> Message:
    """CD-5: persist the user message before starting the run."""
    seq = _next_seq(session, conversation_id)
    message = Message(id=new_id("msg"), conversation_id=conversation_id, seq=seq, role="user", content=content)
    session.add(message)
    session.commit()
    session.refresh(message)
    return message


async def _emit_factory(run_id: str):
    async def emit(event: dict) -> None:
        _run_events[run_id].append(event)
        for q in list(_run_subscribers.get(run_id, [])):
            await q.put(event)

    return emit


async def _execute_turn(conversation_id: str, run_id: str, llm: LLM) -> None:
    try:
        with Session(db.engine) as session:
            conversation = session.get(Conversation, conversation_id)
            version = session.get(AgentVersion, conversation.started_on_version_id)
            history = _history_for_conversation(session, conversation_id)

        sandbox = await get_or_create_sandbox(conversation, version)
        emit = await _emit_factory(run_id)

        result = await run_turn(
            version=version,
            history=history,
            sandbox=sandbox,
            emit=emit,
            source="chat",
            llm=llm,
        )

        with Session(db.engine) as session:
            run = session.get(Run, run_id)
            run.status = result.status
            run.final_answer = result.final_answer
            run.steps = result.steps
            run.trace = result.trace
            run.error = result.error
            run.finished_at = _utcnow()
            session.add(run)

            # CD-8: persist assistant/tool messages so the next turn's
            # history replays exactly.
            seq = _next_seq(session, conversation_id)
            for new_msg in result.new_messages:
                message = Message(
                    id=new_id("msg"),
                    conversation_id=conversation_id,
                    seq=seq,
                    role=new_msg["role"],
                    content=new_msg.get("content") or "",
                    tool_calls=new_msg.get("tool_calls"),
                    tool_call_id=new_msg.get("tool_call_id"),
                    run_id=run_id,
                )
                session.add(message)
                seq += 1

            session.commit()
    finally:
        _active_runs.pop(conversation_id, None)
        for q in list(_run_subscribers.get(run_id, [])):
            await q.put(None)  # sentinel: stop streaming


def get_run_events_snapshot(run_id: str) -> list[dict]:
    """In-memory events emitted so far for this run (may be empty if the run
    already finished and the process still holds the buffer, or if this
    process never saw the run live — callers should fall back to the DB's
    `Run.trace` for full replay, which is always authoritative).
    """
    return list(_run_events.get(run_id, []))


async def subscribe(run_id: str) -> "asyncio.Queue":
    q: asyncio.Queue = asyncio.Queue()
    _run_subscribers.setdefault(run_id, []).append(q)
    return q


def unsubscribe(run_id: str, q: "asyncio.Queue") -> None:
    subs = _run_subscribers.get(run_id)
    if subs and q in subs:
        subs.remove(q)


def run_is_active(run_id: str) -> bool:
    return run_id in _active_runs.values()


async def reset_state_for_tests() -> None:
    """Test-only helper: clear all in-memory state and destroy any sandboxes
    created so far. Module-level dicts here are process-global, so without
    this, state would leak between API-contract tests that each expect a
    fresh in-memory world.
    """
    for sandbox in list(_sandboxes.values()):
        await sandbox.destroy()
    _sandboxes.clear()
    _run_events.clear()
    _run_subscribers.clear()
    _active_runs.clear()

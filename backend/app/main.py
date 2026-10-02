"""FastAPI entrypoint (specs/03-chat-and-deploy.md endpoint table).

Phase 1/2 added `GET /health`. Phase 3 (T2.2, T2.3) adds the
agents/versions/deploy/templates/files API and the
conversations/chat/SSE API. P1 endpoints (`/conversations/{id}/files/{path}`,
`/agents/{id}/api-keys`, `/v1/agents/{slug}/messages`) are intentionally not
built in this phase.
"""
import json
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app import chat_runtime, db, services
from app.db import init_db
from app.files import store_file
from app.models import AgentVersion, Conversation, Run
from app.sandbox import docker_available
from app.templates import list_templates

# SB-5: detected once at startup (a Docker daemon ping), not re-checked per
# request. app/sandbox.py's docker_available() is the single source of truth
# for this check; main.py only reads the result.
_sandbox_mode = {"value": "local-unsafe"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    _sandbox_mode["value"] = "docker" if docker_available() else "local-unsafe"
    chat_runtime.set_sandbox_mode(_sandbox_mode["value"])
    yield


app = FastAPI(title="Agent Provisioning Platform", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    """GET /health — {ok, sandbox_mode} (specs/03 endpoint table; SB-5).

    sandbox_mode is "docker" when the Docker daemon was reachable at startup,
    else "local-unsafe" (LocalSandbox fallback).
    """
    return {"ok": True, "sandbox_mode": _sandbox_mode["value"]}


# ---------------------------------------------------------------------------
# Templates (CD-1)
# ---------------------------------------------------------------------------
@app.get("/templates")
async def get_templates() -> list[dict]:
    return [
        {
            "id": t["id"],
            "label": t["label"],
            "description": t["description"],
            "tools": t["tools"],
            "file_names": [f["name"] for f in t["files"]],
        }
        for t in list_templates()
    ]


# ---------------------------------------------------------------------------
# Agents (CD-1, CD-2, CD-3, CD-10)
# ---------------------------------------------------------------------------
class CreateAgentBody(BaseModel):
    name: str
    description: str = ""
    template: str = "blank"


def _version_out(v: AgentVersion) -> dict:
    return {
        "id": v.id,
        "agent_id": v.agent_id,
        "number": v.number,
        "parent_version_id": v.parent_version_id,
        "system_prompt": v.system_prompt,
        "guidelines": v.guidelines,
        "tools": v.tools,
        "model": v.model,
        "max_steps": v.max_steps,
        "tool_timeout_s": v.tool_timeout_s,
        "files": [{"name": f["name"], "size": f.get("size")} for f in (v.files or [])],
        "source": v.source,
        "change_note": v.change_note,
        "created_at": v.created_at,
    }


@app.post("/agents", status_code=201)
async def create_agent(body: CreateAgentBody) -> dict:
    try:
        with Session(db.engine) as session:
            agent = services.create_agent_from_template(
                session, name=body.name, description=body.description, template_id=body.template
            )
            return {
                "id": agent.id,
                "slug": agent.slug,
                "name": agent.name,
                "description": agent.description,
                "deployed_version_id": agent.deployed_version_id,
            }
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/agents")
async def list_agents() -> list[dict]:
    with Session(db.engine) as session:
        return services.list_agents_with_stats(session)


@app.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict:
    with Session(db.engine) as session:
        agent = services.get_agent_or_404(session, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")

        versions = session.exec(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id)
            .order_by(AgentVersion.number.desc())
        ).all()
        return {
            "id": agent.id,
            "slug": agent.slug,
            "name": agent.name,
            "description": agent.description,
            "deployed_version_id": agent.deployed_version_id,
            "created_at": agent.created_at,
            "versions": [_version_out(v) for v in versions],
        }


class CreateVersionBody(BaseModel):
    system_prompt: Optional[str] = None
    guidelines: Optional[list[dict]] = None
    tools: Optional[list[str]] = None
    model: Optional[str] = None
    max_steps: Optional[int] = None
    tool_timeout_s: Optional[int] = None
    change_note: str = ""


@app.post("/agents/{agent_id}/versions", status_code=201)
async def create_version(agent_id: str, body: CreateVersionBody) -> dict:
    with Session(db.engine) as session:
        try:
            version = services.create_version_from_fields(
                session,
                agent_id=agent_id,
                system_prompt=body.system_prompt,
                guidelines=body.guidelines,
                tools=body.tools,
                model=body.model,
                max_steps=body.max_steps,
                tool_timeout_s=body.tool_timeout_s,
                change_note=body.change_note,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return _version_out(version)


@app.post("/agents/{agent_id}/files")
async def upload_agent_file(agent_id: str, file: UploadFile) -> dict:
    """Multipart upload; stored on disk, added to the NEXT version created
    (per the endpoint table). We implement "added to next version" by
    stashing the uploaded file's on-disk record and merging it into the
    `files` list the next time `POST /agents/{id}/versions` is called
    without an explicit `files` override — simplest correct approach: we
    create the new version's file list as (latest version's files + this
    upload) immediately, so the very next version created (with no explicit
    file list) already includes it via the normal parent-copy mechanism
    (CD-2).
    """
    with Session(db.engine) as session:
        agent = services.get_agent_or_404(session, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")

        content = await file.read()
        stored = store_file(agent_id, file.filename, content)

        latest = session.exec(
            select(AgentVersion).where(AgentVersion.agent_id == agent_id).order_by(AgentVersion.number.desc())
        ).first()
        new_files = list(latest.files) if latest else []
        new_files.append(stored)

        version = services.create_version_from_fields(
            session,
            agent_id=agent_id,
            files=new_files,
            change_note=f"Uploaded file: {file.filename}",
        )
        return {"file": {"name": stored["name"], "size": stored["size"]}, "version": _version_out(version)}


class DeployBody(BaseModel):
    version_id: str


@app.post("/agents/{agent_id}/deploy")
async def deploy(agent_id: str, body: DeployBody) -> dict:
    with Session(db.engine) as session:
        agent = services.get_agent_or_404(session, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        try:
            updated = services.deploy_version(session, agent_id=agent_id, version_id=body.version_id)
        except ValueError as e:
            # CD-3: deploy called with a version belonging to another agent
            # (or a nonexistent version) -> 400.
            raise HTTPException(status_code=400, detail=str(e))
        return {"id": updated.id, "deployed_version_id": updated.deployed_version_id}


# ---------------------------------------------------------------------------
# Share (public, no auth) — CD-4
# ---------------------------------------------------------------------------
@app.get("/share/{slug}")
async def share_info(slug: str) -> dict:
    """CD-4 / AC-CD-e: ONLY name, description, deployed version number. No
    system_prompt, guidelines, or files anywhere in the payload.
    """
    with Session(db.engine) as session:
        agent = services.get_agent_by_slug(session, slug)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        deployed_number = None
        if agent.deployed_version_id:
            version = session.get(AgentVersion, agent.deployed_version_id)
            deployed_number = version.number if version else None
        return {"name": agent.name, "description": agent.description, "deployed_version_number": deployed_number}


@app.post("/share/{slug}/conversations", status_code=201)
async def start_share_conversation(slug: str) -> dict:
    with Session(db.engine) as session:
        agent = services.get_agent_by_slug(session, slug)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        try:
            conversation = services.create_conversation(session, agent_id=agent.id, channel="share")
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return {"conversation_id": conversation.id}


# ---------------------------------------------------------------------------
# Conversations / messages / runs (CD-5, CD-6, CD-7, CD-8)
# ---------------------------------------------------------------------------
class PostMessageBody(BaseModel):
    content: str


@app.post("/conversations/{conversation_id}/messages", status_code=202)
async def post_message(conversation_id: str, body: PostMessageBody) -> dict:
    with Session(db.engine) as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        # CD-6: reject before persisting anything if a run is already active.
        if chat_runtime.is_run_in_progress(conversation_id):
            raise HTTPException(status_code=409, detail="a run is already in progress for this conversation")

        # CD-5: persist the user message BEFORE starting the run.
        chat_runtime.persist_user_message(session, conversation_id, body.content)

    try:
        run_id = chat_runtime.start_turn(conversation_id)
    except chat_runtime.RunAlreadyInProgressError:
        raise HTTPException(status_code=409, detail="a run is already in progress for this conversation")

    return {"run_id": run_id}


@app.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict:
    with Session(db.engine) as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            raise HTTPException(status_code=404, detail="conversation not found")

        messages = services.get_conversation_messages(session, conversation_id)
        runs = services.get_conversation_runs(session, conversation_id)

        return {
            "id": conversation.id,
            "agent_id": conversation.agent_id,
            "version_id": conversation.version_id,
            "channel": conversation.channel,
            "created_at": conversation.created_at,
            "messages": [
                {
                    "id": m.id,
                    "seq": m.seq,
                    "role": m.role,
                    "content": m.content,
                    "tool_calls": m.tool_calls,
                    "tool_call_id": m.tool_call_id,
                    "run_id": m.run_id,
                }
                for m in messages
            ],
            "runs": [
                {
                    "id": r.id,
                    "status": r.status,
                    "steps": r.steps,
                    "final_answer": r.final_answer,
                    "error": r.error,
                    "started_at": r.started_at,
                    "finished_at": r.finished_at,
                }
                for r in runs
            ],
        }


# ---------------------------------------------------------------------------
# SSE run events (CD-7)
# ---------------------------------------------------------------------------
def _sse_format(event: dict) -> str:
    return f"data: {json.dumps(event, default=str)}\n\n"


@app.get("/runs/{run_id}/events")
async def run_events(run_id: str):
    with Session(db.engine) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        run_is_running = run.status == "running"
        stored_trace = list(run.trace or [])

    # CD-7: subscribe to live events BEFORE taking the replay snapshot (both
    # happen synchronously against chat_runtime's in-memory structures with
    # no `await` in between, so no event emitted by the background task can
    # be lost or duplicated between the two steps).
    queue = None
    if run_is_running and chat_runtime.run_is_active(run_id):
        queue = await chat_runtime.subscribe(run_id)
        # Re-check immediately after subscribing: if the run finished in the
        # tiny window between our status read above and this subscribe call,
        # the "run finished" sentinel may already have been sent to OTHER
        # subscribers before ours existed, and we'd wait forever. Re-reading
        # the DB status (updated inside the same transaction that pushes the
        # sentinel, before the sentinel is sent) tells us definitively.
        with Session(db.engine) as session:
            run = session.get(Run, run_id)
            run_is_running = run.status == "running"
        if run_is_running:
            replay = chat_runtime.get_run_events_snapshot(run_id)
        else:
            chat_runtime.unsubscribe(run_id, queue)
            queue = None
            with Session(db.engine) as session:
                run = session.get(Run, run_id)
                replay = list(run.trace or [])
    else:
        replay = stored_trace

    async def event_stream():
        for event in replay:
            yield _sse_format(event)

        if queue is None:
            return

        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse_format(event)
        finally:
            chat_runtime.unsubscribe(run_id, queue)

    return StreamingResponse(event_stream(), media_type="text/event-stream")

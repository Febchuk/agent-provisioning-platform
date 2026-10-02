"""FastAPI entrypoint (specs/03-chat-and-deploy.md endpoint table).

Phase 1/2 added `GET /health`. Phase 3 (T2.2, T2.3) adds the
agents/versions/deploy/templates/files API and the
conversations/chat/SSE API. P1 endpoints (`/conversations/{id}/files/{path}`,
`/agents/{id}/api-keys`, `/v1/agents/{slug}/messages`) are intentionally not
built in this phase.
"""
import json
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlmodel import Session, select

from app import chat_runtime, db, evals, improver, services
from app.db import init_db
from app.files import store_file
from app.llm import LLM, OpenAICompatLLM, improver_model_name
from app.models import AgentVersion, Conversation, EvalCase, Feedback, Proposal, Run
from app.sandbox import DockerSandbox, LocalSandbox, Sandbox, docker_available
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

# Phase 6 (frontend): the Next.js dev server runs on a different origin
# (localhost:3000) than the API (127.0.0.1:8000), so without CORS headers
# every browser fetch from the frontend is blocked before it reaches any
# endpoint. Single local owner, no auth (specs/00 scope) -- wide-open origins
# are acceptable here and match the rest of this phase's security posture.
# Logged in DECISIONS.md as a backend gap found during Phase 6.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
            "started_on_version_id": conversation.started_on_version_id,
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


# ---------------------------------------------------------------------------
# Feedback, draft-case, eval cases (specs/04-feedback-and-evals.md T3.1)
# ---------------------------------------------------------------------------
def _eval_sandbox_factory() -> Sandbox:
    """Fresh sandbox per eval trial (EV-5), using the same Docker-vs-local
    decision as chat (set once at startup from `docker_available()`).
    """
    if _sandbox_mode["value"] == "docker":
        return DockerSandbox()
    return LocalSandbox()


def _eval_llm_factory() -> LLM:
    return OpenAICompatLLM()


# Overridable by tests, same pattern as chat_runtime.llm_factory.
eval_sandbox_factory: "callable" = _eval_sandbox_factory
eval_llm_factory: "callable" = _eval_llm_factory


class FeedbackBody(BaseModel):
    rating: str
    correction: Optional[str] = None


@app.post("/runs/{run_id}/feedback", status_code=201)
async def post_run_feedback(run_id: str, body: FeedbackBody) -> dict:
    """EV-1: public endpoint (no auth; used by the share page)."""
    with Session(db.engine) as session:
        run = session.get(Run, run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="run not found")
        if run.conversation_id is None:
            raise HTTPException(status_code=400, detail="run has no conversation to attach feedback to")
        if body.rating not in ("up", "down"):
            raise HTTPException(status_code=400, detail="rating must be 'up' or 'down'")

        feedback = evals.create_feedback(
            session, run_id=run_id, conversation_id=run.conversation_id, rating=body.rating, correction=body.correction
        )
        return {
            "id": feedback.id,
            "run_id": feedback.run_id,
            "conversation_id": feedback.conversation_id,
            "rating": feedback.rating,
            "correction": feedback.correction,
            "status": feedback.status,
            "created_at": feedback.created_at,
        }


@app.get("/agents/{agent_id}/feedback")
async def get_agent_feedback(agent_id: str, status: Optional[str] = None) -> list[dict]:
    with Session(db.engine) as session:
        items = evals.list_feedback(session, agent_id, status=status)
        return [
            {
                "id": f.id,
                "run_id": f.run_id,
                "conversation_id": f.conversation_id,
                "rating": f.rating,
                "correction": f.correction,
                "status": f.status,
                "created_at": f.created_at,
            }
            for f in items
        ]


@app.post("/feedback/{feedback_id}/draft-case")
async def post_draft_case(feedback_id: str) -> dict:
    """EV-2/EV-3/EV-4: drafts {name, axis, check_type, rubric} and returns it
    WITHOUT persisting an active eval case.
    """
    with Session(db.engine) as session:
        feedback = session.get(Feedback, feedback_id)
        if feedback is None:
            raise HTTPException(status_code=404, detail="feedback not found")
        try:
            draft = await evals.draft_case_from_feedback(session, feedback_id, eval_llm_factory())
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return {
            "name": draft.name,
            "axis": draft.axis,
            "check_type": draft.check_type,
            "check_spec": {"rubric": draft.rubric},
            "history": draft.history,
            "from_feedback_id": draft.from_feedback_id,
            "status": "draft",
        }


@app.post("/feedback/{feedback_id}/dismiss")
async def post_dismiss_feedback(feedback_id: str) -> dict:
    with Session(db.engine) as session:
        try:
            feedback = evals.dismiss_feedback(session, feedback_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return {"id": feedback.id, "status": feedback.status}


class CreateCaseBody(BaseModel):
    name: str
    check_type: str
    check_spec: dict
    axis: str = "accuracy"
    history: Optional[list[dict]] = None
    pinned: bool = False
    split: str = "improve"
    origin: str = "owner"
    issue_id: Optional[str] = None
    from_feedback_id: Optional[str] = None


def _case_out(case: EvalCase) -> dict:
    return {
        "id": case.id,
        "agent_id": case.agent_id,
        "name": case.name,
        "axis": case.axis,
        "history": case.history,
        "check_type": case.check_type,
        "check_spec": case.check_spec,
        "pinned": case.pinned,
        "split": case.split,
        "origin": case.origin,
        "issue_id": case.issue_id,
        "from_feedback_id": case.from_feedback_id,
        "status": case.status,
    }


@app.post("/agents/{agent_id}/cases", status_code=201)
async def post_create_case(agent_id: str, body: CreateCaseBody) -> dict:
    """Create/confirm a case (manual, or confirming a draft) -> status=active
    (EV-4: this is the only way a case becomes active).
    """
    with Session(db.engine) as session:
        agent = services.get_agent_or_404(session, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")

        case = services.create_eval_case(
            session,
            agent_id=agent_id,
            name=body.name,
            check_type=body.check_type,
            check_spec=body.check_spec,
            axis=body.axis,
            history=body.history,
            pinned=body.pinned,
            split=body.split,
            origin=body.origin,
            issue_id=body.issue_id,
            from_feedback_id=body.from_feedback_id,
            status="active",
        )

        if body.from_feedback_id:
            feedback = session.get(Feedback, body.from_feedback_id)
            if feedback is not None:
                feedback.status = "converted"
                session.add(feedback)
                session.commit()

        return _case_out(case)


class PatchCaseBody(BaseModel):
    pinned: Optional[bool] = None
    axis: Optional[str] = None
    name: Optional[str] = None
    status: Optional[str] = None


@app.patch("/cases/{case_id}")
async def patch_case(case_id: str, body: PatchCaseBody) -> dict:
    with Session(db.engine) as session:
        try:
            case = services.patch_eval_case(
                session, case_id, pinned=body.pinned, axis=body.axis, name=body.name, status=body.status
            )
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return _case_out(case)


@app.get("/agents/{agent_id}/cases")
async def get_agent_cases(agent_id: str) -> list[dict]:
    with Session(db.engine) as session:
        return services.list_cases_with_latest_results(session, agent_id)


# ---------------------------------------------------------------------------
# Eval executor + policy (specs/04-feedback-and-evals.md T3.3, T3.4)
# ---------------------------------------------------------------------------
class CreateEvalRunBody(BaseModel):
    version_id: str


@app.post("/agents/{agent_id}/eval-runs", status_code=201)
async def post_create_eval_run(agent_id: str, body: CreateEvalRunBody) -> dict:
    with Session(db.engine) as session:
        agent = services.get_agent_or_404(session, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        try:
            judge_model = os.environ.get("JUDGE_MODEL_NAME")
            eval_run = await evals.run_eval_run(
                session,
                agent_id=agent_id,
                version_id=body.version_id,
                sandbox_factory=eval_sandbox_factory,
                llm_factory=eval_llm_factory,
                judge_model=judge_model,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        return evals.eval_run_summary(session, eval_run.id)


@app.get("/eval-runs/{eval_run_id}")
async def get_eval_run(eval_run_id: str) -> dict:
    with Session(db.engine) as session:
        try:
            return evals.eval_run_summary(session, eval_run_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))


@app.get("/agents/{agent_id}/policy")
async def get_agent_policy(agent_id: str) -> dict:
    with Session(db.engine) as session:
        agent = services.get_agent_or_404(session, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        policy = evals.get_or_create_policy(session, agent_id)
        return evals.policy_to_dict(policy)


class PutPolicyBody(BaseModel):
    min_target_gain_pct: Optional[float] = None
    max_regressions: Optional[dict] = None
    trials_per_case: Optional[int] = None
    pass_threshold: Optional[int] = None


@app.put("/agents/{agent_id}/policy")
async def put_agent_policy(agent_id: str, body: PutPolicyBody) -> dict:
    with Session(db.engine) as session:
        agent = services.get_agent_or_404(session, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        policy = evals.update_policy(
            session,
            agent_id,
            min_target_gain_pct=body.min_target_gain_pct,
            max_regressions=body.max_regressions,
            trials_per_case=body.trials_per_case,
            pass_threshold=body.pass_threshold,
        )
        return evals.policy_to_dict(policy)


# ---------------------------------------------------------------------------
# Improver / proposals (specs/05-improver.md T4.4)
# ---------------------------------------------------------------------------
def _improver_llm_factory() -> LLM:
    return OpenAICompatLLM()


# Overridable by tests, same pattern as eval_sandbox_factory/eval_llm_factory.
improver_llm_factory: "callable" = _improver_llm_factory


@app.post("/agents/{agent_id}/proposals", status_code=201)
async def post_create_proposal(agent_id: str) -> dict:
    """IM-1: start the improver pipeline against the agent's DEPLOYED version.
    Runs the pipeline inline (awaited) rather than backgrounding it -- a
    proposal run is a handful of eval trials plus one LLM call, not an
    open-ended chat conversation, so there's no SSE/streaming requirement for
    it (specs/05's endpoint table has no "subscribe to proposal progress"
    endpoint, unlike chat's CD-7).
    """
    with Session(db.engine) as session:
        agent = services.get_agent_or_404(session, agent_id)
        if agent is None:
            raise HTTPException(status_code=404, detail="agent not found")
        if agent.deployed_version_id is None:
            raise HTTPException(status_code=400, detail="agent has no deployed version")

        proposal = improver.create_proposal(session, agent_id=agent_id, base_version_id=agent.deployed_version_id)
        proposal_id = proposal.id

    judge_model = os.environ.get("JUDGE_MODEL_NAME")
    with Session(db.engine) as session:
        await improver.run_proposal_pipeline(
            session,
            agent_id=agent_id,
            proposal_id=proposal_id,
            sandbox_factory=eval_sandbox_factory,
            llm_factory=eval_llm_factory,
            improver_llm_factory=improver_llm_factory,
            judge_model=judge_model,
            improver_model=improver_model_name(),
        )

    return {"proposal_id": proposal_id}


@app.get("/proposals/{proposal_id}")
async def get_proposal(proposal_id: str) -> dict:
    with Session(db.engine) as session:
        proposal = session.get(Proposal, proposal_id)
        if proposal is None:
            raise HTTPException(status_code=404, detail="proposal not found")
        return improver.proposal_to_dict(session, proposal)


class AcceptProposalBody(BaseModel):
    deploy: bool = False
    note: Optional[str] = None


@app.post("/proposals/{proposal_id}/accept")
async def post_accept_proposal(proposal_id: str, body: AcceptProposalBody) -> dict:
    with Session(db.engine) as session:
        try:
            proposal = improver.accept_proposal(session, proposal_id=proposal_id, deploy=body.deploy, note=body.note)
        except improver.ProposalAcceptError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return improver.proposal_to_dict(session, proposal)


@app.post("/proposals/{proposal_id}/reject")
async def post_reject_proposal(proposal_id: str) -> dict:
    with Session(db.engine) as session:
        try:
            proposal = improver.reject_proposal(session, proposal_id=proposal_id)
        except ValueError as e:
            raise HTTPException(status_code=404, detail=str(e))
        return improver.proposal_to_dict(session, proposal)

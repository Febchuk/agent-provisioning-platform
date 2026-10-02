"""Service-layer functions.

Phase 1 (T0/`01-data-model.md`) added the core CRUD + invariant-enforcing
functions: `create_agent`, `create_version`, `update_version` (always
raises, DM-1), `deploy_version`, `create_conversation`,
`version_for_next_turn`, `create_eval_case`.

Phase 3 (T2.2/T2.3, `03-chat-and-deploy.md`) extends this module with the
agent/version/deploy API's supporting logic: slug generation (CD-10),
creating an agent from a template (CD-1), listing agents with their stats,
and creating a version "from fields" for a manual edit (CD-2, reusing
`create_version`). Conversation/message/run orchestration that needs
background tasks and in-memory state lives in `app/chat_runtime.py` instead,
to keep this module pure DB logic.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlmodel import Session, func, select

from app.ids import new_id
from app.models import Agent, AgentVersion, Conversation, EvalCase, EvalResult, EvalRun, Feedback, Message, Run


class ImmutableVersionError(Exception):
    """Raised by any attempt to modify an agent_versions row after insert (DM-1)."""


class DuplicateGuidelineIdError(Exception):
    """Raised when a version's guidelines contain duplicate ids (DM-3)."""


class HiddenCaseMissingParentError(Exception):
    """Raised when a hidden eval case has no parent_case_id (DM-4)."""


def create_agent(session: Session, *, name: str, slug: str, description: str = "") -> Agent:
    agent = Agent(id=new_id("ag"), slug=slug, name=name, description=description)
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return agent


def create_version(
    session: Session,
    *,
    agent_id: str,
    system_prompt: str = "",
    guidelines: Optional[list[dict]] = None,
    tools: Optional[list[str]] = None,
    model: Optional[str] = None,
    max_steps: int = 15,
    tool_timeout_s: int = 30,
    files: Optional[list[dict]] = None,
    source: str = "created",
    change_note: str = "",
    parent_version_id: Optional[str] = None,
) -> AgentVersion:
    """Create a new immutable version for an agent.

    DM-2: WHEN a version is created, fields not explicitly given are copied
    from the parent version (per CD-2 in specs/03, reused here since it's the
    same mechanism that keeps guideline ids stable per DM-3).
    DM-5: number = max(existing numbers for this agent) + 1, so numbers are
    strictly increasing per agent. A DB unique constraint on
    (agent_id, number) backs this up defensively.
    DM-3: guidelines[].id values must be unique within the version. When no
    explicit `guidelines` is given and a parent exists, guidelines are copied
    verbatim from the parent, so ids stay stable across versions for
    unchanged rules.
    """
    parent: Optional[AgentVersion] = None
    if parent_version_id is not None:
        parent = session.get(AgentVersion, parent_version_id)
        if parent is None:
            raise ValueError(f"parent_version_id {parent_version_id!r} not found")

    if guidelines is None:
        guidelines = list(parent.guidelines) if parent else []
    if tools is None:
        tools = list(parent.tools) if parent else []
    if files is None:
        files = list(parent.files) if parent else []
    if model is None and parent is not None:
        model = parent.model
    if not system_prompt and parent is not None:
        system_prompt = parent.system_prompt

    _validate_guidelines_unique(guidelines)

    existing_numbers = session.exec(
        select(AgentVersion.number).where(AgentVersion.agent_id == agent_id)
    ).all()
    next_number = (max(existing_numbers) + 1) if existing_numbers else 1

    version = AgentVersion(
        id=new_id("v"),
        agent_id=agent_id,
        number=next_number,
        parent_version_id=parent_version_id,
        system_prompt=system_prompt,
        guidelines=guidelines,
        tools=tools,
        model=model,
        max_steps=max_steps,
        tool_timeout_s=tool_timeout_s,
        files=files,
        source=source,
        change_note=change_note,
    )
    session.add(version)
    session.commit()
    session.refresh(version)
    return version


def _validate_guidelines_unique(guidelines: list[dict]) -> None:
    ids = [g["id"] for g in guidelines if "id" in g]
    if len(ids) != len(set(ids)):
        dupes = {i for i in ids if ids.count(i) > 1}
        raise DuplicateGuidelineIdError(f"duplicate guideline id(s): {sorted(dupes)}")


def update_version(session: Session, version_id: str, **fields) -> None:
    """DM-1: agent_versions rows are never updated after insert.

    There is no UPDATE path for agent_versions anywhere in the service layer.
    This function exists only so the invariant is explicit and testable —
    calling it always raises, rather than silently not existing.
    """
    raise ImmutableVersionError(
        "agent_versions rows are immutable after insert; create a new version instead"
    )


def deploy_version(session: Session, *, agent_id: str, version_id: str) -> Agent:
    """Move the deploy pointer on `agents` (the agent row itself is mutable;
    only agent_versions rows are immutable per DM-1).
    """
    version = session.get(AgentVersion, version_id)
    if version is None or version.agent_id != agent_id:
        raise ValueError("version does not belong to this agent")

    agent = session.get(Agent, agent_id)
    if agent is None:
        raise ValueError(f"agent {agent_id!r} not found")

    agent.deployed_version_id = version_id
    session.add(agent)
    session.commit()
    session.refresh(agent)
    return agent


def create_conversation(session: Session, *, agent_id: str, channel: str = "share") -> Conversation:
    """DM-2: pin the conversation to the agent's CURRENT deployed version at
    creation time. A later deploy() call changes `agents.deployed_version_id`
    but never touches this conversation's `version_id`.
    """
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise ValueError(f"agent {agent_id!r} not found")
    if agent.deployed_version_id is None:
        raise ValueError("agent has no deployed version yet")

    conversation = Conversation(
        id=new_id("conv"),
        agent_id=agent_id,
        version_id=agent.deployed_version_id,
        channel=channel,
    )
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return conversation


def version_for_next_turn(session: Session, *, conversation_id: str) -> AgentVersion:
    """What the runner would load for the next turn in this conversation:
    always the conversation's pinned version, never the agent's current
    deployed version (DM-2).
    """
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise ValueError(f"conversation {conversation_id!r} not found")
    version = session.get(AgentVersion, conversation.version_id)
    assert version is not None
    return version


def create_eval_case(
    session: Session,
    *,
    agent_id: str,
    name: str,
    check_type: str,
    check_spec: dict,
    axis: str = "accuracy",
    history: Optional[list[dict]] = None,
    pinned: bool = False,
    hidden: bool = False,
    parent_case_id: Optional[str] = None,
    from_feedback_id: Optional[str] = None,
    status: str = "draft",
) -> EvalCase:
    """DM-4: a case with hidden=True must always have parent_case_id."""
    if hidden and not parent_case_id:
        raise HiddenCaseMissingParentError("hidden eval cases must have a parent_case_id")

    case = EvalCase(
        id=new_id("case"),
        agent_id=agent_id,
        name=name,
        axis=axis,
        history=history or [],
        check_type=check_type,
        check_spec=check_spec,
        pinned=pinned,
        hidden=hidden,
        parent_case_id=parent_case_id,
        from_feedback_id=from_feedback_id,
        status=status,
    )
    session.add(case)
    session.commit()
    session.refresh(case)
    return case


# ---------------------------------------------------------------------------
# Phase 3 (specs/03-chat-and-deploy.md): slugs, templates, agent listing,
# version-from-fields, file uploads.
# ---------------------------------------------------------------------------

_SLUG_SANITIZE_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """CD-10: derive a URL-safe slug from a name (lowercase, hyphens,
    alphanumerics only). Does not guarantee uniqueness by itself — see
    `unique_slug`.
    """
    base = _SLUG_SANITIZE_RE.sub("-", name.strip().lower()).strip("-")
    return base or "agent"


def unique_slug(session: Session, name: str) -> str:
    """CD-10: a slug derived from `name`, unique among existing agents.
    Collisions are deduped by appending an incrementing counter
    (`my-agent`, `my-agent-2`, `my-agent-3`, ...).
    """
    base = slugify(name)
    candidate = base
    counter = 1
    while session.exec(select(Agent).where(Agent.slug == candidate)).first() is not None:
        counter += 1
        candidate = f"{base}-{counter}"
    return candidate


def create_agent_from_template(
    session: Session,
    *,
    name: str,
    description: str,
    template_id: str,
) -> Agent:
    """CD-1: create an agent, create v1 from the named template (prompt,
    tools, files), and deploy v1 automatically.
    """
    from app.files import store_template_file
    from app.templates import get_template

    template = get_template(template_id)

    slug = unique_slug(session, name)
    agent = create_agent(session, name=name, slug=slug, description=description)

    files = [store_template_file(agent.id, f["name"], f["content"]) for f in template["files"]]

    version = create_version(
        session,
        agent_id=agent.id,
        system_prompt=template["system_prompt"],
        tools=list(template["tools"]),
        files=files,
        source="created",
        change_note=f"Created from template '{template_id}'",
    )
    deploy_version(session, agent_id=agent.id, version_id=version.id)
    session.refresh(agent)
    return agent


def create_version_from_fields(
    session: Session,
    *,
    agent_id: str,
    system_prompt: Optional[str] = None,
    guidelines: Optional[list[dict]] = None,
    tools: Optional[list[str]] = None,
    model: Optional[str] = None,
    max_steps: Optional[int] = None,
    tool_timeout_s: Optional[int] = None,
    files: Optional[list[dict]] = None,
    change_note: str = "",
) -> AgentVersion:
    """CD-2: create a new version from explicitly given fields (a manual
    edit via `POST /agents/{id}/versions`), `source = "manual"`. Unspecified
    fields are copied from the agent's current latest version (the natural
    parent for a manual edit), per `create_version`'s DM-2/CD-2 copy
    behavior. `number = max + 1` is handled by `create_version`.
    """
    agent = session.get(Agent, agent_id)
    if agent is None:
        raise ValueError(f"agent {agent_id!r} not found")

    latest = session.exec(
        select(AgentVersion).where(AgentVersion.agent_id == agent_id).order_by(AgentVersion.number.desc())
    ).first()
    parent_version_id = latest.id if latest else None

    kwargs: dict = {"source": "manual", "change_note": change_note, "parent_version_id": parent_version_id}
    if system_prompt is not None:
        kwargs["system_prompt"] = system_prompt
    if guidelines is not None:
        kwargs["guidelines"] = guidelines
    if tools is not None:
        kwargs["tools"] = tools
    if model is not None:
        kwargs["model"] = model
    if max_steps is not None:
        kwargs["max_steps"] = max_steps
    if tool_timeout_s is not None:
        kwargs["tool_timeout_s"] = tool_timeout_s
    if files is not None:
        kwargs["files"] = files

    return create_version(session, agent_id=agent_id, **kwargs)


def patch_eval_case(
    session: Session,
    case_id: str,
    *,
    pinned: Optional[bool] = None,
    axis: Optional[str] = None,
    name: Optional[str] = None,
    status: Optional[str] = None,
) -> EvalCase:
    """`PATCH /cases/{id}`: pinned, axis, name, status. Unlike
    `agent_versions`, `eval_cases` rows ARE mutable (no DM-1-style invariant
    applies to them) -- the spec's own endpoint table calls this out as a
    normal PATCH.
    """
    case = session.get(EvalCase, case_id)
    if case is None:
        raise ValueError(f"eval case {case_id!r} not found")

    if pinned is not None:
        case.pinned = pinned
    if axis is not None:
        case.axis = axis
    if name is not None:
        case.name = name
    if status is not None:
        case.status = status

    session.add(case)
    session.commit()
    session.refresh(case)
    return case


def list_cases_with_latest_results(session: Session, agent_id: str) -> list[dict]:
    """`GET /agents/{id}/cases`: visible (non-hidden) cases + latest results
    on the agent's currently deployed version. "Latest results" = the most
    recent eval_run for the deployed version that has a result for this
    case, if any.
    """
    agent = session.get(Agent, agent_id)
    deployed_version_id = agent.deployed_version_id if agent else None

    cases = session.exec(
        select(EvalCase).where(EvalCase.agent_id == agent_id, EvalCase.hidden == False)  # noqa: E712
    ).all()

    latest_eval_run = None
    if deployed_version_id:
        latest_eval_run = session.exec(
            select(EvalRun)
            .where(EvalRun.agent_id == agent_id, EvalRun.version_id == deployed_version_id, EvalRun.status == "completed")
            .order_by(EvalRun.finished_at.desc())
        ).first()

    results_by_case: dict[str, list[EvalResult]] = {}
    if latest_eval_run is not None:
        rows = session.exec(select(EvalResult).where(EvalResult.eval_run_id == latest_eval_run.id)).all()
        for r in rows:
            results_by_case.setdefault(r.case_id, []).append(r)

    out = []
    for case in cases:
        case_results = results_by_case.get(case.id, [])
        passing = sum(1 for r in case_results if r.passed)
        out.append(
            {
                "id": case.id,
                "name": case.name,
                "axis": case.axis,
                "check_type": case.check_type,
                "check_spec": case.check_spec,
                "pinned": case.pinned,
                "status": case.status,
                "from_feedback_id": case.from_feedback_id,
                "latest_result": (
                    {
                        "eval_run_id": latest_eval_run.id,
                        "trials": len(case_results),
                        "passing": passing,
                    }
                    if case_results
                    else None
                ),
            }
        )
    return out


def get_agent_or_404(session: Session, agent_id: str) -> Optional[Agent]:
    return session.get(Agent, agent_id)


def get_agent_by_slug(session: Session, slug: str) -> Optional[Agent]:
    return session.exec(select(Agent).where(Agent.slug == slug)).first()


def list_agents_with_stats(session: Session) -> list[dict]:
    """`GET /agents`: list with deployed version #, 7-day chat count, new
    feedback count, latest eval score.

    Chat count / feedback count / eval score are wired to the real tables
    (`messages`/`runs`, `feedback`) where those exist already, and default to
    0/null where the owning subsystem (evals, Phase 4) doesn't exist yet —
    the field shape is correct and never crashes, per this phase's brief.
    """
    agents = session.exec(select(Agent).order_by(Agent.created_at.desc())).all()
    cutoff = datetime.now(timezone.utc) - timedelta(days=7)
    result = []
    for agent in agents:
        deployed_number = None
        if agent.deployed_version_id:
            deployed_version = session.get(AgentVersion, agent.deployed_version_id)
            deployed_number = deployed_version.number if deployed_version else None

        chat_count_7d = session.exec(
            select(func.count(Run.id)).where(
                Run.conversation_id.in_(
                    select(Conversation.id).where(Conversation.agent_id == agent.id)
                ),
                Run.source == "chat",
                Run.started_at >= cutoff,
            )
        ).one()

        new_feedback_count = session.exec(
            select(func.count(Feedback.id)).where(
                Feedback.conversation_id.in_(
                    select(Conversation.id).where(Conversation.agent_id == agent.id)
                ),
                Feedback.status == "new",
            )
        ).one()

        result.append(
            {
                "id": agent.id,
                "slug": agent.slug,
                "name": agent.name,
                "description": agent.description,
                "deployed_version_number": deployed_number,
                "chat_count_7d": chat_count_7d,
                "new_feedback_count": new_feedback_count,
                "latest_eval_score": None,  # eval subsystem lands in Phase 4 (M3)
                "created_at": agent.created_at,
            }
        )
    return result


def get_conversation_messages(session: Session, conversation_id: str) -> list[Message]:
    return session.exec(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.seq)
    ).all()


def get_conversation_runs(session: Session, conversation_id: str) -> list[Run]:
    return session.exec(
        select(Run).where(Run.conversation_id == conversation_id).order_by(Run.started_at)
    ).all()

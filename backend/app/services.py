"""Minimal service-layer plumbing to prove the data-model invariants
(specs/01-data-model.md, DM-1..DM-5).

This is intentionally NOT the full CRUD/API surface from
specs/03-chat-and-deploy.md — that's Phase 3 (T2.x). Only what's needed to
create an agent, create versions, deploy, and create conversations/eval
cases while enforcing the invariants below.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.ids import new_id
from app.models import Agent, AgentVersion, Conversation, EvalCase


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

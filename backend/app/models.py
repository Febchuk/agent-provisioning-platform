"""SQLModel tables (specs/01-data-model.md).

SQLite via SQLModel. IDs are short random strings (`ag_...`, `v_...`) per
`app/ids.py`. JSON columns are stored as TEXT (SQLAlchemy JSON type, which
SQLite backs with TEXT).

Enum-like fields (`channel`, `role`, `source`, `status`, etc.) are plain
`str` columns with the allowed values documented in each field's
description — SQLite has no native enum type and the spec doesn't require
one; validation of allowed values belongs to the service layer built on top
of these tables in later phases.

Invariants enforced elsewhere in this module / in `app/services.py`:
  DM-1 agent_versions rows are never updated after insert (no UPDATE path
       exists in the service layer; `services.update_version` raises).
  DM-2 conversations.version_id is pinned at creation time.
  DM-3 guidelines[].id values are unique within a version and stable across
       versions when unchanged (enforced in services.create_version).
  DM-4 split="benchmark" eval_cases MAY have parent_case_id set (inert
       column retained across the v2 cherry-pick verdict rewrite -- a later
       phase retires it; no new logic is built around it in this phase).
  DM-5 agent_versions.number is strictly increasing per agent (enforced in
       services.create_version + a DB unique constraint below).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import ForeignKey
from sqlmodel import JSON, Column, Field, SQLModel, UniqueConstraint


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# --------------------------------------------------------------------------
# agents
# --------------------------------------------------------------------------
class Agent(SQLModel, table=True):
    __tablename__ = "agents"

    id: str = Field(primary_key=True)
    slug: str = Field(unique=True, index=True)  # used in share URL
    name: str
    description: str = ""
    # use_alter=True: agents <-> agent_versions is a circular FK reference
    # (an agent points at its deployed version; a version points back at its
    # agent). use_alter lets SQLAlchemy emit this FK as a separate ALTER so
    # table creation/drop order isn't ambiguous.
    deployed_version_id: Optional[str] = Field(
        default=None,
        sa_column=Column(
            ForeignKey("agent_versions.id", use_alter=True, name="fk_agents_deployed_version_id")
        ),
    )
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# agent_versions
# --------------------------------------------------------------------------
class AgentVersion(SQLModel, table=True):
    __tablename__ = "agent_versions"
    __table_args__ = (UniqueConstraint("agent_id", "number", name="uq_agent_version_number"),)

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    number: int  # 1, 2, 3... per agent (DM-5: strictly increasing)
    parent_version_id: Optional[str] = Field(default=None, foreign_key="agent_versions.id")
    system_prompt: str = ""
    # [{id, section, text, addresses: [case_id]}]
    guidelines: list = Field(default_factory=list, sa_column=Column(JSON))
    # subset of ["bash","read_file","write_file","edit_file","list_files"]
    tools: list = Field(default_factory=list, sa_column=Column(JSON))
    model: Optional[str] = None  # null = default from env
    max_steps: int = 15
    tool_timeout_s: int = 30
    # [{name, path_on_disk, size}]
    files: list = Field(default_factory=list, sa_column=Column(JSON))
    source: str = "created"  # created | manual | proposal
    change_note: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# conversations
# --------------------------------------------------------------------------
class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    version_id: str = Field(foreign_key="agent_versions.id")  # pinned at creation (DM-2)
    channel: str = "share"  # share | api | playground
    sandbox_id: Optional[str] = None
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# messages
# --------------------------------------------------------------------------
class Message(SQLModel, table=True):
    __tablename__ = "messages"

    id: str = Field(primary_key=True)
    conversation_id: str = Field(foreign_key="conversations.id")
    seq: int
    role: str  # user | assistant | tool
    content: str = ""
    tool_calls: Optional[list] = Field(default=None, sa_column=Column(JSON))
    tool_call_id: Optional[str] = None
    run_id: Optional[str] = Field(default=None, foreign_key="runs.id")


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------
class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: str = Field(primary_key=True)
    conversation_id: Optional[str] = Field(default=None, foreign_key="conversations.id")  # null for eval trials
    version_id: str = Field(foreign_key="agent_versions.id")
    source: str = "chat"  # chat | api | eval
    status: str = "running"  # running | succeeded | failed | step_limit
    trace: list = Field(default_factory=list, sa_column=Column(JSON))
    final_answer: str = ""
    steps: int = 0
    error: Optional[str] = None
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None


# --------------------------------------------------------------------------
# feedback
# --------------------------------------------------------------------------
class Feedback(SQLModel, table=True):
    __tablename__ = "feedback"

    id: str = Field(primary_key=True)
    run_id: str = Field(foreign_key="runs.id")
    conversation_id: str = Field(foreign_key="conversations.id")
    rating: str  # up | down
    correction: Optional[str] = None
    status: str = "new"  # new | converted | dismissed
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# eval_cases
# --------------------------------------------------------------------------
class EvalCase(SQLModel, table=True):
    __tablename__ = "eval_cases"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    name: str
    axis: str = "accuracy"  # free text; defaults: accuracy, format, tool-use, safety
    history: list = Field(default_factory=list, sa_column=Column(JSON))
    check_type: str = "contains"  # contains | python_assert | llm_judge | numeric
    check_spec: dict = Field(default_factory=dict, sa_column=Column(JSON))
    pinned: bool = False
    # v2 cherry-pick (specs/specs-v2/specs/04 "Two splits" D-33): replaces the
    # v1 hidden/sibling mechanism. "improve" cases may be shown to the
    # improver (cases_for_improver); "benchmark" cases never are -- used only
    # to judge generalization (verdict's bench_target_delta).
    split: str = "improve"  # improve | benchmark
    origin: str = "feedback"  # feedback | ground_truth | variant
    # Inert in this phase -- kept as a column only; no new logic reads it
    # (a later phase retires it entirely, per this phase's instructions).
    parent_case_id: Optional[str] = Field(default=None, foreign_key="eval_cases.id")
    from_feedback_id: Optional[str] = Field(default=None, foreign_key="feedback.id")
    status: str = "draft"  # draft | active | dismissed


# --------------------------------------------------------------------------
# eval_runs
# --------------------------------------------------------------------------
class EvalRun(SQLModel, table=True):
    __tablename__ = "eval_runs"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    version_id: str = Field(foreign_key="agent_versions.id")
    status: str = "running"
    trials_per_case: int = 3
    # v2 cherry-pick (D-32 cost limit): stubbed field only -- no cost
    # metering infra exists in this phase, so this is ALWAYS None ("not
    # measured"), never 0 or an error. `compute_verdict` must treat a None
    # cost as "not measured" (cost_delta_pct = None, a "Cost not measured"
    # warning), not as a passing zero-cost comparison.
    cost_usd: Optional[float] = None
    started_at: datetime = Field(default_factory=_utcnow)
    finished_at: Optional[datetime] = None


# --------------------------------------------------------------------------
# eval_results
# --------------------------------------------------------------------------
class EvalResult(SQLModel, table=True):
    __tablename__ = "eval_results"

    id: str = Field(primary_key=True)
    eval_run_id: str = Field(foreign_key="eval_runs.id")
    case_id: str = Field(foreign_key="eval_cases.id")
    trial: int  # 0..N-1
    passed: bool
    reason: str = ""
    run_id: str = Field(foreign_key="runs.id")


# --------------------------------------------------------------------------
# policies (one per agent)
# --------------------------------------------------------------------------
class Policy(SQLModel, table=True):
    __tablename__ = "policies"

    agent_id: str = Field(primary_key=True, foreign_key="agents.id")
    # v2 cherry-pick: renamed from min_avg_improvement_pct -- same meaning
    # (minimum required pass-rate gain), now measured only against the
    # proposal's single target_axis rather than all visible cases combined.
    # Default is 10.0 per specs/specs-v2/specs/01-data-model.md's policies
    # table ("min_target_gain_pct | 10.0 (v2, replaces min_avg_improvement_pct)")
    # -- NOT 5.0 (v1's min_avg_improvement_pct default); the two are different
    # fields measuring different things (single-axis gain vs. all-cases average)
    # and the spec gives them different defaults.
    min_target_gain_pct: float = 10.0
    max_regressions: dict = Field(
        default_factory=lambda: {"accuracy": 0, "safety": 0, "tool-use": 1, "format": 1},
        sa_column=Column(JSON),
    )
    trials_per_case: int = 3
    pass_threshold: int = 2
    # v2 cherry-pick additions (D-35 axis floors; D-32 cost limit; stored but
    # not yet gated on: min_signals/cooldown_hours/max_open_proposals are
    # signals/clustering concepts out of scope this phase -- nothing reads
    # them yet, they just validate and persist).
    # Default {"safety": 100} per specs/01-data-model.md's policies table.
    axis_floors: dict = Field(
        default_factory=lambda: {"safety": 100.0}, sa_column=Column(JSON)
    )
    max_cost_increase_pct: float = 25.0
    min_signals: int = 3
    cooldown_hours: int = 24
    max_open_proposals: int = 1


# --------------------------------------------------------------------------
# proposals
# --------------------------------------------------------------------------
class Proposal(SQLModel, table=True):
    __tablename__ = "proposals"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    base_version_id: str = Field(foreign_key="agent_versions.id")
    candidate_version_id: Optional[str] = Field(default=None, foreign_key="agent_versions.id")
    diagnoses: list = Field(default_factory=list, sa_column=Column(JSON))
    ops: list = Field(default_factory=list, sa_column=Column(JSON))
    skipped: list = Field(default_factory=list, sa_column=Column(JSON))
    lint: list = Field(default_factory=list, sa_column=Column(JSON))
    base_eval_run_id: Optional[str] = Field(default=None, foreign_key="eval_runs.id")
    cand_eval_run_id: Optional[str] = Field(default=None, foreign_key="eval_runs.id")
    verdict: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # v2 cherry-pick: the exact policy values (as a dict) the verdict above
    # was computed against, captured at evaluation time -- used only to
    # detect staleness (verdict_is_stale) if the policy changes after the
    # fact. Never used to recompute pass/fail.
    policy_snapshot: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    status: str = "generating"  # generating | evaluating | ready | accepted | rejected | failed
    decision_note: Optional[str] = None  # required when accepting with verdict.meets_policy = false
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# api_keys (P1) — table defined now per spec; no endpoints yet.
# --------------------------------------------------------------------------
class ApiKey(SQLModel, table=True):
    __tablename__ = "api_keys"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    key_hash: str  # sha256
    last4: str
    revoked: bool = False
    created_at: datetime = Field(default_factory=_utcnow)

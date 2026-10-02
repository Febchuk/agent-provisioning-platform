"""SQLModel tables (specs/specs-v2/specs/01-data-model.md, v2).

SQLite via SQLModel. IDs are short random strings (`ag_...`, `v_...`) per
`app/ids.py`. JSON columns are stored as TEXT (SQLAlchemy JSON type, which
SQLite backs with TEXT).

Enum-like fields (`channel`, `role`, `source`, `status`, etc.) are plain
`str` columns with the allowed values documented in each field's
description — SQLite has no native enum type and the spec doesn't require
one; validation of allowed values belongs to the service layer built on top
of these tables in later phases.

This is the v2 Phase 1 (data-model cutover) schema. Per the v2 migration
brief, this phase is SCHEMA-ONLY: it does not implement signals/issues
logic, the verdict rewrite, per-run version resolution, cost/grounding
computation, or any other new behavior. See `specs/specs-v2/specs/
CHANGELOG-v2.md`'s migration-notes table for what changed and why.

Invariants enforced elsewhere in this module / in `app/services.py`:
  DM-1 agent_versions rows are never updated after insert (no UPDATE path
       exists in the service layer; `services.update_version` raises).
  DM-2 (v2, rewritten; NOT YET IMPLEMENTED — this phase only renames the
       column) `conversations.started_on_version_id` is informational only
       ("version at creation"), not authoritative for routing runs. The
       real "resolve the deployed version per run, emit version.changed"
       behavior is a LATER phase (Phase 4). Until then, the service layer
       still does the OLD v1 "pinned" behavior using this renamed field
       (see `services.version_for_next_turn`'s docstring).
  DM-3 guidelines[].id values are unique within a version and stable across
       versions when unchanged (enforced in services.create_version).
  DM-4 (v2, rewritten) Cases with `split = benchmark` SHALL never be
       returned by `cases_for_improver()`. (Enforced starting Phase 5 — this
       phase only adds the `split` column; v1's `hidden`/`parent_case_id`
       mechanism and its DM-4 invariant are removed.)
  DM-5 agent_versions.number is strictly increasing per agent (enforced in
       services.create_version + a DB unique constraint below).
  DM-6 (v2, NOT YET IMPLEMENTED) issues.signal_count equals the number of
       signals with that issue_id. Lands with the signals/issues subsystem
       (Phase 7).
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
    # v2, P1: {enabled, model} — reviewer not implemented in this phase; the
    # column exists so later phases don't need another migration for it.
    reviewer: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    max_steps: int = 15
    tool_timeout_s: int = 30
    # [{name, path_on_disk, size}]
    files: list = Field(default_factory=list, sa_column=Column(JSON))
    source: str = "created"  # created | manual | proposal
    change_note: str = ""
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# conversations — v2
# --------------------------------------------------------------------------
class Conversation(SQLModel, table=True):
    __tablename__ = "conversations"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    # v2 (CH-1): anonymous visitor cookie; "owner" for playground.
    visitor_id: str = "owner"
    channel: str = "share"  # share | api | playground
    # v2: first ~60 chars of the first user message.
    title: str = ""
    # v2: renamed from `version_id`. Informational only — "version at
    # creation," NOT authoritative for routing runs (DM-2 rewritten). The
    # real per-run version resolution lands in a LATER phase (Phase 4); for
    # now the service layer still reads this field as if it were pinned
    # (see services.version_for_next_turn).
    started_on_version_id: str = Field(foreign_key="agent_versions.id")
    sandbox_id: Optional[str] = None  # live sandbox id; null when stopped
    # v2 (IS-10): named volume / provider sandbox id that outlives the live
    # sandbox. Not populated until the isolation/sandbox-provider phase.
    workspace_ref: str = ""
    # v2 (IS-10): whether this conversation's workspace files have expired.
    files_expired: bool = False
    # v2: sorts the chat-history list. Defaults to created_at at creation;
    # updated as new messages arrive (not wired up until the chat-history
    # UI phase).
    last_message_at: datetime = Field(default_factory=_utcnow)
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
    # v2: on assistant messages, which version answered. Not populated until
    # per-run version resolution lands (Phase 4) — nullable until then.
    version_id: Optional[str] = Field(default=None, foreign_key="agent_versions.id")


# --------------------------------------------------------------------------
# runs
# --------------------------------------------------------------------------
class Run(SQLModel, table=True):
    __tablename__ = "runs"

    id: str = Field(primary_key=True)
    conversation_id: Optional[str] = Field(default=None, foreign_key="conversations.id")  # null for eval trials
    version_id: str = Field(foreign_key="agent_versions.id")
    source: str = "chat"  # chat | api | eval | repro (repro: v2)
    status: str = "running"  # running | succeeded | failed | step_limit
    trace: list = Field(default_factory=list, sa_column=Column(JSON))
    final_answer: str = ""
    steps: int = 0
    # v2 (QC-1): {prompt_tokens, completion_tokens, model_calls, latency_ms,
    # cost_usd}. Not populated until the cost/quality phase.
    usage: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # v2 (QC-8): {claims, ungrounded}. Not populated until the cost/quality
    # phase.
    grounding: Optional[dict] = Field(default=None, sa_column=Column(JSON))
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
    # v2 (11-signals-and-triggers.md): the signal this feedback created, if
    # any. Not populated until the signals/issues phase (Phase 7).
    signal_id: Optional[str] = Field(default=None, foreign_key="signals.id")
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# signals — v2 (11-signals-and-triggers.md). Schema only in this phase; no
# signal-emitting logic is implemented yet (lands in Phase 7).
# --------------------------------------------------------------------------
class Signal(SQLModel, table=True):
    __tablename__ = "signals"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    conversation_id: Optional[str] = Field(default=None, foreign_key="conversations.id")
    run_id: Optional[str] = Field(default=None, foreign_key="runs.id")
    version_id: str = Field(foreign_key="agent_versions.id")
    type: str = ""
    payload: dict = Field(default_factory=dict, sa_column=Column(JSON))
    issue_id: Optional[str] = Field(default=None, foreign_key="issues.id")
    created_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# issues — v2 (11-signals-and-triggers.md). Schema only in this phase; no
# issue-creation/triage logic is implemented yet (lands in Phase 7).
# --------------------------------------------------------------------------
class Issue(SQLModel, table=True):
    __tablename__ = "issues"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    title: str = ""
    lesson: str = ""
    axis: str = "accuracy"
    status: str = "open"
    signal_count: int = 0  # DM-6: kept consistent with signals.issue_id count
    repro: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # v2: see DECISIONS.md for the trigger_source-on-issues resolution — the
    # 01-data-model.md table lists this field on `issues`; 11's own prose
    # summary omits it there (only mentions it for `proposals`). Resolved in
    # favor of the data-model table: an issue can carry its own trigger
    # provenance (e.g. SG-13's "Improve anyway" override) before a proposal
    # is created from it.
    trigger_source: Optional[str] = None  # issue_ready | manual | manual_override
    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)


# --------------------------------------------------------------------------
# eval_cases — v2 rewrite: `hidden`/`parent_case_id` removed; `split`,
# `origin`, `issue_id` added.
# --------------------------------------------------------------------------
class EvalCase(SQLModel, table=True):
    __tablename__ = "eval_cases"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    name: str
    axis: str = "accuracy"  # free text; defaults: accuracy, format, tool-use, safety
    history: list = Field(default_factory=list, sa_column=Column(JSON))
    check_type: str = "contains"  # contains | python_assert | llm_judge | numeric (v2) | grounded (v2)
    check_spec: dict = Field(default_factory=dict, sa_column=Column(JSON))
    pinned: bool = False
    # v2: replaces `hidden`. `improve` = improver may see failures;
    # `benchmark` = never shown to the improver (DM-4, enforced starting
    # Phase 5's `cases_for_improver`).
    split: str = "improve"  # improve | benchmark
    # v2: replaces the siblings mechanism (`parent_case_id`).
    origin: str = "owner"  # feedback | owner | ground_truth | variant
    # v2: FK to the issue this case was drafted from, if any.
    issue_id: Optional[str] = Field(default=None, foreign_key="issues.id")
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
    # NOTE: the v2 spec's `eval_runs` row lists a `purpose` field
    # (`base`·`candidate`·`adhoc`·`compare`) that doesn't exist in this v1
    # schema at all (there's only `status`). Introducing it is not a
    # mechanical rename of an existing field, so it's out of scope for this
    # schema-cutover phase per the brief's "schema and mechanical-reference-
    # fixing only" constraint -- left for whichever later phase actually
    # needs `purpose` (the improver/proposal phases already distinguish
    # base/candidate eval runs structurally via `Proposal.base_eval_run_id`/
    # `cand_eval_run_id`, so nothing is broken by its absence here).
    status: str = "running"
    trials_per_case: int = 3
    # v2: nullable; not populated until the cost/quality phase.
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
# policies (one per agent) — v2 rewrite
# --------------------------------------------------------------------------
class Policy(SQLModel, table=True):
    __tablename__ = "policies"

    agent_id: str = Field(primary_key=True, foreign_key="agents.id")
    # v2: renamed from `min_avg_improvement_pct`; same default (5.0).
    min_target_gain_pct: float = 5.0
    max_regressions: dict = Field(
        default_factory=lambda: {"accuracy": 0, "safety": 0, "tool-use": 1, "format": 1},
        sa_column=Column(JSON),
    )
    # v2: minimum candidate pass rate % per axis, e.g. {"safety": 100}.
    axis_floors: dict = Field(default_factory=dict, sa_column=Column(JSON))
    # v2: max allowed cost increase (%) for a candidate vs. base.
    max_cost_increase_pct: float = 25.0
    trials_per_case: int = 3
    pass_threshold: int = 2
    # v2 (11-signals-and-triggers.md): trigger-policy fields.
    min_signals: int = 3
    cooldown_hours: int = 24
    max_open_proposals: int = 1
    min_signal_rate_pct: float = 5.0
    # v2 (P1): LLM monitor sampling rate; 0 = disabled.
    monitor_sample_pct: float = 0.0


# --------------------------------------------------------------------------
# proposals — v2 rewrite
# --------------------------------------------------------------------------
class Proposal(SQLModel, table=True):
    __tablename__ = "proposals"

    id: str = Field(primary_key=True)
    agent_id: str = Field(foreign_key="agents.id")
    base_version_id: str = Field(foreign_key="agent_versions.id")
    candidate_version_id: Optional[str] = Field(default=None, foreign_key="agent_versions.id")
    # v2: required by the spec table, but proposal-creation logic that would
    # populate it doesn't land until a later phase (Phase 5+), so this
    # column is nullable at the DB level for now (see DECISIONS.md).
    target_axis: Optional[str] = None
    # v2: FK to the issue this proposal was created from, if any.
    issue_id: Optional[str] = Field(default=None, foreign_key="issues.id")
    # v2: issue_ready | manual | manual_override. Nullable for now -- no
    # caller sets it yet.
    trigger_source: Optional[str] = None
    diagnoses: list = Field(default_factory=list, sa_column=Column(JSON))
    ops: list = Field(default_factory=list, sa_column=Column(JSON))
    skipped: list = Field(default_factory=list, sa_column=Column(JSON))
    lint: list = Field(default_factory=list, sa_column=Column(JSON))
    base_eval_run_id: Optional[str] = Field(default=None, foreign_key="eval_runs.id")
    cand_eval_run_id: Optional[str] = Field(default=None, foreign_key="eval_runs.id")
    verdict: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    # v2: total cost spent producing the proposal. Not populated until the
    # cost/quality phase.
    cost_usd: Optional[float] = None
    status: str = "generating"  # generating | evaluating | ready | accepted | rejected | failed
    decision_note: Optional[str] = None  # required when accepting with verdict.meets_policy = false
    created_at: datetime = Field(default_factory=_utcnow)
    decided_at: Optional[datetime] = None


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

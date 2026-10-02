"""specs/01-data-model.md §Verification — the three named invariant tests,
plus direct unit tests for DM-3 and DM-4 (guidelines uniqueness / hidden
cases) since those are listed as invariants but not given their own named
test in the spec's Verification list.
"""
import pytest

from app.models import AgentVersion
from app.services import (
    DuplicateGuidelineIdError,
    HiddenCaseMissingParentError,
    ImmutableVersionError,
    create_agent,
    create_conversation,
    create_eval_case,
    create_version,
    deploy_version,
    version_for_next_turn,
)


# ---------------------------------------------------------------------------
# DM-1
# ---------------------------------------------------------------------------
def test_versions_immutable(session):
    """DM-1: attempt to modify an agent_versions row via the service layer raises."""
    from app.services import update_version

    agent = create_agent(session, name="Revenue Analyst", slug="revenue-analyst")
    version = create_version(session, agent_id=agent.id, system_prompt="You are helpful.")

    with pytest.raises(ImmutableVersionError):
        update_version(session, version.id, system_prompt="Something else")

    # And the row on disk is untouched.
    reloaded = session.get(AgentVersion, version.id)
    assert reloaded.system_prompt == "You are helpful."


# ---------------------------------------------------------------------------
# DM-2
# ---------------------------------------------------------------------------
def test_conversation_pins_version(session):
    """DM-2: deploy v2 after a conversation started on v1; next turn still uses v1."""
    agent = create_agent(session, name="Revenue Analyst", slug="revenue-analyst-2")
    v1 = create_version(session, agent_id=agent.id, system_prompt="v1 prompt")
    deploy_version(session, agent_id=agent.id, version_id=v1.id)

    conversation = create_conversation(session, agent_id=agent.id, channel="share")
    assert conversation.version_id == v1.id

    v2 = create_version(session, agent_id=agent.id, system_prompt="v2 prompt", parent_version_id=v1.id)
    deploy_version(session, agent_id=agent.id, version_id=v2.id)

    # Agent's deployed pointer moved...
    session.refresh(agent)
    assert agent.deployed_version_id == v2.id

    # ...but the existing conversation's next turn still resolves to v1.
    version_in_use = version_for_next_turn(session, conversation_id=conversation.id)
    assert version_in_use.id == v1.id
    assert version_in_use.system_prompt == "v1 prompt"


# ---------------------------------------------------------------------------
# DM-5
# ---------------------------------------------------------------------------
def test_version_numbers_monotonic(session):
    """DM-5: agent_versions.number is strictly increasing per agent."""
    agent = create_agent(session, name="Repo Helper", slug="repo-helper")
    v1 = create_version(session, agent_id=agent.id)
    v2 = create_version(session, agent_id=agent.id, parent_version_id=v1.id)
    v3 = create_version(session, agent_id=agent.id, parent_version_id=v2.id)

    assert [v1.number, v2.number, v3.number] == [1, 2, 3]

    # A second, independent agent starts its own numbering at 1.
    other = create_agent(session, name="Blank Agent", slug="blank-agent")
    other_v1 = create_version(session, agent_id=other.id)
    assert other_v1.number == 1


# ---------------------------------------------------------------------------
# DM-3 (guideline id uniqueness + stability across versions)
# ---------------------------------------------------------------------------
def test_guideline_ids_unique_within_version(session):
    agent = create_agent(session, name="Dup Guidelines", slug="dup-guidelines")
    with pytest.raises(DuplicateGuidelineIdError):
        create_version(
            session,
            agent_id=agent.id,
            guidelines=[
                {"id": "g_1", "section": "Revenue rules", "text": "a", "addresses": []},
                {"id": "g_1", "section": "Revenue rules", "text": "b", "addresses": []},
            ],
        )


def test_guideline_ids_stable_across_versions_when_unchanged(session):
    agent = create_agent(session, name="Stable Guidelines", slug="stable-guidelines")
    v1 = create_version(
        session,
        agent_id=agent.id,
        guidelines=[{"id": "g_1", "section": "Revenue rules", "text": "Exclude refunds.", "addresses": []}],
    )
    # v2 created without specifying guidelines -> copied verbatim from parent,
    # so the rule keeps the same id.
    v2 = create_version(session, agent_id=agent.id, parent_version_id=v1.id)
    assert v2.guidelines == v1.guidelines
    assert v2.guidelines[0]["id"] == "g_1"


# ---------------------------------------------------------------------------
# DM-4 (hidden cases require parent_case_id)
# ---------------------------------------------------------------------------
def test_hidden_case_requires_parent_case_id(session):
    agent = create_agent(session, name="Case Agent", slug="case-agent")
    with pytest.raises(HiddenCaseMissingParentError):
        create_eval_case(
            session,
            agent_id=agent.id,
            name="hidden sibling",
            check_type="llm_judge",
            check_spec={"rubric": "..."},
            hidden=True,
            parent_case_id=None,
        )


def test_hidden_case_with_parent_case_id_succeeds(session):
    agent = create_agent(session, name="Case Agent 2", slug="case-agent-2")
    parent = create_eval_case(
        session,
        agent_id=agent.id,
        name="Q3 revenue excludes refunds",
        check_type="contains",
        check_spec={"all": ["412,380"], "none": []},
        status="active",
    )
    sibling = create_eval_case(
        session,
        agent_id=agent.id,
        name="Q3 revenue excludes refunds (sibling)",
        check_type="llm_judge",
        check_spec={"rubric": "Passes if refunds are excluded."},
        hidden=True,
        parent_case_id=parent.id,
    )
    assert sibling.hidden is True
    assert sibling.parent_case_id == parent.id

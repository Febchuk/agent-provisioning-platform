"""specs/specs-v2/specs/01-data-model.md §Verification — the three named
invariant tests that still apply pre-v2-Phase-4, plus direct unit tests for
DM-3 (guidelines uniqueness) since it's listed as an invariant but not given
its own named test in the spec's Verification list.

v1's DM-4 tests (`test_hidden_case_requires_parent_case_id`,
`test_hidden_case_with_parent_case_id_succeeds`) are REMOVED in this v2
Phase 1 cutover: `hidden`/`parent_case_id` and the invariant that validated
them no longer exist (replaced by `split`/`origin`, enforced starting
Phase 5 -- see DECISIONS.md).

Two v2 skeleton tests are added at the bottom of this file for later
phases' traceability (`test_signal_count_consistent` for Phase 7,
`test_run_uses_deployed_version_and_emits_version_changed` for Phase 4).
"""
import pytest

from app.models import AgentVersion
from app.services import (
    DuplicateGuidelineIdError,
    ImmutableVersionError,
    create_agent,
    create_conversation,
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
    """DM-2 (v2: field renamed `version_id` -> `started_on_version_id`; this
    phase still implements the OLD v1 "pinned" behavior with the renamed
    field -- the real per-run resolution lands in Phase 4): deploy v2 after
    a conversation started on v1; next turn still uses v1.
    """
    agent = create_agent(session, name="Revenue Analyst", slug="revenue-analyst-2")
    v1 = create_version(session, agent_id=agent.id, system_prompt="v1 prompt")
    deploy_version(session, agent_id=agent.id, version_id=v1.id)

    conversation = create_conversation(session, agent_id=agent.id, channel="share")
    assert conversation.started_on_version_id == v1.id

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
# v2 skeleton tests — traceability placeholders only, real logic lands in
# the phase named in each docstring/skip reason.
# ---------------------------------------------------------------------------
@pytest.mark.skip(reason="implemented in v2 Phase 7 (signals and issues)")
def test_signal_count_consistent():
    """DM-6: `issues.signal_count` equals the number of signals with that
    `issue_id`. Lands with the signals/issues subsystem (Phase 7).
    """


@pytest.mark.skip(reason="implemented in v2 Phase 4 (per-run version resolution)")
def test_run_uses_deployed_version_and_emits_version_changed():
    """DM-2 (rewritten): a run uses the agent's CURRENTLY DEPLOYED version
    (not the conversation's `started_on_version_id`), recorded on
    `runs.version_id` and the assistant message; a `version.changed {from,
    to}` event is emitted when it differs from the conversation's previous
    run. Replaces `test_conversation_pins_version` once this lands
    (Phase 4).
    """

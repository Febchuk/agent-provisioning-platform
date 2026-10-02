"""T3.5 — starter eval case seeding (specs/tasks.md T3.5).

Verifies: 6 cases, exactly one pinned ("top 3 products"), at least one
safety-axis case that would fail on a naive agent ("won't delete
orders.csv"), and that the refund case is NOT among them (it arrives via
feedback later, per T3.5's own text).
"""
from app.seed_cases import build_seed_case_specs, seed_eval_cases
from app.services import create_agent


def test_build_seed_case_specs_shape():
    specs = build_seed_case_specs()
    assert len(specs) == 6

    for spec in specs:
        assert spec["check_type"] in ("contains", "python_assert", "llm_judge")
        assert spec["history"]
        assert spec["history"][-1]["role"] == "user"

    pinned = [s for s in specs if s["pinned"]]
    assert len(pinned) == 1
    assert "top 3" in pinned[0]["name"].lower()

    safety_cases = [s for s in specs if s["axis"] == "safety"]
    assert len(safety_cases) >= 1
    assert any("delete" in s["name"].lower() for s in safety_cases)

    names = [s["name"].lower() for s in specs]
    assert not any("q3" in n and "refund" in n for n in names), (
        "the Q3-excludes-refunds case must NOT be pre-seeded (T3.5: arrives via feedback)"
    )


def test_seed_eval_cases_persists_six_active_cases(session):
    agent = create_agent(session, name="Seed Cases Agent", slug="seed-cases-agent")
    cases = seed_eval_cases(session, agent.id)

    assert len(cases) == 6
    assert all(c.status == "active" for c in cases)
    assert all(c.agent_id == agent.id for c in cases)
    assert all(c.split == "improve" for c in cases)

    pinned = [c for c in cases if c.pinned]
    assert len(pinned) == 1

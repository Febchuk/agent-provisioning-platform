#!/usr/bin/env python3
"""T3.5 — seed the 6 starter eval cases onto a data-analyst demo agent
(specs/tasks.md T3.5).

Creates a NEW data-analyst agent (via the same `create_agent_from_template`
service Phase 3's `POST /agents` uses) and seeds `app.seed_cases.seed_eval_cases`
onto it, so `scripts/smoke_evals.py` (and anyone demoing the feedback/evals
loop) has a ready v1 with active cases to run.

Deliberately does NOT seed the "Q3 revenue excludes refunds" case -- per
T3.5, that one "arrives via feedback during demo" (created later through the
real feedback -> draft-case -> confirm flow, not pre-loaded here).

Usage:
    python scripts/seed_eval_cases.py [--name "Revenue Analyst"]

Prints the created agent's id/slug and the 6 case names/axes/check_types so
a human can see what was seeded without needing the API running.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from sqlmodel import Session  # noqa: E402

from app import db  # noqa: E402
from app.evals import get_or_create_policy  # noqa: E402
from app.seed_cases import build_seed_case_specs, seed_eval_cases  # noqa: E402
from app.services import create_agent_from_template  # noqa: E402

# `axis_floors: {"safety": 100.0}` is Policy's bare model default per
# specs/01-data-model.md's policies table, so no override is needed here --
# a Data Analyst agent's "safety" axis case is "won't delete orders.csv"
# (seed_cases.py); a correct agent should pass that 100% of the time, which
# is exactly the spec default. Strict accuracy / loose format is expressed
# by the existing max_regressions default ({"accuracy": 0, "format": 1,
# ...}, app/evals.py DEFAULT_MAX_REGRESSIONS).


def seed_demo_agent_with_cases(session: Session, *, name: str = "Revenue Analyst") -> dict:
    agent = create_agent_from_template(
        session, name=name, description="Analyzes orders.csv: revenue, refunds, top products.", template_id="data-analyst"
    )
    cases = seed_eval_cases(session, agent.id)
    policy = get_or_create_policy(session, agent.id)
    return {"agent": agent, "cases": cases, "policy": policy}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", default="Revenue Analyst", help="Name for the new demo agent.")
    args = parser.parse_args()

    db.init_db()
    with Session(db.engine) as session:
        result = seed_demo_agent_with_cases(session, name=args.name)
        agent = result["agent"]
        cases = result["cases"]

        print(f"Created agent: id={agent.id} slug={agent.slug} deployed_version_id={agent.deployed_version_id}")
        print(f"Seeded {len(cases)} eval cases:")
        for case in cases:
            pinned_marker = " [pinned]" if case.pinned else ""
            print(f"  - [{case.axis}/{case.split}/{case.check_type}] {case.name}{pinned_marker}")
        policy = result["policy"]
        print(f"Policy: min_target_gain_pct={policy.min_target_gain_pct} axis_floors={policy.axis_floors}")

    print(
        "\nNote: the 'Q3 revenue excludes refunds' case is NOT seeded here -- "
        "per specs/tasks.md T3.5 it arrives later via feedback during the demo."
    )


if __name__ == "__main__":
    main()

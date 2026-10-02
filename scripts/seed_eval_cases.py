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
from app.evals import get_or_create_policy, update_policy  # noqa: E402
from app.seed_cases import build_seed_case_specs, seed_eval_cases  # noqa: E402
from app.services import create_agent_from_template  # noqa: E402

# v2 cherry-pick: demo policy defaults for the Data Analyst template -- NOT
# baked into Policy's bare model default (`axis_floors: dict = {}`, per
# models.py) since an agent created outside this seed script shouldn't
# silently get an opinionated floor it never asked for. A Data Analyst
# agent's "safety" axis case is "won't delete orders.csv" (seed_cases.py) --
# a correct agent should pass that 100% of the time, so a 100% floor is the
# sensible default for THIS template. Strict accuracy / loose format (the
# other half of "strict accuracy, loose format" called out in this phase's
# instructions) is already expressed by the existing max_regressions default
# ({"accuracy": 0, "format": 1, ...}, app/evals.py DEFAULT_MAX_REGRESSIONS) --
# not duplicated here.
DATA_ANALYST_AXIS_FLOORS = {"safety": 100.0}


def seed_demo_agent_with_cases(session: Session, *, name: str = "Revenue Analyst") -> dict:
    agent = create_agent_from_template(
        session, name=name, description="Analyzes orders.csv: revenue, refunds, top products.", template_id="data-analyst"
    )
    cases = seed_eval_cases(session, agent.id)
    get_or_create_policy(session, agent.id)  # ensure a policy row exists before updating it
    policy = update_policy(session, agent.id, axis_floors=DATA_ANALYST_AXIS_FLOORS)
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

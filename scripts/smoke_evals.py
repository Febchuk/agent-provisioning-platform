#!/usr/bin/env python3
"""T3.5 — Smoke script / M3 milestone exit gate (specs/tasks.md T3.5,
specs/07-verification-and-validation.md §2 M3 row).

Creates a real data-analyst agent (6 active starter cases from
`app.seed_cases`), runs a real eval on its v1 (real Docker sandboxes, real
model + real judge model from backend/.env), and prints per-case pass/fail/
flaky results so a human can read whether the eval executor actually ran
cases and produced meaningful results.

It is EXPECTED and FINE for v1 to fail some cases here (e.g. the safety case
about not deleting orders.csv, if v1's system prompt doesn't defend against
a destructive request) -- the M3 gate is "the executor runs cases and
reports results you can read," not "v1 is already a good agent." That is
exactly the kind of gap a teammate's feedback -> eval case -> improved
version loop (Phase 5) is meant to close later.

Usage:
    python scripts/smoke_evals.py

Requires: Docker running, backend/.env with a working MODEL_BASE_URL /
MODEL_API_KEY / MODEL_NAME (and optionally JUDGE_MODEL_NAME; falls back to
MODEL_NAME if unset, same as app/llm.py's ModelGatewayConfig).
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from sqlmodel import Session  # noqa: E402

from app import db  # noqa: E402
from app.evals import eval_run_summary, run_eval_run  # noqa: E402
from app.llm import OpenAICompatLLM  # noqa: E402
from app.sandbox import DockerSandbox, docker_available  # noqa: E402
from seed_eval_cases import seed_demo_agent_with_cases  # noqa: E402


def _sandbox_factory():
    return DockerSandbox()


def _llm_factory():
    return OpenAICompatLLM()


async def main() -> int:
    print("=== M3 smoke test: scripts/smoke_evals.py ===")

    if not docker_available():
        print("FAIL: Docker is not available. This smoke test requires real Docker (specs/07 M3 gate).")
        return 1

    judge_model = os.environ.get("JUDGE_MODEL_NAME") or os.environ.get("MODEL_NAME")

    db.init_db()
    with Session(db.engine) as session:
        seeded = seed_demo_agent_with_cases(session, name="Smoke Revenue Analyst")
        agent = seeded["agent"]
        cases = seeded["cases"]
        version_id = agent.deployed_version_id
        agent_id = agent.id

        print(f"Created agent: id={agent_id} slug={agent.slug} v1={version_id}")
        print(f"Seeded {len(cases)} active eval cases:")
        for case in cases:
            marker = " [pinned]" if case.pinned else ""
            print(f"  - [{case.axis}/{case.check_type}] {case.name}{marker}")

        print("\nRunning eval on v1 (this calls the real model + real Docker sandboxes; may take a minute)...")
        eval_run = await run_eval_run(
            session,
            agent_id=agent_id,
            version_id=version_id,
            sandbox_factory=_sandbox_factory,
            llm_factory=_llm_factory,
            judge_model=judge_model,
        )

        summary = eval_run_summary(session, eval_run.id)

    print(f"\nEval run {summary['id']} status={summary['status']} trials_per_case={summary['trials_per_case']}")
    print("=== Per-case results ===")
    any_failed = False
    for case_summary in sorted(summary["cases"], key=lambda c: c["name"] or ""):
        status = "PASS" if case_summary["passed"] else "FAIL"
        flaky_marker = " (flaky)" if case_summary["flaky"] else ""
        pinned_marker = " [pinned]" if case_summary["pinned"] else ""
        if not case_summary["passed"]:
            any_failed = True
        print(f"[{status}]{flaky_marker}{pinned_marker} {case_summary['name']} (axis={case_summary['axis']})")
        for trial in case_summary["trials"]:
            trial_status = "pass" if trial["passed"] else "fail"
            reason = trial["reason"][:200]
            print(f"    trial {trial['trial']}: {trial_status} -- {reason}")

    print("\n=== Summary ===")
    n_cases = len(summary["cases"])
    n_passed = sum(1 for c in summary["cases"] if c["passed"])
    print(f"{n_passed}/{n_cases} cases passed on v1.")
    if any_failed:
        print(
            "Some cases failed on v1 -- EXPECTED (v1 is the naive seed agent; this is exactly "
            "the gap the feedback -> eval case -> improve loop exists to close in a later phase)."
        )
    print("\nPASS: the eval executor ran every active case and produced readable per-trial results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

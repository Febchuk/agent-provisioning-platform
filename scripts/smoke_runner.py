#!/usr/bin/env python3
"""T1.4 — Smoke script / M1 milestone exit gate (specs/tasks.md T1.4,
specs/07-verification-and-validation.md §2 M1 row).

Seeds `orders.csv` (via scripts/seed_demo.py's generator, deterministic
ground truth), creates a real `DockerSandbox`, seeds the CSV into it, and
runs a real turn via `run_turn` with the real `OpenAICompatLLM` asking "How
many rows are in orders.csv?". Prints the full trace and the final answer.

Passes when the final answer contains the true row count (2000, per
seed_demo.py's fixed-seed generator) and the trace shows the agent used
`bash` to find out.

Usage:
    python scripts/smoke_runner.py
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS_DIR))

from seed_demo import compute_ground_truth, generate_rows, write_csv  # noqa: E402

from app.llm import OpenAICompatLLM  # noqa: E402
from app.runner import run_turn  # noqa: E402
from app.sandbox import DockerSandbox, docker_available  # noqa: E402

ORDERS_CSV_PATH = Path(__file__).resolve().parent.parent / "orders.csv"


def make_version() -> SimpleNamespace:
    return SimpleNamespace(
        system_prompt=(
            "You are a data analyst with access to a sandbox containing orders.csv. "
            "When asked to count rows in a CSV file, use a precise shell command "
            "(e.g. counting lines and excluding the header) rather than reading the "
            "file and counting by eye."
        ),
        guidelines=[],
        tools=["bash", "read_file", "list_files"],
        model=None,
        max_steps=10,
        tool_timeout_s=30,
        number=1,
    )


def print_event(event: dict) -> None:
    print(f"  [{event['type']}] {json.dumps({k: v for k, v in event.items() if k != 'type'}, default=str)[:500]}")


async def main() -> int:
    print("=== M1 smoke test: scripts/smoke_runner.py ===")

    if not docker_available():
        print("FAIL: Docker is not available. This smoke test requires real Docker (specs/07 M1 gate).")
        return 1

    # Regenerate orders.csv deterministically and get the ground-truth row count.
    rows = generate_rows()
    write_csv(rows, ORDERS_CSV_PATH)
    gt = compute_ground_truth(rows)
    true_row_count = gt["row_count"]
    print(f"Seeded orders.csv at {ORDERS_CSV_PATH} ({true_row_count} rows, ground truth from seed_demo.py)")

    sandbox = DockerSandbox()
    print(f"Created DockerSandbox id={sandbox.id}")

    try:
        csv_bytes = ORDERS_CSV_PATH.read_bytes()
        sandbox.seed_file("orders.csv", csv_bytes)
        print("Seeded orders.csv into the sandbox at /workspace/orders.csv")

        llm = OpenAICompatLLM()

        print("\nRunning turn: \"How many rows are in orders.csv?\"\n")
        print("--- trace ---")

        async def emit(event: dict) -> None:
            print_event(event)

        result = await run_turn(
            version=make_version(),
            history=[{"role": "user", "content": "How many rows are in orders.csv?"}],
            sandbox=sandbox,
            emit=emit,
            source="eval",
            llm=llm,
        )

        print("--- end trace ---\n")
        print(f"status: {result.status}")
        print(f"steps: {result.steps}")
        print(f"final_answer: {result.final_answer!r}")

        used_bash = any(e["type"] == "tool.call" and e.get("name") == "bash" for e in result.trace)
        print(f"used bash tool: {used_bash}")

        answer_has_true_count = str(true_row_count) in result.final_answer

        print(f"\nground truth row_count: {true_row_count}")
        print(f"final answer contains true row count: {answer_has_true_count}")

        if result.status == "succeeded" and answer_has_true_count and used_bash:
            print("\nPASS: M1 smoke test gate satisfied.")
            return 0
        else:
            print("\nFAIL: M1 smoke test gate not satisfied.")
            return 1
    finally:
        await sandbox.destroy()
        print(f"Destroyed DockerSandbox id={sandbox.id}")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

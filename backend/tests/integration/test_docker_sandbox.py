"""Integration tests: real Docker + real model (specs/07 §1 "Integration" row).

AC-RT-f: given orders.csv seeded, the real model asked "How many rows are in
orders.csv?" answers correctly using bash.
AC-RT-g: bash `curl https://example.com` fails inside the sandbox (no
network).

Run explicitly with: pytest -m integration tests/integration/test_docker_sandbox.py
(excluded from the default `pytest -q` run via pytest.ini's addopts).
"""
import csv
import io

import pytest

from app.llm import OpenAICompatLLM
from app.runner import run_turn
from app.sandbox import DockerSandbox, docker_available

pytestmark = pytest.mark.integration


def _make_orders_csv(n_rows: int = 237) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf, lineterminator="\n")
    writer.writerow(["order_id", "amount"])
    for i in range(n_rows):
        writer.writerow([f"o_{i}", "9.99"])
    return buf.getvalue().encode()


class _FakeVersion:
    def __init__(self, tools, max_steps=10, tool_timeout_s=30):
        self.system_prompt = "You are a data analyst with access to a sandbox."
        self.guidelines = []
        self.tools = tools
        self.model = None
        self.max_steps = max_steps
        self.tool_timeout_s = tool_timeout_s
        self.number = 1


@pytest.fixture()
async def docker_sandbox():
    assert docker_available(), "Docker must be available for integration tests"
    sb = DockerSandbox()
    yield sb
    await sb.destroy()


async def test_real_model_counts_csv_rows_using_bash(docker_sandbox):
    n_rows = 237
    docker_sandbox.seed_file("orders.csv", _make_orders_csv(n_rows))

    llm = OpenAICompatLLM()
    events = []

    async def emit(event):
        events.append(event)

    result = await run_turn(
        version=_FakeVersion(tools=["bash", "read_file", "list_files"]),
        history=[
            {
                "role": "user",
                "content": (
                    "How many rows are in orders.csv (not counting the header)? "
                    "Use a shell command to count precisely rather than reading and counting by eye."
                ),
            }
        ],
        sandbox=docker_sandbox,
        emit=emit,
        source="eval",
        llm=llm,
    )

    assert result.status == "succeeded", f"run did not succeed: {result.error}, trace={result.trace}"
    assert str(n_rows) in result.final_answer, (
        f"expected row count {n_rows} in final answer, got: {result.final_answer!r}"
    )
    tool_calls = [e for e in events if e["type"] == "tool.call"]
    assert any(tc["name"] == "bash" for tc in tool_calls), "expected the model to use bash"


async def test_no_network_from_sandbox(docker_sandbox):
    result = await docker_sandbox.exec("curl -m 5 https://example.com", timeout_s=15)
    assert result.exit_code != 0, f"curl unexpectedly succeeded: {result}"

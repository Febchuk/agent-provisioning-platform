"""T3.2 — check evaluators (specs/04-feedback-and-evals.md "Checks" table).

Each check type gets a pass test and a fail test, plus AC-EV-d
(python_assert sees the trial's actual final answer via
/workspace/.final_answer.txt).
"""
import json

import pytest

from app.checks import FINAL_ANSWER_SANDBOX_PATH, check_contains, check_llm_judge, check_python_assert, run_check
from app.llm import ChatResponse, FakeLLM
from app.sandbox import LocalSandbox

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# contains
# ---------------------------------------------------------------------------
async def test_contains_pass():
    spec = {"all": ["412,380"], "none": ["refunded orders included"]}
    result = await check_contains("Total revenue was $412,380 this quarter.", spec)
    assert result.passed is True


async def test_contains_fail_missing_required():
    spec = {"all": ["412,380"], "none": []}
    result = await check_contains("Total revenue was $500,000 this quarter.", spec)
    assert result.passed is False
    assert "missing" in result.reason.lower()


async def test_contains_fail_forbidden_present():
    spec = {"all": [], "none": ["refunded orders included"]}
    result = await check_contains("Note: refunded orders included in this total.", spec)
    assert result.passed is False
    assert "forbidden" in result.reason.lower()


async def test_contains_case_insensitive_and_strips_commas_and_dollar():
    spec = {"all": ["1234"], "none": []}
    result = await check_contains("The answer is $1,234.", spec)
    assert result.passed is True


# ---------------------------------------------------------------------------
# python_assert
# ---------------------------------------------------------------------------
@pytest.fixture()
async def sandbox():
    sb = LocalSandbox()
    yield sb
    await sb.destroy()


async def test_python_assert_pass(sandbox):
    spec = {"code": "assert 1 + 1 == 2"}
    result = await check_python_assert("anything", spec, sandbox=sandbox)
    assert result.passed is True


async def test_python_assert_fail(sandbox):
    spec = {"code": "assert 1 + 1 == 3, 'math is broken'"}
    result = await check_python_assert("anything", spec, sandbox=sandbox)
    assert result.passed is False
    assert "exited" in result.reason.lower()


async def test_python_assert_missing_code_fails_without_raising(sandbox):
    result = await check_python_assert("anything", {}, sandbox=sandbox)
    assert result.passed is False


async def test_python_assert_no_sandbox_fails_without_raising():
    result = await check_python_assert("anything", {"code": "assert True"}, sandbox=None)
    assert result.passed is False


# ---------------------------------------------------------------------------
# AC-EV-d: python_assert sees the trial's actual final answer
# ---------------------------------------------------------------------------
async def test_python_assert_sees_final_answer(sandbox):
    spec = {
        "code": (
            f"content = open('{FINAL_ANSWER_SANDBOX_PATH}').read(); "
            "assert content.strip() == 'the real final answer', content"
        )
    }
    result = await check_python_assert("the real final answer", spec, sandbox=sandbox)
    assert result.passed is True


async def test_python_assert_detects_wrong_final_answer(sandbox):
    spec = {
        "code": (
            f"content = open('{FINAL_ANSWER_SANDBOX_PATH}').read(); "
            "assert content.strip() == 'expected value', content"
        )
    }
    result = await check_python_assert("a completely different answer", spec, sandbox=sandbox)
    assert result.passed is False


# ---------------------------------------------------------------------------
# llm_judge
# ---------------------------------------------------------------------------
async def test_llm_judge_pass():
    llm = FakeLLM([ChatResponse(content=json.dumps({"pass": True, "reason": "matches rubric"}))])
    spec = {"rubric": "Passes if the answer excludes refunded orders."}
    result = await check_llm_judge(
        "Revenue excluding refunds was $412,380.",
        spec,
        llm=llm,
        history=[{"role": "user", "content": "What was Q3 revenue?"}],
    )
    assert result.passed is True
    assert result.reason == "matches rubric"


async def test_llm_judge_fail():
    llm = FakeLLM([ChatResponse(content=json.dumps({"pass": False, "reason": "includes refunds"}))])
    spec = {"rubric": "Passes if the answer excludes refunded orders."}
    result = await check_llm_judge(
        "Revenue including refunds was $448,000.",
        spec,
        llm=llm,
        history=[{"role": "user", "content": "What was Q3 revenue?"}],
    )
    assert result.passed is False
    assert result.reason == "includes refunds"


async def test_llm_judge_invalid_json_fails_without_raising():
    llm = FakeLLM([ChatResponse(content="not json at all")])
    spec = {"rubric": "anything"}
    result = await check_llm_judge("some answer", spec, llm=llm, history=[])
    assert result.passed is False
    assert "invalid json" in result.reason.lower()


async def test_llm_judge_no_llm_fails_without_raising():
    result = await check_llm_judge("some answer", {"rubric": "x"}, llm=None, history=[])
    assert result.passed is False


async def test_llm_judge_does_not_leak_trace_into_prompt():
    """The judge sees ONLY rubric + user question + final answer, never the
    trace (tool calls / tool outputs) -- verified by inspecting what's
    actually sent to the LLM.
    """
    llm = FakeLLM([ChatResponse(content=json.dumps({"pass": True, "reason": "ok"}))])
    spec = {"rubric": "Passes if correct."}
    history = [
        {"role": "user", "content": "What was Q3 revenue?"},
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{"id": "call_1", "type": "function", "function": {"name": "bash", "arguments": "{\"command\": \"SECRET_TRACE_MARKER\"}"}}],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "SECRET_TOOL_OUTPUT_MARKER"},
    ]
    await check_llm_judge("The answer is 412380.", spec, llm=llm, history=history)

    sent_messages = llm.calls[0]["messages"]
    sent_text = json.dumps(sent_messages)
    assert "SECRET_TRACE_MARKER" not in sent_text
    assert "SECRET_TOOL_OUTPUT_MARKER" not in sent_text
    assert "What was Q3 revenue?" in sent_text
    assert "412380" in sent_text


# ---------------------------------------------------------------------------
# run_check dispatch + EV-8 backstop
# ---------------------------------------------------------------------------
async def test_run_check_unknown_type_fails_without_raising():
    result = await run_check("not_a_real_type", "answer", {})
    assert result.passed is False


async def test_run_check_dispatches_contains():
    result = await run_check("contains", "the answer is 42", {"all": ["42"], "none": []})
    assert result.passed is True

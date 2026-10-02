"""Check evaluators (specs/04-feedback-and-evals.md "Checks (`check_spec`)").

Three check types, each a function `(final_answer, check_spec, *, sandbox,
llm, judge_model) -> CheckResult`:

  - `contains`      pure string matching on the final answer; no IO.
  - `python_assert`  runs code in the TRIAL'S sandbox, after the turn, with
                      the final answer available at /workspace/.final_answer.txt;
                      passes iff exit code 0.
  - `llm_judge`      calls a judge model (temperature 0, JSON mode) with
                      ONLY the rubric + user question + final answer -- never
                      the trace (deliberate isolation per the spec's table).

EV-8: callers (the eval executor) are responsible for catching exceptions
raised here and recording them as a failed trial with the reason -- but each
evaluator here also defensively catches its own expected failure modes
(bad JSON from the judge, non-dict check_spec, sandbox exec errors) and
returns a failed CheckResult rather than raising, so a single bad case can
never kill the whole eval run.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.llm import LLM
from app.sandbox import Sandbox


@dataclass
class CheckResult:
    passed: bool
    reason: str = ""


_NUMERIC_STRIP_RE = re.compile(r"[,$]")


def _normalize_for_contains(text: str) -> str:
    """Case-insensitive; commas and `$` stripped (for numbers like "$1,234")."""
    return _NUMERIC_STRIP_RE.sub("", text).lower()


async def check_contains(
    final_answer: str,
    check_spec: dict,
    *,
    sandbox: Optional[Sandbox] = None,
    llm: Optional[LLM] = None,
    judge_model: Optional[str] = None,
) -> CheckResult:
    """`{"all": [...], "none": [...]}` -- final answer (case-insensitive,
    commas/`$` stripped) contains every `all` item and no `none` item.
    """
    haystack = _normalize_for_contains(final_answer or "")
    all_items = check_spec.get("all", []) or []
    none_items = check_spec.get("none", []) or []

    missing = [item for item in all_items if _normalize_for_contains(str(item)) not in haystack]
    if missing:
        return CheckResult(passed=False, reason=f"missing required text: {missing}")

    present_forbidden = [item for item in none_items if _normalize_for_contains(str(item)) in haystack]
    if present_forbidden:
        return CheckResult(passed=False, reason=f"contains forbidden text: {present_forbidden}")

    return CheckResult(passed=True, reason="all required text present, no forbidden text present")


FINAL_ANSWER_SANDBOX_PATH = ".final_answer.txt"


async def check_python_assert(
    final_answer: str,
    check_spec: dict,
    *,
    sandbox: Optional[Sandbox] = None,
    llm: Optional[LLM] = None,
    judge_model: Optional[str] = None,
) -> CheckResult:
    """`{"code": "..."}` -- runs in the trial's sandbox AFTER the turn, with
    the final answer available at /workspace/.final_answer.txt. Passes iff
    exit code 0.
    """
    if sandbox is None:
        return CheckResult(passed=False, reason="python_assert check requires a sandbox")

    code = check_spec.get("code")
    if not isinstance(code, str) or not code:
        return CheckResult(passed=False, reason="python_assert check_spec missing non-empty 'code'")

    try:
        await sandbox.write(FINAL_ANSWER_SANDBOX_PATH, final_answer or "")
        result = await sandbox.exec(f"python3 -c {_shell_quote(code)}", timeout_s=30)
    except Exception as e:  # EV-8: never let a check exception propagate.
        return CheckResult(passed=False, reason=f"python_assert check errored: {e}")

    if result.exit_code == 0:
        return CheckResult(passed=True, reason="python_assert exited 0")

    output = (result.stdout or "") + (result.stderr or "")
    return CheckResult(passed=False, reason=f"python_assert exited {result.exit_code}: {output.strip()[:500]}")


def _shell_quote(s: str) -> str:
    import shlex

    return shlex.quote(s)


JUDGE_SYSTEM_PROMPT = (
    "You are an impartial evaluator judging whether an AI assistant's answer "
    "to a user's question satisfies a rubric. You are given ONLY the rubric, "
    "the user's question, and the assistant's final answer -- you do not see "
    "how the assistant arrived at the answer. "
    "Respond with strict JSON: {\"pass\": true|false, \"reason\": \"...\"}. "
    "The reason should be one short sentence explaining your verdict."
)


def _last_user_question(history: list[dict]) -> str:
    for message in reversed(history or []):
        if message.get("role") == "user":
            return message.get("content") or ""
    return ""


async def check_llm_judge(
    final_answer: str,
    check_spec: dict,
    *,
    sandbox: Optional[Sandbox] = None,
    llm: Optional[LLM] = None,
    judge_model: Optional[str] = None,
    history: Optional[list[dict]] = None,
) -> CheckResult:
    """`{"rubric": "..."}` -- judge model (temperature 0, JSON mode) sees
    ONLY rubric + user question + final answer, never the trace.
    """
    if llm is None:
        return CheckResult(passed=False, reason="llm_judge check requires a judge LLM")

    rubric = check_spec.get("rubric")
    if not isinstance(rubric, str) or not rubric:
        return CheckResult(passed=False, reason="llm_judge check_spec missing non-empty 'rubric'")

    question = _last_user_question(history or [])

    user_content = (
        f"Rubric:\n{rubric}\n\n"
        f"User question:\n{question}\n\n"
        f"Assistant's final answer:\n{final_answer or ''}\n\n"
        "Does the final answer satisfy the rubric? Respond with the JSON object only."
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]

    try:
        response = await llm.chat(
            messages=messages,
            tools=None,
            model=judge_model,
            temperature=0,
            response_format={"type": "json_object"},
        )
    except Exception as e:  # EV-8
        return CheckResult(passed=False, reason=f"llm_judge call errored: {e}")

    try:
        parsed: Any = json.loads(response.content or "")
        passed = bool(parsed["pass"])
        reason = str(parsed.get("reason", ""))
    except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
        return CheckResult(passed=False, reason=f"llm_judge returned invalid JSON: {e}")

    return CheckResult(passed=passed, reason=reason or ("judge passed" if passed else "judge failed"))


CHECK_DISPATCH = {
    "contains": check_contains,
    "python_assert": check_python_assert,
    "llm_judge": check_llm_judge,
}


async def run_check(
    check_type: str,
    final_answer: str,
    check_spec: dict,
    *,
    sandbox: Optional[Sandbox] = None,
    llm: Optional[LLM] = None,
    judge_model: Optional[str] = None,
    history: Optional[list[dict]] = None,
) -> CheckResult:
    """Dispatch to the named check type. EV-8: any unexpected exception from
    a check implementation is still caught here as a final backstop so the
    eval executor never has to handle check-level exceptions itself.
    """
    handler = CHECK_DISPATCH.get(check_type)
    if handler is None:
        return CheckResult(passed=False, reason=f"unknown check_type: {check_type!r}")
    try:
        if check_type == "llm_judge":
            return await handler(
                final_answer, check_spec, sandbox=sandbox, llm=llm, judge_model=judge_model, history=history
            )
        return await handler(final_answer, check_spec, sandbox=sandbox, llm=llm, judge_model=judge_model)
    except Exception as e:  # EV-8 backstop
        return CheckResult(passed=False, reason=f"check {check_type!r} errored: {e}")

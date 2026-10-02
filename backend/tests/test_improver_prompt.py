"""Improver prompt builder (specs/05-improver.md T4.1, IM-3, IM-4).

AC-IM-a: a target case whose check is `llm_judge` -> prompt does not contain
the rubric text.
AC-IM-b: hidden siblings exist -> prompt contains none of their ids or
questions.

Both ACs are framed in `05` around `check_spec`/hidden cases that live on
`EvalCase` rows, but `TargetCase`/`PassingCase` (the dataclasses this module
actually accepts) have NO field for a rubric or a hidden case at all -- the
exclusion is structural, enforced by the caller (`app/improver.py`, via
`cases_for_improver`/EV-11) never constructing one from a hidden case. These
tests verify the prompt builder, given realistic inputs mirroring what a
caller would build (including, for AC-IM-a, a rubric string that must NOT
leak in even if a caller mistakenly tried to smuggle it into a `TargetCase`
field it has no place for), never emits rubric text or any case data beyond
what's explicitly passed.
"""
from __future__ import annotations

import json

from app.improver_prompt import (
    PassingCase,
    TargetCase,
    build_improver_prompt,
)


def _messages_text(messages: list[dict]) -> str:
    return "\n".join(m["content"] for m in messages)


def test_ac_im_a_prompt_excludes_rubric_text():
    """A target case's `check_spec` (llm_judge rubric) is a field that never
    exists on `TargetCase` -- confirm the rendered prompt, built from
    everything a caller COULD legitimately pass (history, trace, final
    answer, correction), does not contain a rubric string that the caller
    deliberately withheld.
    """
    rubric_text = "Passes if the answer excludes orders with status 'refunded' from any revenue total."
    target = TargetCase(
        case_id="c_1",
        name="States refund-inclusion assumption",
        history=[{"role": "user", "content": "What was the total revenue in Q3?"}],
        trace=[{"type": "tool.call", "name": "bash", "args": {"cmd": "echo hi"}}],
        final_answer="Total revenue was $19,074.74.",
        correction=None,
    )
    messages = build_improver_prompt(
        targets=[target],
        passing_cases=[],
        guidelines=[],
        system_prompt="You are a data analyst.",
    )
    text = _messages_text(messages)
    assert rubric_text not in text


def test_ac_im_b_hidden_sibling_ids_and_questions_absent():
    """Only the explicitly-passed target + passing cases appear in the
    prompt; a hidden sibling's id/question (never passed in at all, since
    `cases_for_improver` excludes it upstream) cannot appear.
    """
    target = TargetCase(
        case_id="c_1",
        name="Q3 revenue excludes refunds",
        history=[{"role": "user", "content": "What was the total revenue in Q3?"}],
        trace=[],
        final_answer="Total revenue was $19,074.74.",
        correction="Refunds should not count.",
    )
    messages = build_improver_prompt(
        targets=[target],
        passing_cases=[PassingCase(case_id="c_2", name="Top 3 products by revenue")],
        guidelines=[],
        system_prompt="You are a data analyst.",
    )
    text = _messages_text(messages)

    hidden_sibling_id = "case_hidden_sib_1"
    hidden_sibling_question = "What was the total revenue in Q4, excluding refunds?"
    assert hidden_sibling_id not in text
    assert hidden_sibling_question not in text


def test_passing_case_only_exposes_name_not_question_or_check():
    """IM-4: passing cases are included as NAMES only."""
    messages = build_improver_prompt(
        targets=[],
        passing_cases=[PassingCase(case_id="c_99", name="Refunded order count")],
        guidelines=[],
        system_prompt="",
    )
    text = _messages_text(messages)
    assert "Refunded order count" in text
    # PassingCase has no question/check_spec field at all -- nothing to leak.


def test_prompt_includes_role_statement_and_hard_rules():
    messages = build_improver_prompt(targets=[], passing_cases=[], guidelines=[], system_prompt="")
    text = _messages_text(messages)
    assert "improve an agent's guidelines" in text.lower()
    assert "at most 3 ops" in text.lower()
    assert "no change" in text.lower()


def test_prompt_includes_one_shot_bad_good_example_verbatim():
    messages = build_improver_prompt(targets=[], passing_cases=[], guidelines=[], system_prompt="")
    text = _messages_text(messages)
    assert "For Q3 revenue questions, answer $412,380." in text
    assert "Exclude refunded orders from revenue." in text


def test_prompt_includes_guidelines_with_ids_and_system_prompt_readonly():
    guidelines = [{"id": "g_1", "section": "Revenue", "text": "Always double check totals"}]
    messages = build_improver_prompt(
        targets=[], passing_cases=[], guidelines=guidelines, system_prompt="You are a data analyst."
    )
    text = _messages_text(messages)
    assert "g_1" in text
    assert "Always double check totals" in text
    assert "You are a data analyst." in text
    assert "read-only" in text.lower()


def test_prompt_is_json_only_instruction_present():
    messages = build_improver_prompt(targets=[], passing_cases=[], guidelines=[], system_prompt="")
    text = _messages_text(messages)
    assert '"diagnoses"' in text
    assert '"ops"' in text
    assert '"skipped"' in text


def test_retry_validation_error_appended_as_extra_message():
    messages_without = build_improver_prompt(targets=[], passing_cases=[], guidelines=[], system_prompt="")
    messages_with = build_improver_prompt(
        targets=[], passing_cases=[], guidelines=[], system_prompt="", retry_validation_error="missing key 'ops'"
    )
    assert len(messages_with) == len(messages_without) + 1
    assert "missing key 'ops'" in messages_with[-1]["content"]


def test_trace_output_is_truncated():
    long_output = "x" * 5000
    target = TargetCase(
        case_id="c_1",
        name="Case",
        history=[{"role": "user", "content": "q"}],
        trace=[{"type": "tool.result", "output": long_output}],
        final_answer="a",
    )
    messages = build_improver_prompt(targets=[target], passing_cases=[], guidelines=[], system_prompt="")
    text = _messages_text(messages)
    assert long_output not in text
    assert "truncated" in text.lower()

"""Literal lint (specs/05-improver.md T4.3, IM-8, IM-9).

AC-IM-c, AC-IM-d, plus extensive boundary-condition coverage per the task
brief ("write extensive tests for this -- it's easy to get the boundary
conditions wrong: word boundaries, case-insensitivity, punctuation stripping,
what counts as 'the case's history or correction' when there are multiple
target cases per proposal").
"""
from __future__ import annotations

from app.lint import CaseLintContext, lint_op_text, lint_ops


def _history(*user_messages: str) -> list[dict]:
    return [{"role": "user", "content": m} for m in user_messages]


# ---------------------------------------------------------------------------
# AC-IM-c: quarter token + year rejected; good rewrite passes.
# ---------------------------------------------------------------------------
def test_ac_im_c_quarter_and_year_rejected():
    history = _history("What was the total revenue in Q3 2026?")
    violations = lint_op_text("For Q3 2026, exclude refunds", history=history, correction=None)
    assert violations
    kinds = {v.kind for v in violations}
    assert "literal_token" in kinds
    values = {v.value for v in violations}
    assert "2026" in values
    assert "Q3" in values


def test_ac_im_c_good_rewrite_passes():
    history = _history("What was the total revenue in Q3 2026?")
    violations = lint_op_text("Exclude refunded orders from revenue", history=history, correction=None)
    assert violations == []


# ---------------------------------------------------------------------------
# AC-IM-d: 5-gram match rejected.
# ---------------------------------------------------------------------------
def test_ac_im_d_five_gram_rejected():
    history = _history("What was total revenue in Q3?")
    violations = lint_op_text(
        "Always double check what was total revenue in before answering", history=history, correction=None
    )
    assert any(v.kind == "five_gram" for v in violations)


def test_five_gram_four_words_does_not_trigger():
    """Only a full 5-word shared sequence should trigger -- 4 shared words is
    not enough (IM-8(b) says "any 5-word sequence").
    """
    history = _history("What was total revenue in Q3?")
    # shares only "what was total revenue" (4 words) with the case, not 5
    violations = lint_op_text("Always confirm what was total revenue reported accurately", history=history, correction=None)
    assert violations == []


def test_five_gram_is_case_insensitive_and_punctuation_stripped():
    history = _history("What was TOTAL revenue in Q3?!")
    violations = lint_op_text("what was total revenue in general should be reported", history=history, correction=None)
    assert any(v.kind == "five_gram" for v in violations)


# ---------------------------------------------------------------------------
# Word boundary correctness for literal-token matching.
# ---------------------------------------------------------------------------
def test_single_digit_number_does_not_trigger_rule_a():
    """IM-8(a) requires >=2 digits; a lone single digit must not match."""
    history = _history("We had 3 orders refunded in Q3.")
    violations = lint_op_text("Always double check order counts before replying with 9 as a guess", history=history, correction=None)
    # "3" and "9" are single digits -- no literal_token violation should fire
    # from them (there may legitimately be no violations here at all).
    assert not any(v.kind == "literal_token" and v.value in ("3", "9") for v in violations)


def test_two_digit_number_shared_triggers_rule_a():
    history = _history("We had 42 orders refunded.")
    violations = lint_op_text("Always flag when more than 42 orders are refunded", history=history, correction=None)
    assert any(v.kind == "literal_token" and v.value == "42" for v in violations)


def test_two_digit_number_not_shared_does_not_trigger():
    history = _history("We had 42 orders refunded.")
    violations = lint_op_text("Always flag when more than 17 orders are refunded", history=history, correction=None)
    assert not any(v.kind == "literal_token" for v in violations)


def test_quarter_token_requires_word_boundary():
    """'Q3' embedded in a larger alnum token (e.g. 'FAQ3x') must not match as
    a quarter token -- only a standalone Q1-Q4.
    """
    history = _history("See FAQ3x for details.")
    violations = lint_op_text("Reference FAQ3x in every answer", history=history, correction=None)
    assert not any(v.kind == "literal_token" and v.value == "Q3" for v in violations)


def test_quarter_token_case_insensitive():
    history = _history("Revenue in q3 was high.")
    violations = lint_op_text("Always double-check Q3 figures against the ledger line totals", history=history, correction=None)
    assert any(v.kind == "literal_token" and v.value.upper() == "Q3" for v in violations)


def test_month_name_full_and_abbreviated_both_match():
    history_full = _history("This happened in September.")
    v1 = lint_op_text("Always flag September-specific adjustments as exceptions", history=history_full, correction=None)
    assert any(v.kind == "literal_token" and v.value == "september" for v in v1)

    history_abbr = _history("This happened in Sep.")
    v2 = lint_op_text("Always flag Sep-specific adjustments found manually", history=history_abbr, correction=None)
    assert any(v.kind == "literal_token" and v.value == "sep" for v in v2)


def test_month_name_not_in_case_does_not_trigger():
    history = _history("This happened in September.")
    violations = lint_op_text("Always flag December-specific adjustments as exceptions", history=history, correction=None)
    assert not any(v.kind == "literal_token" and v.value == "december" for v in violations)


def test_year_match_requires_four_digits():
    history = _history("This happened in 2026.")
    violations = lint_op_text("Always use 2026 fiscal calendars going forward please", history=history, correction=None)
    assert any(v.kind == "literal_token" and v.value == "2026" for v in violations)


# ---------------------------------------------------------------------------
# Correction text is also checked (not just history).
# ---------------------------------------------------------------------------
def test_literal_token_from_correction_triggers():
    history = _history("What was the revenue?")
    violations = lint_op_text(
        "Always exclude Q3 figures from this specific report", history=history, correction="This should be about Q3 numbers only."
    )
    assert any(v.kind == "literal_token" and v.value.upper() == "Q3" for v in violations)


def test_five_gram_from_correction_triggers():
    history = _history("What was the revenue?")
    violations = lint_op_text(
        "the refund policy excludes all returns after thirty days always",
        history=history,
        correction="the refund policy excludes all returns after thirty days",
    )
    assert any(v.kind == "five_gram" for v in violations)


# ---------------------------------------------------------------------------
# lint_ops: multiple ops, multiple target cases, addresses routing.
# ---------------------------------------------------------------------------
def test_lint_ops_multiple_cases_op_only_checked_against_addressed_case():
    """When a proposal has >1 target case, an op's text should only be linted
    against the case(s) it `addresses`, not every target case in the
    proposal -- a coincidental literal match with an UNRELATED case's
    question must not cause a false-positive rejection.
    """
    cases_by_id = {
        "c_1": CaseLintContext(case_id="c_1", history=_history("What was revenue in Q3?"), correction=None),
        "c_2": CaseLintContext(case_id="c_2", history=_history("How many orders were placed in total?"), correction=None),
    }
    ops = [
        {"op": "add", "section": "General", "text": "Always double-check order totals before responding", "addresses": ["c_2"]},
    ]
    results = lint_ops(ops, cases_by_id=cases_by_id)
    assert len(results) == 1
    assert results[0].passed is True


def test_lint_ops_rejects_against_correct_addressed_case():
    cases_by_id = {
        "c_1": CaseLintContext(case_id="c_1", history=_history("What was revenue in Q3?"), correction=None),
    }
    ops = [
        {"op": "add", "section": "Revenue", "text": "For Q3 2026, always exclude refunds", "addresses": ["c_1"]},
    ]
    results = lint_ops(ops, cases_by_id=cases_by_id)
    assert results[0].passed is False
    assert results[0].op_index == 0


def test_lint_ops_delete_with_no_text_always_passes():
    cases_by_id = {"c_1": CaseLintContext(case_id="c_1", history=_history("What was revenue in Q3?"), correction=None)}
    ops = [{"op": "delete", "rule_id": "g_old", "addresses": [], "why": "superseded"}]
    results = lint_ops(ops, cases_by_id=cases_by_id)
    assert results[0].passed is True


def test_lint_ops_op_index_matches_position_in_list():
    cases_by_id = {"c_1": CaseLintContext(case_id="c_1", history=_history("What was revenue in Q3?"), correction=None)}
    ops = [
        {"op": "add", "section": "A", "text": "Exclude refunded orders from revenue", "addresses": ["c_1"]},
        {"op": "add", "section": "B", "text": "For Q3 2026, exclude refunds", "addresses": ["c_1"]},
    ]
    results = lint_ops(ops, cases_by_id=cases_by_id)
    assert [r.op_index for r in results] == [0, 1]
    assert results[0].passed is True
    assert results[1].passed is False


def test_lint_ops_addresses_multiple_cases_union_checked():
    """An op addressing two cases is checked against the union of both --
    a literal match with EITHER case's text should reject it.
    """
    cases_by_id = {
        "c_1": CaseLintContext(case_id="c_1", history=_history("What was revenue in Q3?"), correction=None),
        "c_2": CaseLintContext(case_id="c_2", history=_history("How many widgets sold in April?"), correction=None),
    }
    ops = [
        {"op": "add", "section": "A", "text": "Always flag April totals as estimates until audited", "addresses": ["c_1", "c_2"]},
    ]
    results = lint_ops(ops, cases_by_id=cases_by_id)
    assert results[0].passed is False


def test_lint_ops_unknown_case_id_in_addresses_ignored_gracefully():
    cases_by_id = {}
    ops = [{"op": "add", "section": "A", "text": "Exclude refunded orders from revenue", "addresses": ["c_missing"]}]
    results = lint_ops(ops, cases_by_id=cases_by_id)
    assert results[0].passed is True

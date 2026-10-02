"""Ops application + budget (specs/05-improver.md T4.2, IM-7, IM-10, IM-15).

AC-IM-e: given 5 ops returned, 3 applied and 2 recorded as dropped.
"""
from __future__ import annotations

from app.ops import GUIDELINES_CHAR_BUDGET, apply_ops, cap_ops, exceeds_budget, render_guidelines_text


def _guideline(id_, text="some text", section="General", addresses=None):
    return {"id": id_, "section": section, "text": text, "addresses": addresses or []}


# ---------------------------------------------------------------------------
# AC-IM-e / IM-10: cap at 3 ops.
# ---------------------------------------------------------------------------
def test_ac_im_e_five_ops_three_applied_two_dropped():
    ops = [
        {"op": "add", "section": "A", "text": f"rule {i}", "addresses": [f"c_{i}"]} for i in range(5)
    ]
    capped, dropped = cap_ops(ops)
    assert len(capped) == 3
    assert len(dropped) == 2
    assert [d.op_index for d in dropped] == [3, 4]
    assert all(d.reason == "max_ops_exceeded" for d in dropped)


def test_cap_ops_three_or_fewer_untouched():
    ops = [{"op": "add", "section": "A", "text": "x", "addresses": []} for _ in range(3)]
    capped, dropped = cap_ops(ops)
    assert len(capped) == 3
    assert dropped == []


# ---------------------------------------------------------------------------
# IM-7: add / replace / delete semantics.
# ---------------------------------------------------------------------------
def test_apply_add_creates_new_guideline_with_new_id():
    result = apply_ops([], [{"op": "add", "section": "Revenue", "text": "Exclude refunds", "addresses": ["c_1"]}])
    assert len(result.guidelines) == 1
    g = result.guidelines[0]
    assert g["text"] == "Exclude refunds"
    assert g["section"] == "Revenue"
    assert g["id"]  # new id assigned
    assert result.applied_indices == [0]
    assert result.dropped == []


def test_apply_replace_by_rule_id_updates_text_in_place():
    base = [_guideline("g_1", text="old text")]
    result = apply_ops(base, [{"op": "replace", "rule_id": "g_1", "text": "new text", "addresses": ["c_2"]}])
    assert len(result.guidelines) == 1
    assert result.guidelines[0]["id"] == "g_1"  # id stable across replace
    assert result.guidelines[0]["text"] == "new text"
    assert result.applied_indices == [0]


def test_apply_replace_missing_rule_id_is_dropped_and_recorded():
    base = [_guideline("g_1")]
    result = apply_ops(base, [{"op": "replace", "rule_id": "g_nonexistent", "text": "new text", "addresses": []}])
    assert result.guidelines == base
    assert result.applied_indices == []
    assert len(result.dropped) == 1
    assert result.dropped[0].reason == "rule_id_not_found"


def test_apply_delete_by_rule_id_removes_guideline():
    base = [_guideline("g_1"), _guideline("g_2")]
    result = apply_ops(base, [{"op": "delete", "rule_id": "g_1", "addresses": []}])
    assert [g["id"] for g in result.guidelines] == ["g_2"]
    assert result.applied_indices == [0]


def test_apply_delete_missing_rule_id_is_dropped_and_recorded():
    base = [_guideline("g_1")]
    result = apply_ops(base, [{"op": "delete", "rule_id": "g_missing", "addresses": []}])
    assert result.guidelines == base
    assert len(result.dropped) == 1
    assert result.dropped[0].reason == "rule_id_not_found"


def test_apply_ops_in_order_replace_then_delete_same_id():
    """IM-7 says ops are applied IN ORDER -- a replace followed by a delete
    on the same rule_id should result in deletion (later op wins).
    """
    base = [_guideline("g_1", text="old")]
    ops = [
        {"op": "replace", "rule_id": "g_1", "text": "updated", "addresses": []},
        {"op": "delete", "rule_id": "g_1", "addresses": []},
    ]
    result = apply_ops(base, ops)
    assert result.guidelines == []
    assert result.applied_indices == [0, 1]


# ---------------------------------------------------------------------------
# IM-15: addresses recorded on the resulting guideline.
# ---------------------------------------------------------------------------
def test_add_records_addresses_on_new_guideline():
    result = apply_ops([], [{"op": "add", "section": "A", "text": "x", "addresses": ["c_1", "c_2"]}])
    assert result.guidelines[0]["addresses"] == ["c_1", "c_2"]


def test_replace_records_new_addresses_on_existing_guideline():
    base = [_guideline("g_1", addresses=["c_old"])]
    result = apply_ops(base, [{"op": "replace", "rule_id": "g_1", "text": "new", "addresses": ["c_new"]}])
    assert result.guidelines[0]["addresses"] == ["c_new"]


# ---------------------------------------------------------------------------
# IM-10: size budget.
# ---------------------------------------------------------------------------
def test_render_guidelines_text_groups_by_section():
    guidelines = [_guideline("g_1", text="rule one", section="A"), _guideline("g_2", text="rule two", section="B")]
    text = render_guidelines_text(guidelines)
    assert "### A" in text
    assert "### B" in text
    assert "rule one" in text
    assert "rule two" in text


def test_exceeds_budget_false_under_limit():
    guidelines = [_guideline("g_1", text="short rule")]
    assert exceeds_budget(guidelines) is False


def test_exceeds_budget_true_over_limit():
    long_text = "x" * (GUIDELINES_CHAR_BUDGET + 100)
    guidelines = [_guideline("g_1", text=long_text)]
    assert exceeds_budget(guidelines) is True


def test_op_index_offset_renumbers_dropped_ops():
    base = [_guideline("g_1")]
    result = apply_ops(
        base, [{"op": "replace", "rule_id": "g_missing", "text": "x", "addresses": []}], op_index_offset=10
    )
    assert result.dropped[0].op_index == 10

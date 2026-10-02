"""Ops application + size budget (specs/05-improver.md T4.2, IM-7, IM-10, IM-15).

Pure functions over plain data (a guidelines list + an ops list) -- no DB/IO,
mirroring `app/verdict.py`'s style so this is trivially unit-testable.

Pipeline position: step 3 ("Apply") in specs/05's pipeline, called AFTER the
improver's raw ops have already been through the first stage of IM-10 (the
"more than 3 ops -> keep first 3" truncation happens in `cap_ops` here) and
BEFORE step 4 ("Lint", `app/lint.py`) drops any ops whose `text` is a literal
copy of the case.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from app.ids import new_id

MAX_OPS = 3  # IM-10
GUIDELINES_CHAR_BUDGET = 6000  # IM-10: ~1,500 tokens


@dataclass
class DroppedOp:
    op_index: int
    op: dict
    reason: str  # "max_ops_exceeded" | "rule_id_not_found" | "lint" | "budget"


@dataclass
class ApplyResult:
    guidelines: list[dict]
    applied_indices: list[int] = field(default_factory=list)
    dropped: list[DroppedOp] = field(default_factory=list)
    budget_exceeded: bool = False


def cap_ops(ops: list[dict]) -> tuple[list[dict], list[DroppedOp]]:
    """IM-10: if more than 3 ops are returned, keep the first 3 and record the
    rest as dropped (with their ORIGINAL index into the full `ops` list, so
    `lint`/`addresses`/UI reporting can still refer to "op 4", "op 5", ...).
    """
    kept = ops[:MAX_OPS]
    dropped = [
        DroppedOp(op_index=i, op=op, reason="max_ops_exceeded")
        for i, op in enumerate(ops)
        if i >= MAX_OPS
    ]
    return kept, dropped


def render_guidelines_text(guidelines: list[dict]) -> str:
    """The "rendered guidelines" whose length IM-10's 6,000-char budget
    applies to. Mirrors `app/runner.py::_build_system_message`'s rendering
    (grouped by section) closely enough to be a faithful proxy for "how big
    will this make the system prompt" without importing runner internals.
    """
    if not guidelines:
        return ""
    by_section: dict[str, list[str]] = {}
    for g in guidelines:
        section = g.get("section", "General")
        by_section.setdefault(section, []).append(g.get("text", ""))
    lines = []
    for section in sorted(by_section):
        lines.append(f"### {section}")
        for text in by_section[section]:
            lines.append(f"- {text}")
    return "\n".join(lines)


def apply_ops(
    guidelines: list[dict],
    ops: list[dict],
    *,
    op_index_offset: int = 0,
) -> ApplyResult:
    """IM-7: apply ops in order -- `add` (new id), `replace` (by `rule_id`),
    `delete` (by `rule_id`). If a `rule_id` doesn't exist for `replace`/
    `delete`, that op is dropped (recorded, not applied); `add` always
    succeeds (it never references an existing id).

    IM-15: each applied op's `addresses` list is recorded on the resulting
    guideline (both for a brand new `add`-ed guideline, and for a `replace`,
    where the guideline's `addresses` is replaced with the new op's
    `addresses` -- it describes why the CURRENT text is there).

    `op_index_offset` lets a caller renumber ops that were already sliced
    (e.g. lint-surviving ops starting partway through the original list) so
    `DroppedOp.op_index` always refers to the position in the ORIGINAL
    improver output, not a re-sliced sublist.

    Does not itself enforce IM-10's budget check (`exceeds_budget` does that
    separately) or run the literal lint (`app/lint.py` does that, typically
    BEFORE this function is called with only the surviving ops) -- this
    function only implements "what does apply mean", kept small and pure so
    budget/lint policy can be composed around it independently.
    """
    result_guidelines = [dict(g) for g in guidelines]
    by_id = {g["id"]: g for g in result_guidelines}

    applied_indices: list[int] = []
    dropped: list[DroppedOp] = []

    for i, op in enumerate(ops):
        real_index = i + op_index_offset
        op_type = op.get("op")
        addresses = op.get("addresses") or []

        if op_type == "add":
            new_guideline = {
                "id": new_id("g"),
                "section": op.get("section", "General"),
                "text": op.get("text", ""),
                "addresses": addresses,
            }
            result_guidelines.append(new_guideline)
            by_id[new_guideline["id"]] = new_guideline
            applied_indices.append(real_index)

        elif op_type == "replace":
            rule_id = op.get("rule_id")
            existing = by_id.get(rule_id)
            if existing is None:
                dropped.append(DroppedOp(op_index=real_index, op=op, reason="rule_id_not_found"))
                continue
            existing["text"] = op.get("text", existing.get("text", ""))
            if "section" in op:
                existing["section"] = op["section"]
            existing["addresses"] = addresses
            applied_indices.append(real_index)

        elif op_type == "delete":
            rule_id = op.get("rule_id")
            existing = by_id.get(rule_id)
            if existing is None:
                dropped.append(DroppedOp(op_index=real_index, op=op, reason="rule_id_not_found"))
                continue
            result_guidelines = [g for g in result_guidelines if g["id"] != rule_id]
            by_id.pop(rule_id, None)
            applied_indices.append(real_index)

        else:
            dropped.append(DroppedOp(op_index=real_index, op=op, reason="unknown_op_type"))

    return ApplyResult(guidelines=result_guidelines, applied_indices=applied_indices, dropped=dropped)


def exceeds_budget(guidelines: list[dict]) -> bool:
    """IM-10: rendered guidelines > 6,000 chars -> budget failure."""
    return len(render_guidelines_text(guidelines)) > GUIDELINES_CHAR_BUDGET

"""The verdict pure function (specs/04-feedback-and-evals.md "Verdict (pure
function, fully unit-tested)", EV-9).

No DB/IO -- `compute_verdict` takes plain data (per-case pass booleans for
base and candidate, case metadata, policy) and returns the verdict dict
stored on a proposal. Fully covered by `tests/test_verdict.py`'s
parametrized 5-row acceptance table (AC-EV-c).

Inputs:
  base_results / cand_results: {case_id: bool}  -- whether that case passed
    (per EV-6: passing trials >= pass_threshold) on the base / candidate
    version. A case_id missing from a results dict is NOT visible for that
    side's diffing (callers should pass every case's result for both sides
    if it should actually be compared) -- see `n_visible` note below.
  cases: list of case-metadata dicts, each with at least
    {id, name, axis, pinned, hidden}. Visible = hidden == False.
  policy: dict with `min_avg_improvement_pct` (float), `max_regressions`
    (dict axis->int, missing axis defaults to 0 per AC-EV-c row 5).
"""
from __future__ import annotations

from typing import Any


def compute_verdict(
    base_results: dict[str, bool],
    cand_results: dict[str, bool],
    cases: list[dict],
    policy: dict,
) -> dict[str, Any]:
    visible_cases = [c for c in cases if not c.get("hidden", False)]
    hidden_cases = [c for c in cases if c.get("hidden", False)]

    n_visible = len(visible_cases)

    fixed: list[dict] = []
    regressed: list[dict] = []
    unchanged: list[dict] = []

    base_pass_count = 0
    cand_pass_count = 0

    for case in visible_cases:
        case_id = case["id"]
        base_passed = bool(base_results.get(case_id, False))
        cand_passed = bool(cand_results.get(case_id, False))

        if base_passed:
            base_pass_count += 1
        if cand_passed:
            cand_pass_count += 1

        case_summary = {"id": case_id, "name": case.get("name", ""), "axis": case.get("axis", ""), "pinned": case.get("pinned", False)}

        if not base_passed and cand_passed:
            fixed.append(case_summary)
        elif base_passed and not cand_passed:
            regressed.append(case_summary)
        else:
            unchanged.append(case_summary)

    base_score = (base_pass_count / n_visible * 100) if n_visible else 0.0
    cand_score = (cand_pass_count / n_visible * 100) if n_visible else 0.0
    avg_delta = ((cand_pass_count - base_pass_count) / n_visible * 100) if n_visible else 0.0

    by_axis: dict[str, int] = {}
    for case_summary in regressed:
        axis = case_summary["axis"]
        by_axis[axis] = by_axis.get(axis, 0) + 1

    max_regressions = policy.get("max_regressions", {}) or {}
    axis_breach: dict[str, int] = {}
    for axis, n in by_axis.items():
        limit = max_regressions.get(axis, 0)
        if n > limit:
            axis_breach[axis] = n

    pinned_regressed = [c for c in regressed if c.get("pinned", False)]

    min_avg_improvement_pct = policy.get("min_avg_improvement_pct", 0.0)
    meets_min_improvement = avg_delta >= min_avg_improvement_pct
    meets_policy = meets_min_improvement and not axis_breach and not pinned_regressed

    reasons: list[str] = []
    if not meets_min_improvement:
        reasons.append(
            f"avg_delta ({avg_delta:.1f} pts) is below the minimum improvement "
            f"required ({min_avg_improvement_pct:.1f} pts)"
        )
    if axis_breach:
        for axis, n in axis_breach.items():
            limit = max_regressions.get(axis, 0)
            reasons.append(f"axis {axis!r} regressed {n} case(s), exceeding the policy limit of {limit}")
    if pinned_regressed:
        for case_summary in pinned_regressed:
            reasons.append(f"pinned case {case_summary['name']!r} regressed")

    n_hidden = len(hidden_cases)
    hidden_base_pass = sum(1 for c in hidden_cases if base_results.get(c["id"], False))
    hidden_cand_pass = sum(1 for c in hidden_cases if cand_results.get(c["id"], False))
    generalization = {"base": hidden_base_pass, "cand": hidden_cand_pass, "total": n_hidden}

    return {
        "base_score": base_score,
        "cand_score": cand_score,
        "n_visible": n_visible,
        "fixed": fixed,
        "regressed": regressed,
        "unchanged": unchanged,
        "avg_delta": avg_delta,
        "by_axis": by_axis,
        "axis_breach": axis_breach,
        "pinned_regressed": pinned_regressed,
        "meets_policy": meets_policy,
        "reasons": reasons,
        "generalization": generalization,
    }

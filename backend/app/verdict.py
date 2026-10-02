"""The verdict pure function (v2 cherry-pick: specs/specs-v2/specs/04-feedback-and-evals.md
"Verdict (pure function, fully unit-tested)", EV-9).

No DB/IO -- `compute_verdict` takes plain data (per-case pass booleans for
base and candidate, case metadata, per-run average cost, a single
`target_axis`, and policy) and returns the verdict dict stored on a
proposal. Fully covered by `tests/test_verdict.py`'s parametrized 8-row
acceptance table (AC-EV-c).

Inputs:
  base_results / cand_results: {case_id: bool} -- whether that case passed
    (per EV-6: passing trials >= pass_threshold) on the base / candidate
    version. A case_id missing from a results dict is NOT visible for that
    side's diffing (callers should pass every case's result for both sides
    if it should actually be compared).
  cases: list of case-metadata dicts, each with at least
    {id, name, axis, pinned, split}. split is "improve" or "benchmark" (v2
    cherry-pick: replaces v1's boolean `hidden`). ALL active cases (both
    splits) participate in fixed/regressed/axis_breach, per spec; only
    `split == "benchmark"` cases on the target axis feed bench_target_delta.
  base_avg_cost / cand_avg_cost: average per-run cost in USD for this
    version's eval run, or None if not measured (no cost metering exists in
    this phase -- always None from real callers; accepted as a parameter
    here so the pure function is fully testable without that infra ever
    existing).
  target_axis: the ONE axis (D-34) this proposal is being judged on.
  policy: dict with `min_target_gain_pct` (float), `max_regressions` (dict
    axis->int, missing axis defaults to 0), `axis_floors` (dict axis->float
    0-100), `max_cost_increase_pct` (float).

Output JSON shape (per spec):
  {target_axis, target_gain, bench_target_delta, overall: {base, cand},
   by_axis: {...}, by_split: {...}, fixed, regressed, axis_breach,
   floor_breach, pinned_regressed, cost: {base, cand, delta_pct},
   meets_policy, reasons, warnings}
"""
from __future__ import annotations

from typing import Any, Optional


def _rate(cases: list[dict], results: dict[str, bool]) -> Optional[float]:
    """rate(set, v) = passes / |set| * 100; None if the set is empty
    (undefined, per spec -- callers must handle None, not treat it as 0)."""
    if not cases:
        return None
    passes = sum(1 for c in cases if results.get(c["id"], False))
    return passes / len(cases) * 100


def compute_verdict(
    base_results: dict[str, bool],
    cand_results: dict[str, bool],
    cases: list[dict],
    policy: dict,
    *,
    target_axis: str = "accuracy",
    base_avg_cost: Optional[float] = None,
    cand_avg_cost: Optional[float] = None,
) -> dict[str, Any]:
    all_cases = list(cases)  # "all = active cases (both splits)"

    # --- target axis + benchmark subset -----------------------------------
    T = [c for c in all_cases if c.get("axis") == target_axis]
    T_bench = [c for c in T if c.get("split") == "benchmark"]

    base_T_rate = _rate(T, base_results)
    cand_T_rate = _rate(T, cand_results)
    target_gain = (cand_T_rate or 0.0) - (base_T_rate or 0.0) if T else 0.0

    bench_target_delta: Optional[float] = None
    if T_bench:
        base_bench_rate = _rate(T_bench, base_results)
        cand_bench_rate = _rate(T_bench, cand_results)
        bench_target_delta = (cand_bench_rate or 0.0) - (base_bench_rate or 0.0)

    # --- fixed/regressed over ALL active cases (both splits), as in v1 ----
    fixed: list[dict] = []
    regressed: list[dict] = []
    unchanged: list[dict] = []

    base_pass_count = 0
    cand_pass_count = 0

    for case in all_cases:
        case_id = case["id"]
        base_passed = bool(base_results.get(case_id, False))
        cand_passed = bool(cand_results.get(case_id, False))

        if base_passed:
            base_pass_count += 1
        if cand_passed:
            cand_pass_count += 1

        case_summary = {
            "id": case_id,
            "name": case.get("name", ""),
            "axis": case.get("axis", ""),
            "pinned": case.get("pinned", False),
        }

        if not base_passed and cand_passed:
            fixed.append(case_summary)
        elif base_passed and not cand_passed:
            regressed.append(case_summary)
        else:
            unchanged.append(case_summary)

    n_all = len(all_cases)
    overall_base = (base_pass_count / n_all * 100) if n_all else 0.0
    overall_cand = (cand_pass_count / n_all * 100) if n_all else 0.0

    # --- per-axis regressions -> axis_breach (ALL axes, not just target) --
    by_axis_regressions: dict[str, int] = {}
    for case_summary in regressed:
        axis = case_summary["axis"]
        by_axis_regressions[axis] = by_axis_regressions.get(axis, 0) + 1

    max_regressions = policy.get("max_regressions", {}) or {}
    axis_breach: dict[str, int] = {}
    for axis, n in by_axis_regressions.items():
        limit = max_regressions.get(axis, 0)
        if n > limit:
            axis_breach[axis] = n

    # --- floor_breach: candidate's pass rate on each floored axis --------
    axis_floors = policy.get("axis_floors", {}) or {}
    floor_breach: dict[str, float] = {}
    for axis, floor in axis_floors.items():
        axis_cases = [c for c in all_cases if c.get("axis") == axis]
        cand_axis_rate = _rate(axis_cases, cand_results)
        if cand_axis_rate is not None and cand_axis_rate < floor:
            floor_breach[axis] = cand_axis_rate

    # --- pinned regressions (unchanged from v1) ---------------------------
    pinned_regressed = [c for c in regressed if c.get("pinned", False)]

    # --- cost ---------------------------------------------------------------
    cost_delta_pct: Optional[float] = None
    if base_avg_cost is not None and cand_avg_cost is not None and base_avg_cost != 0:
        cost_delta_pct = (cand_avg_cost - base_avg_cost) / base_avg_cost * 100

    # --- by_axis / by_split reporting (non-gating) -------------------------
    by_axis: dict[str, dict[str, Optional[float]]] = {}
    axes = sorted({c.get("axis", "") for c in all_cases})
    for axis in axes:
        axis_cases = [c for c in all_cases if c.get("axis") == axis]
        by_axis[axis] = {
            "base": _rate(axis_cases, base_results),
            "cand": _rate(axis_cases, cand_results),
        }

    by_split: dict[str, dict[str, Optional[float]]] = {}
    for split in ("improve", "benchmark"):
        split_cases = [c for c in all_cases if c.get("split") == split]
        by_split[split] = {
            "base": _rate(split_cases, base_results),
            "cand": _rate(split_cases, cand_results),
        }

    # --- meets_policy: AND of all 5 gates -----------------------------------
    min_target_gain_pct = policy.get("min_target_gain_pct", 0.0)
    max_cost_increase_pct = policy.get("max_cost_increase_pct", float("inf"))

    gate_target_gain = target_gain >= min_target_gain_pct
    gate_generalization = bench_target_delta is None or bench_target_delta >= 0
    gate_axis_breach = not axis_breach
    gate_floor_breach = not floor_breach
    gate_pinned = not pinned_regressed
    gate_cost = cost_delta_pct is None or cost_delta_pct <= max_cost_increase_pct

    meets_policy = (
        gate_target_gain
        and gate_generalization
        and gate_axis_breach
        and gate_floor_breach
        and gate_pinned
        and gate_cost
    )

    # --- reasons -------------------------------------------------------------
    reasons: list[str] = []
    if not gate_target_gain:
        reasons.append(
            f"target_gain on {target_axis!r} ({target_gain:.1f} pts) is below the minimum "
            f"required ({min_target_gain_pct:.1f} pts)"
        )
    if not gate_generalization:
        reasons.append(
            f"bench_target_delta on {target_axis!r} is negative ({bench_target_delta:.1f} pts): "
            "candidate overfits to improve-split cases (generalization gate)"
        )
    if axis_breach:
        for axis, n in axis_breach.items():
            limit = max_regressions.get(axis, 0)
            reasons.append(f"axis {axis!r} regressed {n} case(s), exceeding the policy limit of {limit}")
    if floor_breach:
        for axis, rate in floor_breach.items():
            floor = axis_floors.get(axis)
            reasons.append(f"axis {axis!r} candidate pass rate ({rate:.1f}%) is below its floor ({floor}%)")
    if pinned_regressed:
        for case_summary in pinned_regressed:
            reasons.append(f"pinned case {case_summary['name']!r} regressed")
    if not gate_cost:
        reasons.append(
            f"cost_delta_pct ({cost_delta_pct:.1f}%) exceeds the policy limit of {max_cost_increase_pct:.1f}%"
        )

    # --- warnings --------------------------------------------------------
    warnings: list[str] = []
    if not T_bench:
        warnings.append(f"No benchmark cases on {target_axis}: generalization not measured")
    if cost_delta_pct is None:
        warnings.append("Cost not measured")

    return {
        "target_axis": target_axis,
        "target_gain": target_gain,
        "bench_target_delta": bench_target_delta,
        "overall": {"base": overall_base, "cand": overall_cand},
        "by_axis": by_axis,
        "by_split": by_split,
        "fixed": fixed,
        "regressed": regressed,
        "unchanged": unchanged,
        "axis_breach": axis_breach,
        "floor_breach": floor_breach,
        "pinned_regressed": pinned_regressed,
        "cost": {"base": base_avg_cost, "cand": cand_avg_cost, "delta_pct": cost_delta_pct},
        "meets_policy": meets_policy,
        "reasons": reasons,
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Policy staleness (v2 cherry-pick: owner-facing "policy changed after
# evaluation" messaging). Pure comparison -- never recomputes pass/fail.
# ---------------------------------------------------------------------------
_STALENESS_FIELDS = (
    "min_target_gain_pct",
    "max_regressions",
    "axis_floors",
    "max_cost_increase_pct",
    "min_signals",
    "cooldown_hours",
    "max_open_proposals",
    "trials_per_case",
    "pass_threshold",
)


def verdict_is_stale(policy_snapshot: Optional[dict], live_policy: dict) -> dict[str, dict[str, Any]]:
    """Compares a proposal's stored `policy_snapshot` (the policy dict it was
    judged against) to the CURRENT live policy. Returns {field: {from, to}}
    for every field that differs -- empty dict means not stale. This is
    purely a staleness SIGNAL for the owner (e.g. "Policy changed after
    evaluation (min gain 10 -> 5)"); it never changes the stored verdict or
    its meets_policy value.
    """
    if not policy_snapshot:
        return {}

    diff: dict[str, dict[str, Any]] = {}
    for field in _STALENESS_FIELDS:
        old = policy_snapshot.get(field)
        new = live_policy.get(field)
        if old != new:
            diff[field] = {"from": old, "to": new}
    return diff

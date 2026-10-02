"""v2 cherry-pick: compute_verdict pure function
(specs/specs-v2/specs/04-feedback-and-evals.md "Verdict (pure function,
fully unit-tested)", EV-9).

AC-EV-c: the 8-row acceptance table, parametrized (replaces v1's 5-row
table). Target axis = accuracy unless stated, per the spec's table.
"""
import pytest

from app.verdict import compute_verdict, verdict_is_stale


def _case(id_, name=None, axis="accuracy", pinned=False, split="improve"):
    return {"id": id_, "name": name or id_, "axis": axis, "pinned": pinned, "split": split}


DEFAULT_POLICY = {
    "min_target_gain_pct": 5.0,
    "max_regressions": {"accuracy": 0, "safety": 0, "tool-use": 1, "format": 1},
    "axis_floors": {},
    "max_cost_increase_pct": 25.0,
}


# ---------------------------------------------------------------------------
# Row 1: Target gain +25, bench delta +50, 1 format regression (limit 1),
# cost +10% -> meets_policy = true
# ---------------------------------------------------------------------------
def test_row1_meets_policy_true():
    # Target axis (accuracy) T = improve + benchmark cases together (8 total).
    # base passes 0/8 (0%); cand passes 2/8 (25%) -> target_gain +25.
    target_improve = [_case(f"ti{i}", axis="accuracy", split="improve") for i in range(6)]
    base_results = {c["id"]: False for c in target_improve}
    cand_results = {"ti0": True, "ti1": False, "ti2": False, "ti3": False, "ti4": False, "ti5": False}

    # Benchmark subset (2 of the 8): base 0/2 (0%) -> cand 1/2 (50%) -> delta +50.
    target_bench = [_case("tb0", axis="accuracy", split="benchmark"), _case("tb1", axis="accuracy", split="benchmark")]
    base_results.update({"tb0": False, "tb1": False})
    cand_results.update({"tb0": True, "tb1": False})

    # 1 format regression (limit 1): passes on base, fails on cand.
    format_case = _case("f0", axis="format", split="improve")
    base_results["f0"] = True
    cand_results["f0"] = False

    cases = target_improve + target_bench + [format_case]
    verdict = compute_verdict(
        base_results, cand_results, cases, DEFAULT_POLICY,
        target_axis="accuracy", base_avg_cost=1.0, cand_avg_cost=1.1,
    )

    assert verdict["target_gain"] == pytest.approx(25.0)
    assert verdict["bench_target_delta"] == pytest.approx(50.0)
    assert verdict["axis_breach"] == {}
    assert verdict["floor_breach"] == {}
    assert verdict["pinned_regressed"] == []
    assert verdict["cost"]["delta_pct"] == pytest.approx(10.0)
    assert verdict["meets_policy"] is True


# ---------------------------------------------------------------------------
# Row 2: As row 1, but the regressed case is pinned -> false (pinned)
# ---------------------------------------------------------------------------
def test_row2_pinned_regression_blocks():
    target_improve = [_case(f"ti{i}", axis="accuracy", split="improve") for i in range(6)]
    base_results = {c["id"]: False for c in target_improve}
    cand_results = {"ti0": True, "ti1": False, "ti2": False, "ti3": False, "ti4": False, "ti5": False}

    target_bench = [_case("tb0", axis="accuracy", split="benchmark"), _case("tb1", axis="accuracy", split="benchmark")]
    base_results.update({"tb0": False, "tb1": False})
    cand_results.update({"tb0": True, "tb1": False})

    format_case = _case("f0", axis="format", split="improve", pinned=True)  # PINNED this time
    base_results["f0"] = True
    cand_results["f0"] = False

    cases = target_improve + target_bench + [format_case]
    verdict = compute_verdict(
        base_results, cand_results, cases, DEFAULT_POLICY,
        target_axis="accuracy", base_avg_cost=1.0, cand_avg_cost=1.1,
    )

    assert verdict["meets_policy"] is False
    assert len(verdict["pinned_regressed"]) == 1
    assert verdict["pinned_regressed"][0]["id"] == "f0"
    pinned_name = verdict["pinned_regressed"][0]["name"]
    assert any(pinned_name in r for r in verdict["reasons"])


# ---------------------------------------------------------------------------
# Row 3: 1 accuracy regression (limit 0) -> false (axis_breach)
# ---------------------------------------------------------------------------
def test_row3_accuracy_regression_limit_zero_breaches():
    target_improve = [_case(f"ti{i}", axis="accuracy", split="improve") for i in range(5)]
    # base 1/5 passing (ti4), cand 2/5 passing (ti0 fixed, ti4 REGRESSED) -> gain still positive
    base_results = {"ti0": False, "ti1": False, "ti2": False, "ti3": False, "ti4": True}
    cand_results = {"ti0": True, "ti1": False, "ti2": False, "ti3": False, "ti4": False}  # ti4 regressed

    cases = target_improve
    verdict = compute_verdict(base_results, cand_results, cases, DEFAULT_POLICY, target_axis="accuracy")

    assert verdict["axis_breach"] == {"accuracy": 1}
    assert verdict["meets_policy"] is False


# ---------------------------------------------------------------------------
# Row 4: Target gain +5 (min policy threshold higher, e.g. 10) -> false
# (target gain)
# ---------------------------------------------------------------------------
def test_row4_target_gain_below_higher_threshold():
    target_improve = [_case(f"ti{i}", axis="accuracy", split="improve") for i in range(20)]
    # base 10/20 (50%), cand 11/20 (55%) -> gain +5
    base_results = {f"ti{i}": (i < 10) for i in range(20)}
    cand_results = {f"ti{i}": (i < 11) for i in range(20)}

    policy = {**DEFAULT_POLICY, "min_target_gain_pct": 10.0}  # higher than default 5.0
    verdict = compute_verdict(base_results, cand_results, target_improve, policy, target_axis="accuracy")

    assert verdict["target_gain"] == pytest.approx(5.0)
    assert verdict["meets_policy"] is False
    assert any("target_gain" in r for r in verdict["reasons"])


# ---------------------------------------------------------------------------
# Row 5: Improve-split target cases fixed, benchmark target delta -50 ->
# false (generalization gate -- overfitting caught)
# ---------------------------------------------------------------------------
def test_row5_generalization_gate_catches_overfitting():
    # Improve-split target cases: base 0/2 -> cand 2/2 (fixed, gain +100).
    target_improve = [_case("ti0", axis="accuracy", split="improve"), _case("ti1", axis="accuracy", split="improve")]
    base_results = {"ti0": False, "ti1": False}
    cand_results = {"ti0": True, "ti1": True}

    # Benchmark target cases regress: base 2/2 -> cand 0/2 (delta -100,
    # but using -50 setup per spec text: base 1/2 -> cand 0/2 gives -50).
    target_bench = [_case("tb0", axis="accuracy", split="benchmark"), _case("tb1", axis="accuracy", split="benchmark")]
    base_results.update({"tb0": True, "tb1": False})  # 1/2 = 50%
    cand_results.update({"tb0": False, "tb1": False})  # 0/2 = 0%, delta -50

    cases = target_improve + target_bench
    verdict = compute_verdict(base_results, cand_results, cases, DEFAULT_POLICY, target_axis="accuracy")

    # Combined T (improve+bench) still shows a positive overall target_gain...
    assert verdict["target_gain"] == pytest.approx(25.0)
    # ...but the benchmark-only delta is negative: overfitting caught.
    assert verdict["bench_target_delta"] == pytest.approx(-50.0)
    assert verdict["meets_policy"] is False
    assert any("generalization" in r or "overfit" in r for r in verdict["reasons"])


# ---------------------------------------------------------------------------
# Row 6: One of 3 safety cases regresses (limit 0); safety rate 67% (floor
# 100) -> false (axis_breach AND floor_breach)
# ---------------------------------------------------------------------------
def test_row6_axis_breach_and_floor_breach_both():
    # Target axis (accuracy) passes fine on its own.
    target_improve = [_case(f"ti{i}", axis="accuracy", split="improve") for i in range(4)]
    base_results = {c["id"]: False for c in target_improve}
    cand_results = {"ti0": True, "ti1": True, "ti2": False, "ti3": False}  # gain +50

    # 3 safety cases: all pass on base, 1 regresses on candidate -> cand 2/3 = 66.7%
    safety_cases = [_case(f"s{i}", axis="safety", split="improve") for i in range(3)]
    base_results.update({"s0": True, "s1": True, "s2": True})
    cand_results.update({"s0": True, "s1": True, "s2": False})  # s2 regressed

    policy = {**DEFAULT_POLICY, "axis_floors": {"safety": 100.0}}
    cases = target_improve + safety_cases
    verdict = compute_verdict(base_results, cand_results, cases, policy, target_axis="accuracy")

    assert verdict["axis_breach"] == {"safety": 1}
    assert verdict["floor_breach"]["safety"] == pytest.approx(200 / 3, rel=1e-3)
    assert verdict["meets_policy"] is False


# ---------------------------------------------------------------------------
# Row 7: Cost +40% (limit 25) -> false (cost)
# ---------------------------------------------------------------------------
def test_row7_cost_exceeds_limit():
    target_improve = [_case(f"ti{i}", axis="accuracy", split="improve") for i in range(4)]
    base_results = {c["id"]: False for c in target_improve}
    cand_results = {"ti0": True, "ti1": True, "ti2": False, "ti3": False}  # gain +50, otherwise clean

    verdict = compute_verdict(
        base_results, cand_results, target_improve, DEFAULT_POLICY,
        target_axis="accuracy", base_avg_cost=1.0, cand_avg_cost=1.4,
    )

    assert verdict["cost"]["delta_pct"] == pytest.approx(40.0)
    assert verdict["meets_policy"] is False
    assert any("cost" in r.lower() for r in verdict["reasons"])


# ---------------------------------------------------------------------------
# Row 8: No benchmark cases on target axis, otherwise passing -> true, with
# a warning present
# ---------------------------------------------------------------------------
def test_row8_no_benchmark_cases_passes_with_warning():
    target_improve = [_case(f"ti{i}", axis="accuracy", split="improve") for i in range(4)]
    base_results = {c["id"]: False for c in target_improve}
    cand_results = {"ti0": True, "ti1": True, "ti2": False, "ti3": False}  # gain +50, no regressions

    verdict = compute_verdict(base_results, cand_results, target_improve, DEFAULT_POLICY, target_axis="accuracy")

    assert verdict["bench_target_delta"] is None
    assert verdict["meets_policy"] is True
    assert any("no benchmark cases" in w.lower() for w in verdict["warnings"])


# ---------------------------------------------------------------------------
# Additional unit coverage beyond the 8-row table (output shape, cost-None
# handling, pure-function no-side-effects, policy staleness).
# ---------------------------------------------------------------------------
def test_output_has_all_required_keys():
    cases = [_case("c1")]
    verdict = compute_verdict({"c1": True}, {"c1": True}, cases, DEFAULT_POLICY, target_axis="accuracy")
    expected_keys = {
        "target_axis",
        "target_gain",
        "bench_target_delta",
        "overall",
        "by_axis",
        "by_split",
        "fixed",
        "regressed",
        "axis_breach",
        "floor_breach",
        "pinned_regressed",
        "cost",
        "meets_policy",
        "reasons",
        "warnings",
    }
    assert expected_keys <= set(verdict.keys())


def test_cost_none_is_not_measured_not_zero_not_error():
    cases = [_case("c1")]
    verdict = compute_verdict(
        {"c1": False}, {"c1": True}, cases, DEFAULT_POLICY, target_axis="accuracy",
        base_avg_cost=None, cand_avg_cost=None,
    )
    assert verdict["cost"]["delta_pct"] is None
    assert verdict["cost"]["base"] is None
    assert verdict["cost"]["cand"] is None
    assert any("cost not measured" in w.lower() for w in verdict["warnings"])
    # cost gate must not block meets_policy when unmeasured
    assert verdict["meets_policy"] is True


def test_pure_function_no_side_effects_on_inputs():
    cases = [_case("c1")]
    base_results = {"c1": True}
    cand_results = {"c1": False}
    compute_verdict(base_results, cand_results, cases, DEFAULT_POLICY, target_axis="accuracy")
    assert base_results == {"c1": True}
    assert cand_results == {"c1": False}
    assert cases == [_case("c1")]


def test_verdict_is_stale_no_snapshot_returns_empty():
    assert verdict_is_stale(None, {"min_target_gain_pct": 10.0}) == {}


def test_verdict_is_stale_detects_changed_fields():
    snapshot = {"min_target_gain_pct": 10.0, "max_regressions": {"accuracy": 0}}
    live = {"min_target_gain_pct": 5.0, "max_regressions": {"accuracy": 0}}
    diff = verdict_is_stale(snapshot, live)
    assert diff == {"min_target_gain_pct": {"from": 10.0, "to": 5.0}}


def test_verdict_is_stale_unchanged_returns_empty():
    snapshot = {"min_target_gain_pct": 5.0}
    live = {"min_target_gain_pct": 5.0}
    assert verdict_is_stale(snapshot, live) == {}

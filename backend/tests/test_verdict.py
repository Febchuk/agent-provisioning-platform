"""T3.4 — compute_verdict pure function (specs/04-feedback-and-evals.md
"Verdict (pure function, fully unit-tested)", EV-9).

AC-EV-c: the 5-row acceptance table, parametrized.
"""
import pytest

from app.verdict import compute_verdict


def _case(id_, name=None, axis="accuracy", pinned=False, hidden=False):
    return {"id": id_, "name": name or id_, "axis": axis, "pinned": pinned, "hidden": hidden}


def _make_8_cases(regressed_axis="format", regressed_pinned=False):
    """8 visible cases: base passes 5/8, cand passes 7/8, with exactly 1
    regression (on `regressed_axis`, possibly pinned) and 3 fixes, matching
    row 1/2's "base 5/8, cand 7/8, 1 <axis> regression" setup:

      c1-c4: pass on both (unchanged-passing)
      c5:    pass on base, FAIL on cand  -> the 1 regression (on regressed_axis)
      c6-c8: fail on base, pass on cand  -> 3 fixes

    base passing = c1,c2,c3,c4,c5 = 5/8. cand passing = c1,c2,c3,c4,c6,c7,c8 = 7/8.
    """
    cases = [_case(f"c{i}") for i in range(1, 9)]
    cases[4]["axis"] = regressed_axis  # c5 (index 4) is the regressed case
    cases[4]["pinned"] = regressed_pinned

    base_results = {f"c{i}": (i <= 5) for i in range(1, 9)}
    cand_results = {f"c{i}": (i <= 4 or i >= 6) for i in range(1, 9)}

    return cases, base_results, cand_results


def test_row1_meets_policy_true():
    cases, base_results, cand_results = _make_8_cases(regressed_axis="format", regressed_pinned=False)
    policy = {"min_target_gain_pct": 5.0, "max_regressions": {"format": 1, "accuracy": 0}}

    verdict = compute_verdict(base_results, cand_results, cases, policy)

    assert verdict["n_visible"] == 8
    assert verdict["base_score"] == pytest.approx(5 / 8 * 100)
    assert verdict["cand_score"] == pytest.approx(7 / 8 * 100)
    assert verdict["avg_delta"] == pytest.approx((7 - 5) / 8 * 100)
    assert len(verdict["regressed"]) == 1
    assert verdict["by_axis"] == {"format": 1}
    assert verdict["axis_breach"] == {}
    assert verdict["meets_policy"] is True


def test_row2_pinned_regression_blocks_and_names_case():
    cases, base_results, cand_results = _make_8_cases(regressed_axis="format", regressed_pinned=True)
    policy = {"min_target_gain_pct": 5.0, "max_regressions": {"format": 1, "accuracy": 0}}

    verdict = compute_verdict(base_results, cand_results, cases, policy)

    assert verdict["meets_policy"] is False
    assert len(verdict["pinned_regressed"]) == 1
    regressed_name = verdict["pinned_regressed"][0]["name"]
    assert any(regressed_name in reason for reason in verdict["reasons"])


def test_row3_accuracy_regression_limit_zero_breaches():
    cases, base_results, cand_results = _make_8_cases(regressed_axis="accuracy", regressed_pinned=False)
    policy = {"min_target_gain_pct": 5.0, "max_regressions": {"accuracy": 0, "format": 1}}

    verdict = compute_verdict(base_results, cand_results, cases, policy)

    assert verdict["meets_policy"] is False
    assert verdict["axis_breach"] == {"accuracy": 1}


def test_row4_avg_delta_zero_mentions_minimum_improvement():
    cases = [_case(f"c{i}") for i in range(1, 5)]
    base_results = {"c1": True, "c2": True, "c3": False, "c4": False}
    cand_results = {"c1": True, "c2": False, "c3": True, "c4": False}  # same pass count (2/4) -> avg_delta 0
    policy = {"min_target_gain_pct": 5.0, "max_regressions": {"accuracy": 1}}

    verdict = compute_verdict(base_results, cand_results, cases, policy)

    assert verdict["avg_delta"] == pytest.approx(0.0)
    assert verdict["meets_policy"] is False
    assert any("minimum improvement" in r.lower() for r in verdict["reasons"])


def test_row5_axis_not_in_policy_defaults_to_zero():
    cases = [_case("c1", axis="tool-use"), _case("c2"), _case("c3"), _case("c4")]
    base_results = {"c1": True, "c2": True, "c3": False, "c4": False}
    cand_results = {"c1": False, "c2": True, "c3": True, "c4": True}  # c1 regressed on "tool-use"
    policy = {"min_target_gain_pct": 0.0, "max_regressions": {"accuracy": 0}}  # no "tool-use" entry

    verdict = compute_verdict(base_results, cand_results, cases, policy)

    assert verdict["meets_policy"] is False
    assert verdict["axis_breach"] == {"tool-use": 1}


# ---------------------------------------------------------------------------
# Additional unit coverage beyond the 5-row table (output shape, no DB/IO).
# ---------------------------------------------------------------------------
def test_output_has_all_required_keys():
    cases = [_case("c1")]
    verdict = compute_verdict({"c1": True}, {"c1": True}, cases, {"min_target_gain_pct": 0.0, "max_regressions": {}})
    expected_keys = {
        "base_score",
        "cand_score",
        "n_visible",
        "fixed",
        "regressed",
        "unchanged",
        "avg_delta",
        "by_axis",
        "axis_breach",
        "pinned_regressed",
        "meets_policy",
        "reasons",
        "generalization",
    }
    assert expected_keys <= set(verdict.keys())


def test_hidden_cases_excluded_from_n_visible_but_counted_in_generalization():
    cases = [_case("c1"), _case("c2", hidden=True), _case("c3", hidden=True)]
    base_results = {"c1": True, "c2": True, "c3": False}
    cand_results = {"c1": True, "c2": True, "c3": True}
    verdict = compute_verdict(base_results, cand_results, cases, {"min_target_gain_pct": 0.0, "max_regressions": {}})

    assert verdict["n_visible"] == 1
    assert verdict["generalization"] == {"base": 1, "cand": 2, "total": 2}


def test_pure_function_no_side_effects_on_inputs():
    cases = [_case("c1")]
    base_results = {"c1": True}
    cand_results = {"c1": False}
    policy = {"min_target_gain_pct": 0.0, "max_regressions": {}}
    compute_verdict(base_results, cand_results, cases, policy)
    assert base_results == {"c1": True}
    assert cand_results == {"c1": False}
    assert cases == [_case("c1")]

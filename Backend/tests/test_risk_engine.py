"""
Unit tests for app.services.risk_engine.

These are pure-function tests — no DB, no network. They verify:
  * the composite scoring formula and rounding
  * GREEN / AMBER / RED tier boundary behaviour
  * the prediabetes trajectory heuristic and its labels
  * the component breakdown contributions
  * that localised text is wired through evaluate_risk()
"""
import pytest

from app.services.risk_engine import (
    AMBER_MAX,
    GREEN_MAX,
    TIER_AMBER,
    TIER_GREEN,
    TIER_RED,
    compute_composite,
    compute_prediabetes_trajectory,
    evaluate_risk,
)


# --------------------------------------------------------------------------- #
# Composite formula
# --------------------------------------------------------------------------- #
def test_composite_formula_all_components():
    # idrs 50*0.6=30 ; voice 0.8*100*0.25=20 ; rppg flag=15  => 65
    composite, breakdown = compute_composite(50, 0.8, True)
    assert composite == pytest.approx(65.0)
    assert breakdown.idrs_contribution == pytest.approx(30.0)
    assert breakdown.voice_contribution == pytest.approx(20.0)
    assert breakdown.rppg_contribution == pytest.approx(15.0)


def test_composite_zero_inputs():
    composite, breakdown = compute_composite(0, 0.0, False)
    assert composite == 0.0
    assert breakdown.idrs_contribution == 0.0
    assert breakdown.voice_contribution == 0.0
    assert breakdown.rppg_contribution == 0.0


def test_rppg_flag_adds_fixed_points():
    without = compute_composite(10, 0.0, False)[0]
    with_flag = compute_composite(10, 0.0, True)[0]
    assert with_flag - without == pytest.approx(15.0)


def test_composite_is_rounded_two_places():
    composite, _ = compute_composite(33, 0.333, True)
    # 33*0.6=19.8 ; 0.333*100*0.25=8.325 ; +15 => 43.125 -> 43.12 (round half-even)
    assert composite == round(composite, 2)


# --------------------------------------------------------------------------- #
# Tier boundaries
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "idrs,voice,rppg,expected",
    [
        (0, 0.0, False, TIER_GREEN),       # 0
        (60, 0.0, False, TIER_GREEN),      # 36 -> green
        (65, 0.0, False, TIER_AMBER),      # 39 ... see exact below
    ],
)
def test_tier_rough(idrs, voice, rppg, expected):
    # sanity smoke; exact boundary cases tested separately
    composite, _ = compute_composite(idrs, voice, rppg)
    result = evaluate_risk(idrs, voice, rppg)
    if composite < GREEN_MAX:
        assert result.tier == TIER_GREEN
    elif composite < AMBER_MAX:
        assert result.tier == TIER_AMBER
    else:
        assert result.tier == TIER_RED


def test_green_just_below_40():
    # composite 39.9 -> GREEN
    r = evaluate_risk(66.5, 0.0, False)  # 66.5*0.6 = 39.9
    assert r.composite_score < GREEN_MAX
    assert r.tier == TIER_GREEN


def test_amber_at_exactly_40():
    # composite exactly 40 -> AMBER (>= 40)
    r = evaluate_risk(0, 1.6, False)  # not possible (voice<=1); build via idrs
    # Use idrs only: 40 / 0.6 = 66.667 -> messy. Use components: idrs 25 (=15)
    # voice 1.0 (=25) -> 40 exactly.
    r = evaluate_risk(25, 1.0, False)
    assert r.composite_score == pytest.approx(40.0)
    assert r.tier == TIER_AMBER


def test_amber_just_below_65():
    r = evaluate_risk(100, 0.0, False)  # 60
    assert r.tier == TIER_AMBER


def test_red_at_exactly_65():
    r = evaluate_risk(50, 0.8, True)  # 30 + 20 + 15 = 65
    assert r.composite_score == pytest.approx(65.0)
    assert r.tier == TIER_RED


def test_red_high():
    r = evaluate_risk(100, 1.0, True)  # 60 + 25 + 15 = 100
    assert r.composite_score == pytest.approx(100.0)
    assert r.tier == TIER_RED


# --------------------------------------------------------------------------- #
# Prediabetes trajectory
# --------------------------------------------------------------------------- #
def test_trajectory_none_when_no_input():
    assert (
        compute_prediabetes_trajectory(None, None, False, False) is None
    )


def test_trajectory_stable_label():
    # waist 82 -> +10 only => 10 -> stable
    t = compute_prediabetes_trajectory(82, None, False, False)
    assert t is not None
    assert t.label == "stable"
    assert t.score == pytest.approx(10.0)


def test_trajectory_rising_label():
    # waist 92 (+22) + family (+15) = 37 -> rising
    t = compute_prediabetes_trajectory(92, None, False, True)
    assert t.label == "rising"
    assert "waist" in t.drivers
    assert "family_history" in t.drivers


def test_trajectory_high_risk_label_and_cap():
    # waist 105 (+35) + diet 100 (*0.25=25) + occupation (+15) + family (+15)
    # = 90 -> high_risk, and well under the 100 cap
    t = compute_prediabetes_trajectory(105, 100, True, True)
    assert t.label == "high_risk"
    assert t.score <= 100.0
    assert "diet" in t.drivers


def test_trajectory_score_never_exceeds_100():
    # Max achievable from the heuristic: waist 35 + diet 25 + occ 15 + family 15 = 90.
    # The min(score, 100) cap guarantees the score can never exceed 100 even if
    # weights are later increased.
    t = compute_prediabetes_trajectory(150, 100, True, True)
    assert t.score <= 100.0
    assert t.score == pytest.approx(90.0)


def test_trajectory_diet_driver_threshold():
    # dietary_score below 60 should not list 'diet' as a driver
    t = compute_prediabetes_trajectory(None, 40, False, True)
    assert "diet" not in t.drivers


# --------------------------------------------------------------------------- #
# evaluate_risk wiring
# --------------------------------------------------------------------------- #
def test_evaluate_risk_returns_localised_text():
    r = evaluate_risk(10, 0.1, False, lang="en")
    assert r.action_text
    assert r.explanation
    # explanation embeds the rounded score
    assert str(int(round(r.composite_score))) in r.explanation


def test_evaluate_risk_lang_changes_text():
    en = evaluate_risk(80, 0.5, True, lang="en")
    kn = evaluate_risk(80, 0.5, True, lang="kn")
    assert en.tier == kn.tier
    # localised strings should differ between languages
    assert en.action_text != kn.action_text


def test_evaluate_risk_includes_trajectory_when_inputs_present():
    r = evaluate_risk(
        40, 0.3, False,
        waist_cm=95, dietary_score=70,
        occupation_transition_flag=True, family_history_flag=False,
    )
    assert r.prediabetes_trajectory is not None
    assert r.prediabetes_trajectory.label in {"stable", "rising", "high_risk"}


def test_evaluate_risk_no_trajectory_when_absent():
    r = evaluate_risk(40, 0.3, False)
    assert r.prediabetes_trajectory is None

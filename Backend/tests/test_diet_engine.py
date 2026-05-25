"""
Unit tests for app.services.diet_engine.

Verify:
  * weekly rotation via last_advice_index (week_index / next_index)
  * the supplement / branded-product safety guardrail (_assert_safe)
  * region / profile / income-band graceful fallbacks
  * language fallback to English when a translation is missing
  * that no banned term ever leaks into served advice from the shipped KB
"""
import pytest

from app.core.responses import AppError
from app.services import diet_engine
from app.services.diet_engine import (
    _assert_safe,
    _income_band,
    _profile_for_tier,
    _region_from_district,
    get_weekly_advice,
)


# --------------------------------------------------------------------------- #
# Safety guardrail
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "bad_text",
    [
        "Take a multivitamin every morning",
        "Buy protein powder from the store",
        "Use Horlicks daily",
        "Try this supplement",
        "Purchase the premium plan",
        "A capsule after lunch",
    ],
)
def test_assert_safe_blocks_banned_terms(bad_text):
    with pytest.raises(AppError):
        _assert_safe(bad_text)


@pytest.mark.parametrize(
    "ok_text",
    [
        "Swap white rice for red rice three times a week",
        "Add drumstick leaves to your sambar",
        "Walk 20 minutes before your first meal",
        "Eat a handful of roasted chana as a snack",
    ],
)
def test_assert_safe_allows_clean_food_advice(ok_text):
    assert _assert_safe(ok_text) == ok_text


def test_assert_safe_handles_empty():
    assert _assert_safe("") == ""


# --------------------------------------------------------------------------- #
# Profile / band / region mapping
# --------------------------------------------------------------------------- #
def test_profile_for_tier_amber():
    assert _profile_for_tier("AMBER") == "amber"
    assert _profile_for_tier("amber") == "amber"


def test_profile_for_tier_defaults_to_prediabetic():
    assert _profile_for_tier("GREEN") == "prediabetic"
    assert _profile_for_tier("RED") == "prediabetic"
    assert _profile_for_tier(None) == "prediabetic"


def test_income_band_defaults_to_low_income():
    assert _income_band({}) == "low_income"
    assert _income_band({"income_band": "garbage"}) == "low_income"


def test_income_band_respects_valid_value():
    assert _income_band({"income_band": "mid_income"}) == "mid_income"


def test_region_from_district_coastal():
    assert _region_from_district({"district_code": "bantval"}) == "karnataka_coastal"
    assert _region_from_district({"district_code": "udupi"}) == "karnataka_coastal"


def test_region_from_district_default():
    assert _region_from_district({"district_code": "xyz"}) == "default"
    assert _region_from_district({}) == "default"


# --------------------------------------------------------------------------- #
# Weekly rotation
# --------------------------------------------------------------------------- #
def test_weekly_rotation_index_advances_with_last_index():
    patient0 = {"region": "karnataka_coastal", "last_advice_index": 0}
    patient1 = {"region": "karnataka_coastal", "last_advice_index": 1}

    a0 = get_weekly_advice(patient0, lang="en", tier="RED")
    a1 = get_weekly_advice(patient1, lang="en", tier="RED")

    assert a0["week_index"] == 0
    assert a1["week_index"] == 1
    assert a0["next_index"] == 1
    # advice text should differ as the list rotates
    assert a0["advice"] != a1["advice"]


def test_weekly_rotation_wraps_around():
    # low_income prediabetic list has 4 items; index 4 should wrap to 0
    p_first = {"region": "karnataka_coastal", "last_advice_index": 0}
    p_wrap = {"region": "karnataka_coastal", "last_advice_index": 4}
    first = get_weekly_advice(p_first, lang="en", tier="RED")
    wrap = get_weekly_advice(p_wrap, lang="en", tier="RED")
    assert wrap["week_index"] == 0
    assert wrap["advice"] == first["advice"]


def test_next_index_is_modular():
    p = {"region": "karnataka_coastal", "last_advice_index": 3}
    a = get_weekly_advice(p, lang="en", tier="RED")
    # 4-item list: index 3 -> next wraps to 0
    assert a["next_index"] == 0


# --------------------------------------------------------------------------- #
# Fallbacks
# --------------------------------------------------------------------------- #
def test_region_fallback_to_default():
    # unknown region should fall back to the 'default' KB region
    patient = {"region": "atlantis", "last_advice_index": 0}
    advice = get_weekly_advice(patient, lang="en", tier="RED")
    assert advice["region"] == "default"
    assert advice["advice"]


def test_lang_fallback_to_english():
    # request a language not present on the item (e.g. 'ta' missing in KB items)
    patient = {"region": "karnataka_coastal", "last_advice_index": 0}
    en = get_weekly_advice(patient, lang="en", tier="RED")
    ta = get_weekly_advice(patient, lang="ta", tier="RED")
    # KB items only carry hi/kn/en, so 'ta' must fall back to the en string
    assert ta["advice"] == en["advice"]


def test_kannada_advice_differs_from_english():
    patient = {"region": "karnataka_coastal", "last_advice_index": 0}
    en = get_weekly_advice(patient, lang="en", tier="RED")
    kn = get_weekly_advice(patient, lang="kn", tier="RED")
    assert kn["advice"] != en["advice"]


def test_amber_profile_resolves():
    patient = {"region": "karnataka_coastal", "last_advice_index": 0}
    advice = get_weekly_advice(patient, lang="en", tier="AMBER")
    assert advice["profile"] == "amber"
    assert advice["advice"]


# --------------------------------------------------------------------------- #
# Whole-KB safety sweep
# --------------------------------------------------------------------------- #
def test_entire_kb_passes_safety():
    """No item or tip in the shipped KB may contain a banned term."""
    kb = diet_engine._load_kb()
    for region, profiles in kb.items():
        for profile, bands in profiles.items():
            for band, items in bands.items():
                for item in items:
                    for key, val in item.items():
                        if key == "tips":
                            for tip in val:
                                assert _assert_safe(tip) == tip
                        else:
                            assert _assert_safe(val) == val

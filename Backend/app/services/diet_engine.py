"""
Diet engine.

Loads a geo-localised, rule-based knowledge base (data/diet_advice.json) and
serves weekly-rotating advice in the patient's language.

Guardrails (enforced):
  * NEVER recommend supplements, branded products, or paid services.
  * Advice is food-swap / activity based and uses locally available items.

The KB structure is:
  {region: {profile: {income_band: [ {hi, kn, ta, ...en, tips:[...]}, ... ]}}}

Rotation: a patient's `last_advice_index` is incremented each time advice is
served, so successive weekly fetches cycle through the list.
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from typing import Any, Dict, List, Optional, Tuple

from app.config import settings
from app.core.i18n import normalise_lang
from app.core.responses import AppError

logger = logging.getLogger("novacare.diet")

# Words that must never appear in served advice (defence in depth, in case
# the KB is edited carelessly).
_BANNED_PATTERNS = re.compile(
    r"\b("
    r"supplement|supplements|multivitamin|protein\s*powder|capsule|tablet|"
    r"pill|tonic|syrup|brand|branded|patanjali|himalaya|ensure|horlicks|"
    r"bournvita|complan|buy|purchase|subscription|paid\s+plan|premium"
    r")\b",
    re.IGNORECASE,
)

DEFAULT_REGION = "default"


@lru_cache(maxsize=1)
def _load_kb() -> Dict[str, Any]:
    with open(settings.DIET_KB_PATH, "r", encoding="utf-8") as fh:
        return json.load(fh)


def _profile_for_tier(tier: Optional[str]) -> str:
    """Map a risk tier to a diet profile key."""
    t = (tier or "").upper()
    if t == "AMBER":
        return "amber"
    # GREEN patients still get gentle prediabetic-style guidance; RED too.
    return "prediabetic"


def _income_band(patient: Dict[str, Any]) -> str:
    band = patient.get("income_band")
    if band in ("low_income", "mid_income"):
        return band
    # Default to low_income to keep advice cost-free and accessible.
    return "low_income"


def _resolve_list(
    kb: Dict[str, Any],
    region: str,
    profile: str,
    income_band: str,
) -> Tuple[List[Dict[str, Any]], str, str]:
    """
    Find the advice list, gracefully falling back across region / profile /
    income band. Returns (list, used_region, used_profile).
    """
    regions_to_try = [region, DEFAULT_REGION]
    profiles_to_try = [profile, "prediabetic"]
    bands_to_try = [income_band, "low_income", "mid_income"]

    for r in regions_to_try:
        region_block = kb.get(r)
        if not region_block:
            continue
        for p in profiles_to_try:
            profile_block = region_block.get(p)
            if not profile_block:
                continue
            for b in bands_to_try:
                lst = profile_block.get(b)
                if lst:
                    return lst, r, p
    raise AppError("No dietary advice available for this profile", status_code=404)


def _assert_safe(text: str) -> str:
    if _BANNED_PATTERNS.search(text or ""):
        logger.error("Blocked unsafe diet advice containing banned term: %s", text)
        raise AppError("Diet advice failed safety check", status_code=500)
    return text


def get_weekly_advice(
    patient: Dict[str, Any],
    *,
    region: Optional[str] = None,
    lang: str = "en",
    tier: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resolve the next rotating advice item for a patient.

    Does NOT persist the new index — the caller (repository/service) advances
    `last_advice_index` so this function stays pure & testable.
    """
    kb = _load_kb()
    lang = normalise_lang(lang)

    region = region or patient.get("region") or _region_from_district(patient)
    profile = _profile_for_tier(tier or patient.get("last_tier"))
    band = _income_band(patient)

    advice_list, used_region, used_profile = _resolve_list(kb, region, profile, band)

    last_index = int(patient.get("last_advice_index", 0) or 0)
    idx = last_index % len(advice_list)
    item = advice_list[idx]

    advice_text = item.get(lang) or item.get("en") or next(iter(item.values()))
    advice_text = _assert_safe(advice_text)
    tips = [_assert_safe(t) for t in item.get("tips", [])]

    return {
        "region": used_region,
        "profile": used_profile,
        "income_band": band,
        "lang": lang,
        "week_index": idx,
        "next_index": (idx + 1) % len(advice_list),
        "advice": advice_text,
        "tips": tips,
    }


def _region_from_district(patient: Dict[str, Any]) -> str:
    """Very small mapping; extend as more regions are onboarded."""
    district = (patient.get("district_code") or "").lower()
    coastal = {"dk", "udupi", "uttara_kannada", "mangaluru", "bantval"}
    if any(c in district for c in coastal):
        return "karnataka_coastal"
    return DEFAULT_REGION

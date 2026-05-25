"""
Risk engine.

Composite NCD risk tier from three on-device signals:

    composite = (idrs_score * 0.6)
              + (voice_score * 100 * 0.25)
              + (rppg_hrv_flag * 15)

    GREEN  if composite < 40
    AMBER  if 40 <= composite < 65
    RED    if composite >= 65

Also computes a prediabetes trajectory from waist circumference, dietary
score, occupation transition, and family history. Explanations and action
text are localised via the `lang` parameter.

This module is pure and synchronous so it is trivial to unit-test.
"""
from __future__ import annotations

from typing import List, Optional

from app.core.i18n import translate
from app.schemas.session import (
    ComponentBreakdown,
    PrediabetesTrajectory,
    RiskResult,
)

# Weights / thresholds kept as named constants for clarity & testing.
IDRS_WEIGHT = 0.6
VOICE_WEIGHT = 0.25
RPPG_POINTS = 15.0

GREEN_MAX = 40.0
AMBER_MAX = 65.0

TIER_GREEN = "GREEN"
TIER_AMBER = "AMBER"
TIER_RED = "RED"


def _tier_for(composite: float) -> str:
    if composite < GREEN_MAX:
        return TIER_GREEN
    if composite < AMBER_MAX:
        return TIER_AMBER
    return TIER_RED


def compute_composite(
    idrs_score: float,
    voice_score: float,
    rppg_hrv_flag: bool,
) -> tuple[float, ComponentBreakdown]:
    """Return (composite_score, component_breakdown)."""
    idrs_c = idrs_score * IDRS_WEIGHT
    voice_c = voice_score * 100.0 * VOICE_WEIGHT
    rppg_c = (RPPG_POINTS if rppg_hrv_flag else 0.0)
    composite = idrs_c + voice_c + rppg_c
    breakdown = ComponentBreakdown(
        idrs_contribution=round(idrs_c, 2),
        voice_contribution=round(voice_c, 2),
        rppg_contribution=round(rppg_c, 2),
    )
    return round(composite, 2), breakdown


def compute_prediabetes_trajectory(
    waist_cm: Optional[float],
    dietary_score: Optional[float],
    occupation_transition_flag: bool,
    family_history_flag: bool,
) -> Optional[PrediabetesTrajectory]:
    """
    A transparent, additive heuristic (0-100). Higher => steeper trajectory
    toward diabetes. Returns None if there is no input at all.
    """
    if (
        waist_cm is None
        and dietary_score is None
        and not occupation_transition_flag
        and not family_history_flag
    ):
        return None

    score = 0.0
    drivers: List[str] = []

    # Central adiposity is the single strongest modifiable driver.
    if waist_cm is not None:
        if waist_cm >= 100:
            score += 35
            drivers.append("waist")
        elif waist_cm >= 90:
            score += 22
            drivers.append("waist")
        elif waist_cm >= 80:
            score += 10

    # Dietary score 0-100: higher means worse diet quality here.
    if dietary_score is not None:
        score += min(max(dietary_score, 0.0), 100.0) * 0.25
        if dietary_score >= 60:
            drivers.append("diet")

    # Rural->urban / sedentary occupation transition.
    if occupation_transition_flag:
        score += 15
        drivers.append("occupation_transition")

    # Genetic predisposition.
    if family_history_flag:
        score += 15
        drivers.append("family_history")

    score = round(min(score, 100.0), 2)

    if score < 30:
        label = "stable"
    elif score < 60:
        label = "rising"
    else:
        label = "high_risk"

    return PrediabetesTrajectory(score=score, label=label, drivers=drivers)


def _action_key(tier: str) -> str:
    return {
        TIER_GREEN: "action_green",
        TIER_AMBER: "action_amber",
        TIER_RED: "action_red",
    }[tier]


def _explanation_key(tier: str) -> str:
    return {
        TIER_GREEN: "explain_green",
        TIER_AMBER: "explain_amber",
        TIER_RED: "explain_red",
    }[tier]


def evaluate_risk(
    idrs_score: float,
    voice_score: float,
    rppg_hrv_flag: bool,
    *,
    waist_cm: Optional[float] = None,
    dietary_score: Optional[float] = None,
    occupation_transition_flag: bool = False,
    family_history_flag: bool = False,
    lang: str = "en",
) -> RiskResult:
    """Full evaluation: tier + breakdown + localised text + trajectory."""
    composite, breakdown = compute_composite(idrs_score, voice_score, rppg_hrv_flag)
    tier = _tier_for(composite)

    action_text = translate(_action_key(tier), lang)
    explanation = translate(
        _explanation_key(tier),
        lang,
        score=int(round(composite)),
    )

    trajectory = compute_prediabetes_trajectory(
        waist_cm=waist_cm,
        dietary_score=dietary_score,
        occupation_transition_flag=occupation_transition_flag,
        family_history_flag=family_history_flag,
    )

    return RiskResult(
        tier=tier,
        composite_score=composite,
        component_breakdown=breakdown,
        action_text=action_text,
        explanation=explanation,
        prediabetes_trajectory=trajectory,
    )

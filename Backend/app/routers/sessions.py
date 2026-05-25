"""
Screening sessions router.

  POST /sessions/                create session (ASHA)
  POST /sessions/{id}/idrs       submit IDRS answers
  POST /sessions/{id}/voice      submit voice features (NOT raw audio)
  POST /sessions/{id}/rppg       submit rPPG features
  GET  /sessions/{id}/result     computed tier + action card
  GET  /sessions/{id}/report     patient-facing localised report

The result endpoint runs the risk engine using whatever signals are present;
missing voice/rppg default to neutral (0.0 / False) so a partial screen still
yields a tier.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.deps import require_any, require_asha
from app.core.i18n import translate
from app.core.responses import AppError, ok
from app.database import get_db
from app.repositories import patient_repo, session_repo
from app.schemas.session import (
    IdrsSubmit,
    RppgFeatures,
    SessionCreate,
    VoiceFeatures,
)
from app.services import diet_engine
from app.services.risk_engine import evaluate_risk

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _idrs_total(body: IdrsSubmit) -> int:
    if body.idrs_total is not None:
        return body.idrs_total
    parts = [
        body.age_score or 0,
        body.waist_score or 0,
        body.activity_score or 0,
        body.family_history_score or 0,
    ]
    return min(sum(parts), 100)


@router.post("/")
async def create_session(
    body: SessionCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    principal=Depends(require_asha),
):
    patient = await patient_repo.get_patient(db, body.patient_id)
    if patient is None:
        raise AppError("Patient not found", status_code=404)

    created = await session_repo.create_session(
        db,
        {
            "patient_id": body.patient_id,
            "asha_id": principal.get("asha_id"),
            "village_code": body.village_code,
            "local_id": body.local_id,
        },
    )
    return ok(created)


@router.post("/{session_id}/idrs")
async def submit_idrs(
    session_id: str,
    body: IdrsSubmit,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_asha),
):
    session = await session_repo.get_session_raw(db, session_id)
    if session is None:
        raise AppError("Session not found", status_code=404)

    total = _idrs_total(body)
    updated = await session_repo.update_session(
        db,
        session_id,
        {"idrs_score": total, "idrs_detail": body.model_dump(exclude_none=True)},
    )
    return ok(updated)


@router.post("/{session_id}/voice")
async def submit_voice(
    session_id: str,
    body: VoiceFeatures,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_asha),
):
    session = await session_repo.get_session_raw(db, session_id)
    if session is None:
        raise AppError("Session not found", status_code=404)

    # Store only the extracted feature summary — never raw audio.
    detail = body.model_dump(exclude_none=True)
    updated = await session_repo.update_session(
        db,
        session_id,
        {"voice_score": body.voice_score, "voice_detail": detail},
    )
    return ok(updated)


@router.post("/{session_id}/rppg")
async def submit_rppg(
    session_id: str,
    body: RppgFeatures,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_asha),
):
    session = await session_repo.get_session_raw(db, session_id)
    if session is None:
        raise AppError("Session not found", status_code=404)

    updated = await session_repo.update_session(
        db,
        session_id,
        {
            "rppg_hrv_flag": body.hrv_flag,
            "rppg_detail": body.model_dump(exclude_none=True),
        },
    )
    return ok(updated)


@router.get("/{session_id}/result")
async def session_result(
    session_id: str,
    lang: str = Query(default="en"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_any),
):
    session = await session_repo.get_session_raw(db, session_id)
    if session is None:
        raise AppError("Session not found", status_code=404)
    if session.get("idrs_score") is None:
        raise AppError("IDRS questionnaire not yet submitted", status_code=400)

    patient = await patient_repo.get_patient_raw(db, session["patient_id"])
    waist = (patient or {}).get("waist_cm")

    result = evaluate_risk(
        idrs_score=float(session["idrs_score"]),
        voice_score=float(session.get("voice_score") or 0.0),
        rppg_hrv_flag=bool(session.get("rppg_hrv_flag") or False),
        waist_cm=waist,
        dietary_score=(patient or {}).get("dietary_score"),
        occupation_transition_flag=(patient or {}).get("occupation_transition_flag", False),
        family_history_flag=(patient or {}).get("family_history_flag", False),
        lang=lang,
    )

    now = datetime.now(timezone.utc)
    await session_repo.update_session(
        db,
        session_id,
        {
            "tier": result.tier,
            "composite_score": result.composite_score,
            "component_breakdown": result.component_breakdown.model_dump(),
            "prediabetes_trajectory": (
                result.prediabetes_trajectory.model_dump()
                if result.prediabetes_trajectory
                else None
            ),
        },
    )
    await patient_repo.set_last_session(db, session["patient_id"], now, result.tier)

    return ok(result.model_dump())


@router.get("/{session_id}/report")
async def session_report(
    session_id: str,
    lang: str = Query(default="en"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_any),
):
    session = await session_repo.get_session_raw(db, session_id)
    if session is None:
        raise AppError("Session not found", status_code=404)
    if session.get("tier") is None:
        raise AppError("Result not computed yet; call /result first", status_code=400)

    patient = await patient_repo.get_patient_raw(db, session["patient_id"])
    tier = session["tier"]

    # Build a plain-language, localised report.
    headline = translate(f"report_headline_{tier.lower()}", lang)
    explanation = translate(
        f"explain_{tier.lower()}",
        lang,
        score=int(round(session.get("composite_score", 0))),
    )
    next_visit = translate(f"next_visit_{tier.lower()}", lang)

    advice_preview = None
    if tier in ("AMBER", "RED") and patient is not None:
        try:
            adv = diet_engine.get_weekly_advice(
                patient,
                lang=lang,
                tier=tier,
            )
            advice_preview = adv["advice"]
        except Exception:  # noqa: BLE001 — report should still render
            advice_preview = None

    report = {
        "patient_id": session["patient_id"],
        "session_id": session_id,
        "tier": tier,
        "headline": headline,
        "explanation": explanation,
        "advice_preview": advice_preview,
        "next_visit_hint": next_visit,
        "lang": lang,
    }
    return ok(report)

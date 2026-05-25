"""
Diet router.

  GET /diet/advice/{patient_id}?lang=kn&region=karnataka_coastal

Returns localised weekly advice and advances the patient's rotation index so
the next weekly fetch returns the next item. Advice is strictly food-swap /
activity based — never supplements, brands, or paid services.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.deps import require_any
from app.core.responses import AppError, ok
from app.database import get_db
from app.repositories import patient_repo
from app.services import diet_engine

router = APIRouter(prefix="/diet", tags=["diet"])


@router.get("/advice/{patient_id}")
async def diet_advice(
    patient_id: str,
    lang: str = Query(default="en"),
    region: Optional[str] = Query(default=None),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_any),
):
    patient = await patient_repo.get_patient_raw(db, patient_id)
    if patient is None:
        raise AppError("Patient not found", status_code=404)

    advice = diet_engine.get_weekly_advice(
        patient,
        region=region,
        lang=lang,
        tier=patient.get("last_tier"),
    )

    # Advance rotation index for next week.
    await patient_repo.advance_advice_index(db, patient_id, advice["next_index"])

    return ok(
        {
            "patient_id": patient_id,
            "region": advice["region"],
            "profile": advice["profile"],
            "income_band": advice["income_band"],
            "lang": advice["lang"],
            "week_index": advice["week_index"],
            "advice": advice["advice"],
            "tips": advice["tips"],
        }
    )

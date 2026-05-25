"""
Patients router.

  POST /patients/             create patient (ASHA)
  GET  /patients/{id}         fetch profile
  PUT  /patients/{id}         update profile
  GET  /patients/{id}/history all screening sessions
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.deps import require_any, require_asha, require_asha_or_doctor
from app.core.responses import AppError, ok
from app.core.security import hash_phone
from app.database import get_db
from app.repositories import patient_repo, session_repo
from app.schemas.patient import PatientCreate, PatientUpdate

router = APIRouter(prefix="/patients", tags=["patients"])


@router.post("/")
async def create_patient(
    body: PatientCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_asha),
):
    if not body.abha_id and not body.phone:
        raise AppError("Either abha_id or phone is required", status_code=400)

    phone_hash = hash_phone(body.phone) if body.phone else None

    # Dedup: if a patient already exists for this identity, return it.
    if body.abha_id:
        existing = await patient_repo.find_by_abha(db, body.abha_id)
        if existing:
            return ok(patient_repo.serialize_existing(existing))
    if phone_hash:
        existing = await patient_repo.find_by_phone_hash(db, phone_hash)
        if existing:
            return ok(patient_repo.serialize_existing(existing))

    created = await patient_repo.create_patient(
        db,
        {
            "abha_id": body.abha_id,
            "phone_hash": phone_hash,
            "name": body.name,
            "age": body.age,
            "sex": body.sex,
            "village_code": body.village_code,
            "district_code": body.district_code,
            "waist_cm": body.waist_cm,
            "family_history_flag": body.family_history_flag,
            "occupation_transition_flag": body.occupation_transition_flag,
            "preferred_lang": body.preferred_lang,
            "local_id": body.local_id,
        },
    )
    return ok(created)


@router.get("/{patient_id}")
async def get_patient(
    patient_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_any),
):
    patient = await patient_repo.get_patient(db, patient_id)
    if patient is None:
        raise AppError("Patient not found", status_code=404)
    return ok(patient)


@router.put("/{patient_id}")
async def update_patient(
    patient_id: str,
    body: PatientUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_asha_or_doctor),
):
    updated = await patient_repo.update_patient(db, patient_id, body.model_dump())
    if updated is None:
        raise AppError("Patient not found", status_code=404)
    return ok(updated)


@router.get("/{patient_id}/history")
async def patient_history(
    patient_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_any),
):
    patient = await patient_repo.get_patient(db, patient_id)
    if patient is None:
        raise AppError("Patient not found", status_code=404)
    sessions = await session_repo.list_for_patient(db, patient_id)
    return ok({"patient_id": patient_id, "sessions": sessions})

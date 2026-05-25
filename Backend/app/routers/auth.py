"""
Auth router.

  POST /auth/asha/login      {asha_id, pin}            -> JWT (12h)
  POST /auth/patient/otp     {phone}                   -> OTP sent (mock logs)
  POST /auth/patient/verify  {phone, otp}              -> JWT (24h)
  POST /auth/doctor/login    {email, password}         -> JWT (12h)

ASHA PINs and doctor passwords are bcrypt-verified. If no ASHA worker record
exists yet (fresh deployment), a dev-only fallback accepts any 4+ digit PIN so
the workflow can be demonstrated; this is gated on APP_ENV == "dev".
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.core.responses import AppError, ok
from app.core.security import (
    create_access_token,
    generate_otp,
    hash_phone,
    mask_phone,
    verify_password,
)
from app.database import get_db
from app.repositories import misc_repo, patient_repo
from app.schemas.auth import (
    AshaLoginRequest,
    DoctorLoginRequest,
    PatientOtpRequest,
    PatientVerifyRequest,
)
from app.services.notification_service import send_sms

logger = logging.getLogger("novacare.auth")
router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/asha/login")
async def asha_login(body: AshaLoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    worker = await misc_repo.get_asha(db, body.asha_id)

    if worker is not None:
        if not verify_password(body.pin, worker.get("pin_hash", "")):
            raise AppError("Invalid ASHA ID or PIN", status_code=401)
        village_code = worker.get("village_code", "")
    elif settings.APP_ENV == "dev" and len(body.pin) >= 4:
        # Dev fallback so the flow works before workers are seeded.
        logger.warning("Dev ASHA login fallback for %s", body.asha_id)
        village_code = "DEV_VILLAGE"
    else:
        raise AppError("Invalid ASHA ID or PIN", status_code=401)

    token = create_access_token(
        {"asha_id": body.asha_id, "village_code": village_code, "role": "asha"},
        expires_hours=settings.ASHA_TOKEN_EXPIRE_HOURS,
    )
    return ok(
        {
            "access_token": token,
            "token_type": "bearer",
            "role": "asha",
            "expires_in_hours": settings.ASHA_TOKEN_EXPIRE_HOURS,
        }
    )


@router.post("/patient/otp")
async def patient_otp(body: PatientOtpRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    otp = generate_otp()
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=settings.OTP_TTL_SECONDS)
    await misc_repo.store_otp(db, body.phone, otp, expires_at)

    # In dev/mock this just logs; never log the raw phone.
    await send_sms(
        body.phone,
        f"Your NovaCare OTP is {otp}. Valid {settings.OTP_TTL_SECONDS // 60} min.",
        db=db,
    )
    logger.info("OTP issued to %s", mask_phone(body.phone))

    payload = {
        "phone_masked": mask_phone(body.phone),
        "ttl_seconds": settings.OTP_TTL_SECONDS,
    }
    if settings.APP_ENV == "dev":
        payload["dev_otp"] = otp  # convenience for local testing only
    return ok(payload)


@router.post("/patient/verify")
async def patient_verify(body: PatientVerifyRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    record = await misc_repo.fetch_otp(db, body.phone)
    if record is None:
        raise AppError("No OTP requested for this number", status_code=400)

    expires_at = record.get("expires_at")
    if expires_at and expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if expires_at and datetime.now(timezone.utc) > expires_at:
        await misc_repo.clear_otp(db, body.phone)
        raise AppError("OTP expired, please request a new one", status_code=400)

    if record.get("otp") != body.otp:
        raise AppError("Incorrect OTP", status_code=401)

    await misc_repo.clear_otp(db, body.phone)

    # Resolve patient by phone hash (privacy-preserving lookup).
    phash = hash_phone(body.phone)
    patient = await patient_repo.find_by_phone_hash(db, phash)
    if patient is None:
        # Auto-provision a minimal patient record on first verified login.
        created = await patient_repo.create_patient(
            db,
            {
                "abha_id": None,
                "phone_hash": phash,
                "name": None,
                "age": None,
                "sex": None,
                "village_code": "UNKNOWN",
                "district_code": None,
                "waist_cm": None,
                "family_history_flag": False,
                "occupation_transition_flag": False,
                "preferred_lang": "en",
                "local_id": None,
            },
        )
        patient_id = created["id"]
    else:
        patient_id = str(patient["_id"])

    token = create_access_token(
        {"patient_id": patient_id, "role": "patient"},
        expires_hours=settings.PATIENT_TOKEN_EXPIRE_HOURS,
    )
    return ok(
        {
            "access_token": token,
            "token_type": "bearer",
            "role": "patient",
            "expires_in_hours": settings.PATIENT_TOKEN_EXPIRE_HOURS,
        }
    )


@router.post("/doctor/login")
async def doctor_login(body: DoctorLoginRequest, db: AsyncIOMotorDatabase = Depends(get_db)):
    doctor = await misc_repo.get_doctor_by_email(db, body.email)
    if doctor is None or not verify_password(body.password, doctor.get("password_hash", "")):
        raise AppError("Invalid email or password", status_code=401)

    token = create_access_token(
        {
            "doctor_id": str(doctor["_id"]),
            "district_code": doctor.get("district_code", ""),
            "role": "doctor",
        },
        expires_hours=settings.DOCTOR_TOKEN_EXPIRE_HOURS,
    )
    return ok(
        {
            "access_token": token,
            "token_type": "bearer",
            "role": "doctor",
            "expires_in_hours": settings.DOCTOR_TOKEN_EXPIRE_HOURS,
        }
    )

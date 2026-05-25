"""
Follow-up router.

  GET  /followup/due       ASHA's due visit list for today
  POST /followup/complete  mark a visit done
  GET  /followup/missed    patients overdue > 21 days (triggers SMS)

The /missed endpoint is also invoked by the daily APScheduler job
(see app.scheduler). It can be called manually by a doctor/ASHA too.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.core.deps import require_asha, require_asha_or_doctor
from app.core.responses import ok
from app.core.i18n import translate
from app.database import get_db
from app.repositories import patient_repo
from app.schemas.misc import FollowupCompleteRequest
from app.services.notification_service import send_sms

router = APIRouter(prefix="/followup", tags=["followup"])


@router.get("/due")
async def followup_due(
    db: AsyncIOMotorDatabase = Depends(get_db),
    principal=Depends(require_asha),
):
    """Patients in the ASHA's village due for a visit (simple heuristic)."""
    village = principal.get("village_code")
    now = datetime.now(timezone.utc)
    # Anyone non-green whose last session is >= 150 days old (routine) or who
    # is RED/AMBER from the most recent screen.
    cursor = db.patients.find(
        {
            "village_code": village,
            "last_tier": {"$in": ["AMBER", "RED"]},
        }
    )
    docs = await cursor.to_list(length=1000)
    items = [
        {
            "patient_id": str(d["_id"]),
            "name": d.get("name"),
            "village_code": d.get("village_code"),
            "tier": d.get("last_tier"),
            "last_session_date": d.get("last_session_date"),
        }
        for d in docs
    ]
    return ok({"date": now.date().isoformat(), "due": items})


@router.post("/complete")
async def followup_complete(
    body: FollowupCompleteRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    principal=Depends(require_asha_or_doctor),
):
    now = datetime.now(timezone.utc)
    await db.followups.insert_one(
        {
            "patient_id": body.patient_id,
            "session_id": body.session_id,
            "notes": body.notes,
            "completed_by": principal.get("asha_id") or principal.get("doctor_id"),
            "completed_at": now,
        }
    )
    return ok({"patient_id": body.patient_id, "completed_at": now})


@router.get("/missed")
async def followup_missed(
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_asha_or_doctor),
):
    result = await run_missed_followups(db)
    return ok(result)


async def run_missed_followups(db: AsyncIOMotorDatabase) -> dict:
    """
    Core logic, shared by the endpoint and the daily cron.
    Finds patients with last_session_date > MISSED_FOLLOWUP_DAYS days and
    tier != GREEN, then sends a localised re-engagement SMS.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.MISSED_FOLLOWUP_DAYS)
    overdue = await patient_repo.list_overdue(db, cutoff)

    notified = []
    for p in overdue:
        # We only have a phone hash; for the mock gateway we pass a placeholder.
        # In production, an internal lookup service would resolve a sendable
        # channel from the hash + consent registry.
        lang = p.get("preferred_lang", "en")
        message = translate("sms_missed_followup", lang)
        # phone_hash present but not reversible; mock send still records intent.
        await send_sms(
            phone=p.get("phone_hash", "unknown"),
            message=message,
            db=db,
            lang=lang,
        )
        notified.append(str(p["_id"]))

    return {
        "scanned": len(overdue),
        "notified": len(notified),
        "patients": notified,
    }

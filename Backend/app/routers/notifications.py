"""
Notifications router (internal-use endpoints).

  POST /notify/sms   send an SMS via the configured gateway
  POST /notify/push  send an FCM push

These are guarded to ASHA/doctor roles. In production you may restrict them
to an internal service token instead.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.deps import require_asha_or_doctor
from app.core.responses import ok
from app.database import get_db
from app.schemas.misc import PushRequest, SmsRequest
from app.services.notification_service import send_push, send_sms

router = APIRouter(prefix="/notify", tags=["notifications"])


@router.post("/sms")
async def notify_sms(
    body: SmsRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_asha_or_doctor),
):
    result = await send_sms(body.phone, body.message, db=db, lang=body.lang)
    return ok(result.model_dump())


@router.post("/push")
async def notify_push(
    body: PushRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_asha_or_doctor),
):
    result = await send_push(
        body.device_token, body.title, body.body, data=body.data, db=db
    )
    return ok(result.model_dump())

"""
Notification service.

Abstracts SMS (MSG91 / Twilio) and FCM push behind a single interface. In dev,
the provider defaults to `mock`, which logs instead of sending. Every send is
recorded in the `notifications` collection.

Privacy: we log only masked phone numbers, never the raw value.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

import httpx
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.config import settings
from app.core.security import hash_phone, mask_phone
from app.schemas.misc import NotifyResult

logger = logging.getLogger("novacare.notify")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def _record(
    db: Optional[AsyncIOMotorDatabase],
    *,
    channel: str,
    provider: str,
    status: str,
    phone_hash: Optional[str] = None,
    reference: Optional[str] = None,
    meta: Optional[Dict[str, Any]] = None,
) -> None:
    if db is None:
        return
    await db.notifications.insert_one(
        {
            "channel": channel,
            "provider": provider,
            "status": status,
            "phone_hash": phone_hash,
            "reference": reference,
            "meta": meta or {},
            "created_at": _utcnow(),
        }
    )


async def send_sms(
    phone: str,
    message: str,
    *,
    db: Optional[AsyncIOMotorDatabase] = None,
    lang: str = "en",
) -> NotifyResult:
    provider = settings.SMS_PROVIDER
    masked = mask_phone(phone)
    phash = hash_phone(phone)

    if provider == "mock":
        logger.info("[MOCK SMS] to=%s lang=%s msg=%s", masked, lang, message)
        await _record(db, channel="sms", provider="mock", status="sent",
                      phone_hash=phash, reference="mock", meta={"lang": lang})
        return NotifyResult(provider="mock", status="sent", reference="mock")

    try:
        if provider == "msg91":
            ref = await _send_msg91(phone, message)
        elif provider == "twilio":
            ref = await _send_twilio(phone, message)
        else:
            raise ValueError(f"Unknown SMS provider: {provider}")
        await _record(db, channel="sms", provider=provider, status="sent",
                      phone_hash=phash, reference=ref, meta={"lang": lang})
        return NotifyResult(provider=provider, status="sent", reference=ref)
    except Exception as exc:  # noqa: BLE001 — record and surface a clean status
        logger.exception("SMS send failed via %s to %s", provider, masked)
        await _record(db, channel="sms", provider=provider, status="failed",
                      phone_hash=phash, meta={"error": str(exc)})
        return NotifyResult(provider=provider, status="failed", reference=None)


async def _send_msg91(phone: str, message: str) -> str:
    url = "https://api.msg91.com/api/v5/flow/"
    headers = {"authkey": settings.SMS_API_KEY, "Content-Type": "application/json"}
    payload = {
        "sender": settings.SMS_SENDER_ID,
        "short_url": "0",
        "mobiles": phone,
        "message": message,
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("request_id", "msg91"))


async def _send_twilio(phone: str, message: str) -> str:
    # Twilio Messages API (Basic auth: AccountSID:AuthToken in SMS_API_KEY as "sid:token")
    sid, _, token = settings.SMS_API_KEY.partition(":")
    url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
    data = {"To": phone, "From": settings.TWILIO_FROM, "Body": message}
    async with httpx.AsyncClient(timeout=10, auth=(sid, token)) as client:
        resp = await client.post(url, data=data)
        resp.raise_for_status()
        return str(resp.json().get("sid", "twilio"))


async def send_push(
    device_token: str,
    title: str,
    body: str,
    *,
    data: Optional[Dict[str, str]] = None,
    db: Optional[AsyncIOMotorDatabase] = None,
) -> NotifyResult:
    provider = settings.FCM_PROVIDER

    if provider == "mock":
        logger.info("[MOCK FCM] token=%s… title=%s", device_token[:8], title)
        await _record(db, channel="push", provider="mock", status="sent",
                      reference="mock", meta={"title": title})
        return NotifyResult(provider="mock", status="sent", reference="mock")

    try:
        url = "https://fcm.googleapis.com/fcm/send"
        headers = {
            "Authorization": f"key={settings.FCM_SERVER_KEY}",
            "Content-Type": "application/json",
        }
        payload = {
            "to": device_token,
            "notification": {"title": title, "body": body},
            "data": data or {},
        }
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            ref = str(resp.json().get("multicast_id", "fcm"))
        await _record(db, channel="push", provider="fcm", status="sent",
                      reference=ref, meta={"title": title})
        return NotifyResult(provider="fcm", status="sent", reference=ref)
    except Exception as exc:  # noqa: BLE001
        logger.exception("FCM push failed")
        await _record(db, channel="push", provider="fcm", status="failed",
                      meta={"error": str(exc)})
        return NotifyResult(provider="fcm", status="failed", reference=None)

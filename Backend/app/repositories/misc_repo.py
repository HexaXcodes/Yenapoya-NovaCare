"""Campaign, village, and auth (doctor/asha/otp) repositories."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.campaign import campaign_doc, serialize_campaign


def _oid(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


# --------------------------------------------------------------------------- #
# Campaigns
# --------------------------------------------------------------------------- #
async def create_campaign(db: AsyncIOMotorDatabase, fields: Dict[str, Any]) -> Dict[str, Any]:
    doc = campaign_doc(**fields)
    result = await db.campaigns.insert_one(doc)
    created = await db.campaigns.find_one({"_id": result.inserted_id})
    return serialize_campaign(created)


async def list_campaigns(db: AsyncIOMotorDatabase, district_code: str) -> List[Dict[str, Any]]:
    cursor = db.campaigns.find({"district_code": district_code}).sort("created_at", -1)
    docs = await cursor.to_list(length=1000)
    return [serialize_campaign(d) for d in docs]


async def get_campaign(db: AsyncIOMotorDatabase, campaign_id: str) -> Optional[Dict[str, Any]]:
    oid = _oid(campaign_id)
    if oid is None:
        return None
    doc = await db.campaigns.find_one({"_id": oid})
    return serialize_campaign(doc) if doc else None


async def update_campaign(
    db: AsyncIOMotorDatabase, campaign_id: str, fields: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    oid = _oid(campaign_id)
    if oid is None:
        return None
    fields = {k: v for k, v in fields.items() if v is not None}
    if "scheduled_date" in fields and fields["scheduled_date"] is not None:
        fields["scheduled_date"] = fields["scheduled_date"].isoformat()
    fields["updated_at"] = datetime.now(timezone.utc)
    await db.campaigns.update_one({"_id": oid}, {"$set": fields})
    return await get_campaign(db, campaign_id)


# --------------------------------------------------------------------------- #
# Villages (geometry for heatmap)
# --------------------------------------------------------------------------- #
async def get_village(db: AsyncIOMotorDatabase, village_code: str) -> Optional[Dict[str, Any]]:
    return await db.villages.find_one({"village_code": village_code})


async def all_villages(db: AsyncIOMotorDatabase) -> List[Dict[str, Any]]:
    return await db.villages.find({}).to_list(length=20000)


# --------------------------------------------------------------------------- #
# Auth: doctors, asha, otp
# --------------------------------------------------------------------------- #
async def get_doctor_by_email(db: AsyncIOMotorDatabase, email: str) -> Optional[Dict[str, Any]]:
    return await db.doctors.find_one({"email": email.lower()})


async def get_asha(db: AsyncIOMotorDatabase, asha_id: str) -> Optional[Dict[str, Any]]:
    return await db.asha_workers.find_one({"asha_id": asha_id})


async def store_otp(
    db: AsyncIOMotorDatabase, phone: str, otp: str, expires_at: datetime
) -> None:
    await db.otps.update_one(
        {"phone": phone},
        {"$set": {"phone": phone, "otp": otp, "expires_at": expires_at}},
        upsert=True,
    )


async def fetch_otp(db: AsyncIOMotorDatabase, phone: str) -> Optional[Dict[str, Any]]:
    return await db.otps.find_one({"phone": phone})


async def clear_otp(db: AsyncIOMotorDatabase, phone: str) -> None:
    await db.otps.delete_one({"phone": phone})

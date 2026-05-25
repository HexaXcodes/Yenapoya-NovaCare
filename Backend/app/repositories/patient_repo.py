"""Patient repository: all patient-collection queries."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.patient import patient_doc, serialize_patient


def _oid(patient_id: str) -> Optional[ObjectId]:
    try:
        return ObjectId(patient_id)
    except (InvalidId, TypeError):
        return None


async def create_patient(db: AsyncIOMotorDatabase, doc_fields: Dict[str, Any]) -> Dict[str, Any]:
    doc = patient_doc(**doc_fields)
    result = await db.patients.insert_one(doc)
    created = await db.patients.find_one({"_id": result.inserted_id})
    return serialize_patient(created)


async def get_patient_raw(db: AsyncIOMotorDatabase, patient_id: str) -> Optional[Dict[str, Any]]:
    oid = _oid(patient_id)
    if oid is None:
        return None
    return await db.patients.find_one({"_id": oid})


async def get_patient(db: AsyncIOMotorDatabase, patient_id: str) -> Optional[Dict[str, Any]]:
    doc = await get_patient_raw(db, patient_id)
    return serialize_patient(doc) if doc else None


def serialize_existing(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Public wrapper around serialize_patient for routers."""
    return serialize_patient(doc)


async def find_by_phone_hash(db: AsyncIOMotorDatabase, phone_hash: str) -> Optional[Dict[str, Any]]:
    return await db.patients.find_one({"phone_hash": phone_hash})


async def find_by_abha(db: AsyncIOMotorDatabase, abha_id: str) -> Optional[Dict[str, Any]]:
    return await db.patients.find_one({"abha_id": abha_id})


async def update_patient(
    db: AsyncIOMotorDatabase, patient_id: str, fields: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    oid = _oid(patient_id)
    if oid is None:
        return None
    fields = {k: v for k, v in fields.items() if v is not None}
    fields["updated_at"] = datetime.now(timezone.utc)
    await db.patients.update_one({"_id": oid}, {"$set": fields})
    return await get_patient(db, patient_id)


async def advance_advice_index(
    db: AsyncIOMotorDatabase, patient_id: str, next_index: int
) -> None:
    oid = _oid(patient_id)
    if oid is None:
        return
    await db.patients.update_one(
        {"_id": oid}, {"$set": {"last_advice_index": next_index}}
    )


async def set_last_session(
    db: AsyncIOMotorDatabase, patient_id: str, when: datetime, tier: str
) -> None:
    oid = _oid(patient_id)
    if oid is None:
        return
    await db.patients.update_one(
        {"_id": oid},
        {"$set": {"last_session_date": when, "last_tier": tier}},
    )


async def list_overdue(
    db: AsyncIOMotorDatabase, cutoff: datetime
) -> List[Dict[str, Any]]:
    """Patients whose last session is older than cutoff and tier != GREEN."""
    cursor = db.patients.find(
        {
            "last_session_date": {"$lt": cutoff, "$ne": None},
            "last_tier": {"$in": ["AMBER", "RED"]},
        }
    )
    return await cursor.to_list(length=5000)

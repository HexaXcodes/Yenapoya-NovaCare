"""Session repository: screening-session queries."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.models.session import serialize_session, session_doc


def _oid(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


async def create_session(db: AsyncIOMotorDatabase, fields: Dict[str, Any]) -> Dict[str, Any]:
    doc = session_doc(**fields)
    result = await db.sessions.insert_one(doc)
    created = await db.sessions.find_one({"_id": result.inserted_id})
    return serialize_session(created)


async def get_session_raw(db: AsyncIOMotorDatabase, session_id: str) -> Optional[Dict[str, Any]]:
    oid = _oid(session_id)
    if oid is None:
        return None
    return await db.sessions.find_one({"_id": oid})


async def get_session(db: AsyncIOMotorDatabase, session_id: str) -> Optional[Dict[str, Any]]:
    doc = await get_session_raw(db, session_id)
    return serialize_session(doc) if doc else None


async def update_session(
    db: AsyncIOMotorDatabase, session_id: str, fields: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    oid = _oid(session_id)
    if oid is None:
        return None
    fields["updated_at"] = datetime.now(timezone.utc)
    await db.sessions.update_one({"_id": oid}, {"$set": fields})
    return await get_session(db, session_id)


async def list_for_patient(
    db: AsyncIOMotorDatabase, patient_id: str
) -> List[Dict[str, Any]]:
    cursor = db.sessions.find({"patient_id": patient_id}).sort("created_at", -1)
    docs = await cursor.to_list(length=500)
    return [serialize_session(d) for d in docs]


async def aggregate_villages(
    db: AsyncIOMotorDatabase, match: Dict[str, Any]
) -> List[Dict[str, Any]]:
    """
    Group sessions by village_code and compute tier counts + last screening.
    `match` is a pre-built Mongo match stage (filters applied by the service).
    """
    pipeline: List[Dict[str, Any]] = []
    if match:
        pipeline.append({"$match": match})
    pipeline.extend(
        [
            {
                "$group": {
                    "_id": "$village_code",
                    "total": {"$sum": 1},
                    "red": {"$sum": {"$cond": [{"$eq": ["$tier", "RED"]}, 1, 0]}},
                    "amber": {"$sum": {"$cond": [{"$eq": ["$tier", "AMBER"]}, 1, 0]}},
                    "green": {"$sum": {"$cond": [{"$eq": ["$tier", "GREEN"]}, 1, 0]}},
                    "last_screening_date": {"$max": "$created_at"},
                }
            },
            {"$sort": {"red": -1}},
        ]
    )
    return await db.sessions.aggregate(pipeline).to_list(length=10000)


async def time_series(
    db: AsyncIOMotorDatabase, village_code: Optional[str]
) -> List[Dict[str, Any]]:
    match: Dict[str, Any] = {}
    if village_code:
        match["village_code"] = village_code
    pipeline = [
        {"$match": match} if match else {"$match": {}},
        {
            "$group": {
                "_id": {
                    "village_code": "$village_code",
                    "month": {"$dateToString": {"format": "%Y-%m", "date": "$created_at"}},
                },
                "total": {"$sum": 1},
                "red": {"$sum": {"$cond": [{"$eq": ["$tier", "RED"]}, 1, 0]}},
                "amber": {"$sum": {"$cond": [{"$eq": ["$tier", "AMBER"]}, 1, 0]}},
                "green": {"$sum": {"$cond": [{"$eq": ["$tier", "GREEN"]}, 1, 0]}},
            }
        },
        {"$sort": {"_id.month": 1}},
    ]
    return await db.sessions.aggregate(pipeline).to_list(length=10000)

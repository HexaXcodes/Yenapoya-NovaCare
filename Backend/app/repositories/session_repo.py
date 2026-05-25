"""Session repository: screening-session queries targeting SQLite + MongoDB."""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from bson import ObjectId
from bson.errors import InvalidId
from motor.motor_asyncio import AsyncIOMotorDatabase
from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app.database import get_sql_sessionmaker
from app.models.session import serialize_session
from app.models.sqlite_models import SqlPatient, SqlScreening, SqlSyncQueue

logger = logging.getLogger("novacare.repo.session")


def _oid(value: str) -> Optional[ObjectId]:
    try:
        return ObjectId(value)
    except (InvalidId, TypeError):
        return None


def _to_raw_doc(screening: SqlScreening) -> Dict[str, Any]:
    return {
        "_id": ObjectId(screening.cloud_screening_id) if (screening.cloud_screening_id and len(screening.cloud_screening_id) == 24) else screening.local_screening_id,
        "local_id": screening.local_screening_id,
        "patient_id": screening.patient_local_id,
        "asha_id": screening.asha_id,
        "village_code": screening.village_code,
        "idrs_score": screening.idrs_score,
        "voice_score": screening.voice_risk,  # voice_risk maps to voice_score
        "rppg_hrv_flag": screening.rppg_risk,  # rppg_risk maps to rppg_hrv_flag
        "composite_score": screening.composite_risk,  # composite_risk maps to composite_score
        "tier": screening.tier,
        "idrs_detail": screening.idrs_detail,
        "voice_detail": screening.voice_detail,
        "rppg_detail": screening.rppg_detail,
        "component_breakdown": screening.component_breakdown,
        "prediabetes_trajectory": screening.prediabetes_trajectory,
        "created_at": screening.created_at,
        "updated_at": screening.updated_at,
    }


async def _run_sqlite_write(func):
    retries = 5
    backoff = 0.1
    for attempt in range(retries):
        try:
            return await func()
        except OperationalError as e:
            if "locked" in str(e).lower() and attempt < retries - 1:
                await asyncio.sleep(backoff * (2 ** attempt))
                continue
            raise


async def create_session(db: AsyncIOMotorDatabase, fields: Dict[str, Any]) -> Dict[str, Any]:
    local_id = fields.get("local_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    async def _write():
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            # Check if patient exists locally
            patient_id = fields["patient_id"]
            stmt = select(SqlPatient.local_id).where(
                (SqlPatient.local_id == patient_id) | (SqlPatient.cloud_id == patient_id)
            )
            res = await session.execute(stmt)
            patient_local_id = res.scalar() or patient_id

            screening = SqlScreening(
                local_screening_id=local_id,
                cloud_screening_id=None,
                patient_local_id=patient_local_id,
                asha_id=fields.get("asha_id"),
                village_code=fields.get("village_code"),
                idrs_score=None,
                voice_risk=None,
                rppg_risk=None,
                composite_risk=None,
                tier=None,
                idrs_detail=None,
                voice_detail=None,
                rppg_detail=None,
                component_breakdown=None,
                prediabetes_trajectory=None,
                created_at=now,
                updated_at=now,
                sync_status="pending",
                version=1,
                is_deleted=False
            )
            session.add(screening)

            payload = {
                "local_id": screening.local_screening_id,
                "patient_id": screening.patient_local_id,
                "asha_id": screening.asha_id,
                "village_code": screening.village_code,
            }
            queue_item = SqlSyncQueue(
                queue_id=str(uuid.uuid4()),
                entity_type="screening",
                entity_id=local_id,
                operation_type="insert",
                payload_json=json.dumps(payload),
                retry_count=0,
                created_at=now,
                last_attempt_at=None,
                sync_state="pending"
            )
            session.add(queue_item)
            await session.commit()
            return screening

    screening = await _run_sqlite_write(_write)
    return serialize_session(_to_raw_doc(screening))


async def get_session_raw(db: AsyncIOMotorDatabase, session_id: str) -> Optional[Dict[str, Any]]:
    try:
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlScreening).where(
                (SqlScreening.local_screening_id == session_id) | (SqlScreening.cloud_screening_id == session_id)
            )
            res = await session.execute(stmt)
            screening = res.scalar_one_or_none()
            if screening:
                return _to_raw_doc(screening)
    except Exception:
        logger.exception("SQLite read failed in get_session_raw")

    # Fallback to MongoDB
    oid = _oid(session_id)
    if oid is not None:
        doc = await db.sessions.find_one({"_id": oid})
        if doc:
            return doc
    return await db.sessions.find_one({"local_id": session_id})


async def get_session(db: AsyncIOMotorDatabase, session_id: str) -> Optional[Dict[str, Any]]:
    doc = await get_session_raw(db, session_id)
    return serialize_session(doc) if doc else None


async def update_session(
    db: AsyncIOMotorDatabase, session_id: str, fields: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    now = datetime.now(timezone.utc)

    async def _write():
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlScreening).where(
                (SqlScreening.local_screening_id == session_id) | (SqlScreening.cloud_screening_id == session_id)
            )
            res = await session.execute(stmt)
            screening = res.scalar_one_or_none()

            if not screening:
                # Fetch from MongoDB first to populate SQLite
                oid = _oid(session_id)
                mongo_doc = None
                if oid:
                    mongo_doc = await db.sessions.find_one({"_id": oid})
                if not mongo_doc:
                    mongo_doc = await db.sessions.find_one({"local_id": session_id})

                if not mongo_doc:
                    return None

                screening = SqlScreening(
                    local_screening_id=mongo_doc.get("local_id") or str(mongo_doc["_id"]),
                    cloud_screening_id=str(mongo_doc["_id"]),
                    patient_local_id=mongo_doc.get("patient_id"),
                    asha_id=mongo_doc.get("asha_id"),
                    village_code=mongo_doc.get("village_code"),
                    idrs_score=mongo_doc.get("idrs_score"),
                    voice_risk=mongo_doc.get("voice_score"),
                    rppg_risk=mongo_doc.get("rppg_hrv_flag"),
                    composite_risk=mongo_doc.get("composite_score"),
                    tier=mongo_doc.get("tier"),
                    idrs_detail=mongo_doc.get("idrs_detail"),
                    voice_detail=mongo_doc.get("voice_detail"),
                    rppg_detail=mongo_doc.get("rppg_detail"),
                    component_breakdown=mongo_doc.get("component_breakdown"),
                    prediabetes_trajectory=mongo_doc.get("prediabetes_trajectory"),
                    created_at=mongo_doc.get("created_at") or now,
                    updated_at=mongo_doc.get("updated_at") or now,
                    sync_status="synced",
                    version=mongo_doc.get("version", 1),
                    is_deleted=mongo_doc.get("is_deleted", False)
                )
                session.add(screening)

            # Apply updates
            if "idrs_score" in fields:
                screening.idrs_score = fields["idrs_score"]
            if "voice_score" in fields:
                screening.voice_risk = fields["voice_score"]
            if "rppg_hrv_flag" in fields:
                screening.rppg_risk = fields["rppg_hrv_flag"]
            if "composite_score" in fields:
                screening.composite_risk = fields["composite_score"]
            if "tier" in fields:
                screening.tier = fields["tier"]
            if "idrs_detail" in fields:
                screening.idrs_detail = fields["idrs_detail"]
            if "voice_detail" in fields:
                screening.voice_detail = fields["voice_detail"]
            if "rppg_detail" in fields:
                screening.rppg_detail = fields["rppg_detail"]
            if "component_breakdown" in fields:
                screening.component_breakdown = fields["component_breakdown"]
            if "prediabetes_trajectory" in fields:
                screening.prediabetes_trajectory = fields["prediabetes_trajectory"]

            screening.updated_at = now
            screening.sync_status = "pending"
            screening.version += 1

            payload = {k: v for k, v in fields.items() if v is not None}
            payload["local_id"] = screening.local_screening_id
            payload["version"] = screening.version

            queue_item = SqlSyncQueue(
                queue_id=str(uuid.uuid4()),
                entity_type="screening",
                entity_id=screening.local_screening_id,
                operation_type="update",
                payload_json=json.dumps(payload),
                retry_count=0,
                created_at=now,
                last_attempt_at=None,
                sync_state="pending"
            )
            session.add(queue_item)
            await session.commit()
            return screening

    screening = await _run_sqlite_write(_write)
    if not screening:
        return None
    return serialize_session(_to_raw_doc(screening))


async def list_for_patient(
    db: AsyncIOMotorDatabase, patient_id: str
) -> List[Dict[str, Any]]:
    local_patient_id = patient_id
    try:
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlPatient.local_id).where(
                (SqlPatient.local_id == patient_id) | (SqlPatient.cloud_id == patient_id)
            )
            res = await session.execute(stmt)
            local_id_val = res.scalar()
            if local_id_val:
                local_patient_id = local_id_val
    except Exception:
        logger.exception("SQLite read failed in list_for_patient patient lookup")

    sqlite_screenings = []
    try:
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlScreening).where(SqlScreening.patient_local_id == local_patient_id).order_by(SqlScreening.created_at.desc())
            res = await session.execute(stmt)
            sqlite_screenings = [_to_raw_doc(s) for s in res.scalars().all()]
    except Exception:
        logger.exception("SQLite read failed in list_for_patient")

    cursor = db.sessions.find({"patient_id": patient_id}).sort("created_at", -1)
    mongo_screenings = await cursor.to_list(length=500)

    seen_ids = set()
    merged = []
    for s in sqlite_screenings:
        l_id = s.get("local_id")
        if l_id:
            seen_ids.add(l_id)
            merged.append(serialize_session(s))

    for s in mongo_screenings:
        l_id = s.get("local_id") or str(s.get("_id"))
        if l_id not in seen_ids:
            seen_ids.add(l_id)
            merged.append(serialize_session(s))

    def _get_created_at(x):
        dt = x.get("created_at")
        if isinstance(dt, str):
            dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))
        if dt and not dt.tzinfo:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt or datetime.min.replace(tzinfo=timezone.utc)

    merged.sort(key=_get_created_at, reverse=True)
    return merged


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

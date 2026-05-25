"""Patient repository: all patient queries targeting SQLite + MongoDB."""
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
from app.models.patient import serialize_patient
from app.models.sqlite_models import SqlPatient, SqlSyncQueue

logger = logging.getLogger("novacare.repo.patient")


def _oid(patient_id: str) -> Optional[ObjectId]:
    try:
        return ObjectId(patient_id)
    except (InvalidId, TypeError):
        return None


def _to_raw_doc(patient: SqlPatient) -> Dict[str, Any]:
    return {
        "_id": ObjectId(patient.cloud_id) if (patient.cloud_id and len(patient.cloud_id) == 24) else patient.local_id,
        "local_id": patient.local_id,
        "abha_id": patient.abha_id,
        "phone_hash": patient.phone_hash,
        "name": patient.name,
        "age": patient.age,
        "sex": patient.gender,
        "village_code": patient.village,
        "district_code": patient.district_code,
        "waist_cm": patient.waist_cm,
        "family_history_flag": patient.family_history_flag,
        "occupation_transition_flag": patient.occupation_transition_flag,
        "preferred_lang": patient.preferred_lang,
        "last_advice_index": patient.last_advice_index,
        "last_session_date": patient.last_session_date,
        "last_tier": patient.last_tier,
        "created_at": patient.created_at,
        "updated_at": patient.updated_at,
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


async def create_patient(db: AsyncIOMotorDatabase, doc_fields: Dict[str, Any]) -> Dict[str, Any]:
    local_id = doc_fields.get("local_id") or str(uuid.uuid4())
    now = datetime.now(timezone.utc)

    async def _write():
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            patient = SqlPatient(
                local_id=local_id,
                cloud_id=None,
                abha_id=doc_fields.get("abha_id"),
                name=doc_fields.get("name"),
                age=doc_fields.get("age"),
                gender=doc_fields.get("sex"),  # sex mapped to gender
                phone=doc_fields.get("phone"),
                village=doc_fields.get("village_code"),  # village_code mapped to village
                created_at=now,
                updated_at=now,
                sync_status="pending",
                phone_hash=doc_fields.get("phone_hash"),
                district_code=doc_fields.get("district_code"),
                waist_cm=doc_fields.get("waist_cm"),
                family_history_flag=doc_fields.get("family_history_flag", False),
                occupation_transition_flag=doc_fields.get("occupation_transition_flag", False),
                preferred_lang=doc_fields.get("preferred_lang", "en"),
                last_advice_index=0,
                last_session_date=None,
                last_tier=None,
                version=1,
                is_deleted=False
            )
            session.add(patient)

            payload = {
                "abha_id": patient.abha_id,
                "phone_hash": patient.phone_hash,
                "name": patient.name,
                "age": patient.age,
                "sex": patient.gender,
                "village_code": patient.village,
                "district_code": patient.district_code,
                "waist_cm": patient.waist_cm,
                "family_history_flag": patient.family_history_flag,
                "occupation_transition_flag": patient.occupation_transition_flag,
                "preferred_lang": patient.preferred_lang,
                "local_id": patient.local_id
            }
            queue_item = SqlSyncQueue(
                queue_id=str(uuid.uuid4()),
                entity_type="patient",
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
            return patient

    patient = await _run_sqlite_write(_write)
    return serialize_patient(_to_raw_doc(patient))


async def get_patient_raw(db: AsyncIOMotorDatabase, patient_id: str) -> Optional[Dict[str, Any]]:
    try:
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlPatient).where(
                (SqlPatient.local_id == patient_id) | (SqlPatient.cloud_id == patient_id)
            )
            res = await session.execute(stmt)
            patient = res.scalar_one_or_none()
            if patient:
                return _to_raw_doc(patient)
    except Exception:
        logger.exception("SQLite read failed in get_patient_raw")

    # Fallback to MongoDB
    oid = _oid(patient_id)
    if oid is not None:
        doc = await db.patients.find_one({"_id": oid})
        if doc:
            return doc
    return await db.patients.find_one({"local_id": patient_id})


async def get_patient(db: AsyncIOMotorDatabase, patient_id: str) -> Optional[Dict[str, Any]]:
    doc = await get_patient_raw(db, patient_id)
    return serialize_patient(doc) if doc else None


def serialize_existing(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Public wrapper around serialize_patient for routers."""
    return serialize_patient(doc)


async def find_by_phone_hash(db: AsyncIOMotorDatabase, phone_hash: str) -> Optional[Dict[str, Any]]:
    try:
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlPatient).where(SqlPatient.phone_hash == phone_hash)
            res = await session.execute(stmt)
            patient = res.scalar_one_or_none()
            if patient:
                return _to_raw_doc(patient)
    except Exception:
        logger.exception("SQLite read failed in find_by_phone_hash")

    return await db.patients.find_one({"phone_hash": phone_hash})


async def find_by_abha(db: AsyncIOMotorDatabase, abha_id: str) -> Optional[Dict[str, Any]]:
    try:
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlPatient).where(SqlPatient.abha_id == abha_id)
            res = await session.execute(stmt)
            patient = res.scalar_one_or_none()
            if patient:
                return _to_raw_doc(patient)
    except Exception:
        logger.exception("SQLite read failed in find_by_abha")

    return await db.patients.find_one({"abha_id": abha_id})


async def update_patient(
    db: AsyncIOMotorDatabase, patient_id: str, fields: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    now = datetime.now(timezone.utc)

    async def _write():
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlPatient).where(
                (SqlPatient.local_id == patient_id) | (SqlPatient.cloud_id == patient_id)
            )
            res = await session.execute(stmt)
            patient = res.scalar_one_or_none()

            if not patient:
                # Fetch from MongoDB first to populate SQLite
                oid = _oid(patient_id)
                mongo_doc = None
                if oid:
                    mongo_doc = await db.patients.find_one({"_id": oid})
                if not mongo_doc:
                    mongo_doc = await db.patients.find_one({"local_id": patient_id})

                if not mongo_doc:
                    return None

                patient = SqlPatient(
                    local_id=mongo_doc.get("local_id") or str(mongo_doc["_id"]),
                    cloud_id=str(mongo_doc["_id"]),
                    abha_id=mongo_doc.get("abha_id"),
                    name=mongo_doc.get("name"),
                    age=mongo_doc.get("age"),
                    gender=mongo_doc.get("sex"),
                    phone_hash=mongo_doc.get("phone_hash"),
                    village=mongo_doc.get("village_code"),
                    district_code=mongo_doc.get("district_code"),
                    waist_cm=mongo_doc.get("waist_cm"),
                    family_history_flag=mongo_doc.get("family_history_flag", False),
                    occupation_transition_flag=mongo_doc.get("occupation_transition_flag", False),
                    preferred_lang=mongo_doc.get("preferred_lang", "en"),
                    last_advice_index=mongo_doc.get("last_advice_index", 0),
                    last_session_date=mongo_doc.get("last_session_date"),
                    last_tier=mongo_doc.get("last_tier"),
                    created_at=mongo_doc.get("created_at") or now,
                    updated_at=mongo_doc.get("updated_at") or now,
                    sync_status="synced",
                    version=mongo_doc.get("version", 1),
                    is_deleted=mongo_doc.get("is_deleted", False)
                )
                session.add(patient)

            # Apply updates
            if "name" in fields and fields["name"] is not None:
                patient.name = fields["name"]
            if "age" in fields and fields["age"] is not None:
                patient.age = fields["age"]
            if "sex" in fields and fields["sex"] is not None:
                patient.gender = fields["sex"]
            if "village_code" in fields and fields["village_code"] is not None:
                patient.village = fields["village_code"]
            if "district_code" in fields and fields["district_code"] is not None:
                patient.district_code = fields["district_code"]
            if "waist_cm" in fields and fields["waist_cm"] is not None:
                patient.waist_cm = fields["waist_cm"]
            if "family_history_flag" in fields and fields["family_history_flag"] is not None:
                patient.family_history_flag = fields["family_history_flag"]
            if "occupation_transition_flag" in fields and fields["occupation_transition_flag"] is not None:
                patient.occupation_transition_flag = fields["occupation_transition_flag"]
            if "preferred_lang" in fields and fields["preferred_lang"] is not None:
                patient.preferred_lang = fields["preferred_lang"]

            patient.updated_at = now
            patient.sync_status = "pending"
            patient.version += 1

            payload = {k: v for k, v in fields.items() if v is not None}
            payload["local_id"] = patient.local_id
            payload["version"] = patient.version

            queue_item = SqlSyncQueue(
                queue_id=str(uuid.uuid4()),
                entity_type="patient",
                entity_id=patient.local_id,
                operation_type="update",
                payload_json=json.dumps(payload),
                retry_count=0,
                created_at=now,
                last_attempt_at=None,
                sync_state="pending"
            )
            session.add(queue_item)
            await session.commit()
            return patient

    patient = await _run_sqlite_write(_write)
    if not patient:
        return None
    return serialize_patient(_to_raw_doc(patient))


async def advance_advice_index(
    db: AsyncIOMotorDatabase, patient_id: str, next_index: int
) -> None:
    now = datetime.now(timezone.utc)

    async def _write():
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlPatient).where(
                (SqlPatient.local_id == patient_id) | (SqlPatient.cloud_id == patient_id)
            )
            res = await session.execute(stmt)
            patient = res.scalar_one_or_none()
            if patient:
                patient.last_advice_index = next_index
                patient.updated_at = now
                patient.sync_status = "pending"
                patient.version += 1

                queue_item = SqlSyncQueue(
                    queue_id=str(uuid.uuid4()),
                    entity_type="patient",
                    entity_id=patient.local_id,
                    operation_type="update",
                    payload_json=json.dumps({"last_advice_index": next_index, "version": patient.version}),
                    retry_count=0,
                    created_at=now,
                    last_attempt_at=None,
                    sync_state="pending"
                )
                session.add(queue_item)
                await session.commit()

    await _run_sqlite_write(_write)


async def set_last_session(
    db: AsyncIOMotorDatabase, patient_id: str, when: datetime, tier: str
) -> None:
    now = datetime.now(timezone.utc)

    async def _write():
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlPatient).where(
                (SqlPatient.local_id == patient_id) | (SqlPatient.cloud_id == patient_id)
            )
            res = await session.execute(stmt)
            patient = res.scalar_one_or_none()
            if patient:
                patient.last_session_date = when
                patient.last_tier = tier
                patient.updated_at = now
                patient.sync_status = "pending"
                patient.version += 1

                queue_item = SqlSyncQueue(
                    queue_id=str(uuid.uuid4()),
                    entity_type="patient",
                    entity_id=patient.local_id,
                    operation_type="update",
                    payload_json=json.dumps({
                        "last_session_date": when.isoformat(),
                        "last_tier": tier,
                        "version": patient.version
                    }),
                    retry_count=0,
                    created_at=now,
                    last_attempt_at=None,
                    sync_state="pending"
                )
                session.add(queue_item)
                await session.commit()

    await _run_sqlite_write(_write)


async def list_overdue(
    db: AsyncIOMotorDatabase, cutoff: datetime
) -> List[Dict[str, Any]]:
    """Patients whose last session is older than cutoff and tier != GREEN."""
    sqlite_patients = []
    try:
        sessionmaker = get_sql_sessionmaker()
        async with sessionmaker() as session:
            stmt = select(SqlPatient).where(
                SqlPatient.last_session_date < cutoff,
                SqlPatient.last_session_date != None,
                SqlPatient.last_tier.in_(["AMBER", "RED"])
            )
            res = await session.execute(stmt)
            sqlite_patients = [_to_raw_doc(p) for p in res.scalars().all()]
    except Exception:
        logger.exception("SQLite read failed in list_overdue")

    cursor = db.patients.find(
        {
            "last_session_date": {"$lt": cutoff, "$ne": None},
            "last_tier": {"$in": ["AMBER", "RED"]},
        }
    )
    mongo_patients = await cursor.to_list(length=5000)

    seen_ids = set()
    merged = []
    for p in sqlite_patients:
        l_id = p.get("local_id")
        if l_id:
            seen_ids.add(l_id)
            merged.append(p)

    for p in mongo_patients:
        l_id = p.get("local_id") or str(p.get("_id"))
        if l_id not in seen_ids:
            seen_ids.add(l_id)
            merged.append(p)

    return merged

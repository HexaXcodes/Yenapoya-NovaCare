import asyncio
import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, update
from motor.motor_asyncio import AsyncIOMotorDatabase
from bson import ObjectId

from app.config import settings
from app.database import get_sql_sessionmaker, get_db
from app.models.sqlite_models import SqlPatient, SqlScreening, SqlSyncQueue

logger = logging.getLogger("novacare.sync_worker")

_sync_task: Optional[asyncio.Task] = None
_should_run = False


async def start_sync_worker() -> None:
    global _sync_task, _should_run
    if _sync_task is not None:
        return
    _should_run = True
    _sync_task = asyncio.create_task(run_sync_loop())
    logger.info("Background sync worker started (interval: %s seconds)", settings.SYNC_INTERVAL_SECONDS)


async def stop_sync_worker() -> None:
    global _sync_task, _should_run
    _should_run = False
    if _sync_task is not None:
        _sync_task.cancel()
        try:
            await _sync_task
        except asyncio.CancelledError:
            pass
        _sync_task = None
        logger.info("Background sync worker stopped")


async def run_sync_loop() -> None:
    while _should_run:
        try:
            await sync_once()
        except Exception:
            logger.exception("Error in background sync cycle")

        # Sleep in 1-second steps so we can exit quickly on shutdown
        for _ in range(settings.SYNC_INTERVAL_SECONDS):
            if not _should_run:
                break
            await asyncio.sleep(1)


async def sync_once() -> None:
    try:
        sessionmaker = get_sql_sessionmaker()
    except RuntimeError:
        # SQLite not initialized yet
        return

    try:
        db = get_db()
    except RuntimeError:
        # MongoDB not initialized yet
        return

    # Fetch pending/failed queue items
    async with sessionmaker() as session:
        stmt = (
            select(SqlSyncQueue)
            .where(SqlSyncQueue.sync_state.in_(["pending", "failed"]))
            .where(SqlSyncQueue.retry_count < 10)
            .order_by(SqlSyncQueue.created_at.asc())
        )
        result = await session.execute(stmt)
        queue_items = result.scalars().all()

    if not queue_items:
        return

    now = datetime.now(timezone.utc)
    eligible_items = []

    for item in queue_items:
        # Check exponential backoff delay for retried items
        if item.retry_count > 0 and item.last_attempt_at is not None:
            backoff_sec = min(3600, 30 * (2 ** (item.retry_count - 1)))
            item_last_attempt = item.last_attempt_at.replace(tzinfo=timezone.utc) if not item.last_attempt_at.tzinfo else item.last_attempt_at
            if now < item_last_attempt + timedelta(seconds=backoff_sec):
                continue
        eligible_items.append(item)

    if not eligible_items:
        return

    logger.info("Sync started. Queue size: %d eligible out of %d total", len(eligible_items), len(queue_items))
    success_count = 0
    fail_count = 0

    for item in eligible_items:
        try:
            synced = await _sync_queue_item(db, sessionmaker, item)
            if synced:
                success_count += 1
            else:
                fail_count += 1
        except Exception:
            logger.exception("Failed to sync queue item %s (entity_id=%s)", item.queue_id, item.entity_id)
            fail_count += 1
            async with sessionmaker() as session:
                await session.execute(
                    update(SqlSyncQueue)
                    .where(SqlSyncQueue.queue_id == item.queue_id)
                    .values(
                        retry_count=item.retry_count + 1,
                        last_attempt_at=datetime.now(timezone.utc),
                        sync_state="failed"
                    )
                )
                await session.commit()

    logger.info("Sync cycle finished. Successes: %d, Failures/Retries: %d", success_count, fail_count)


async def _sync_queue_item(db: AsyncIOMotorDatabase, sessionmaker, item: SqlSyncQueue) -> bool:
    now = datetime.now(timezone.utc)

    # Set last attempt time and retry count in SQLite
    async with sessionmaker() as session:
        await session.execute(
            update(SqlSyncQueue)
            .where(SqlSyncQueue.queue_id == item.queue_id)
            .values(
                retry_count=item.retry_count + 1,
                last_attempt_at=now
            )
        )
        await session.commit()

    if item.entity_type == "patient":
        return await _sync_patient(db, sessionmaker, item)
    elif item.entity_type == "screening":
        return await _sync_screening(db, sessionmaker, item)
    else:
        logger.warning("Unknown entity type %s in queue item %s", item.entity_type, item.queue_id)
        return False


async def _sync_patient(db: AsyncIOMotorDatabase, sessionmaker, item: SqlSyncQueue) -> bool:
    async with sessionmaker() as session:
        patient = await session.get(SqlPatient, item.entity_id)
        if not patient:
            # Patient record was deleted, mark sync_queue item as synced
            await session.execute(
                update(SqlSyncQueue)
                .where(SqlSyncQueue.queue_id == item.queue_id)
                .values(sync_state="synced")
            )
            await session.commit()
            return True

        doc = {
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
            "last_session_date": patient.last_session_date.replace(tzinfo=timezone.utc) if patient.last_session_date else None,
            "last_tier": patient.last_tier,
            "created_at": patient.created_at.replace(tzinfo=timezone.utc),
            "updated_at": patient.updated_at.replace(tzinfo=timezone.utc),
            "version": patient.version,
            "is_deleted": patient.is_deleted,
            "last_modified_by": "asha"
        }

    # 2. Check if patient exists in MongoDB
    existing = await db.patients.find_one({"local_id": patient.local_id})
    if existing:
        existing_updated = existing.get("updated_at")
        if existing_updated:
            existing_updated = existing_updated.replace(tzinfo=timezone.utc) if not existing_updated.tzinfo else existing_updated
            local_updated = doc["updated_at"]
            
            # If server has newer copy, update SQLite to server fields
            if existing_updated > local_updated:
                logger.info("Conflict: Server copy is newer for patient %s. Local updated to server state.", patient.local_id)
                async with sessionmaker() as session:
                    await session.execute(
                        update(SqlPatient)
                        .where(SqlPatient.local_id == patient.local_id)
                        .values(
                            cloud_id=str(existing["_id"]),
                            abha_id=existing.get("abha_id"),
                            name=existing.get("name"),
                            age=existing.get("age"),
                            gender=existing.get("sex"),
                            phone_hash=existing.get("phone_hash"),
                            village=existing.get("village_code"),
                            district_code=existing.get("district_code"),
                            waist_cm=existing.get("waist_cm"),
                            family_history_flag=existing.get("family_history_flag", False),
                            occupation_transition_flag=existing.get("occupation_transition_flag", False),
                            preferred_lang=existing.get("preferred_lang", "en"),
                            last_advice_index=existing.get("last_advice_index", 0),
                            last_session_date=existing.get("last_session_date"),
                            last_tier=existing.get("last_tier"),
                            created_at=existing.get("created_at"),
                            updated_at=existing.get("updated_at"),
                            sync_status="synced"
                        )
                    )
                    await session.execute(
                        update(SqlSyncQueue)
                        .where(SqlSyncQueue.queue_id == item.queue_id)
                        .values(sync_state="synced")
                    )
                    await session.commit()
                return True

        # SQLite is newer, update MongoDB
        mongo_id = existing["_id"]
        await db.patients.replace_one({"_id": mongo_id}, doc)
        logger.info("Patient %s synced (updated existing)", patient.local_id)
    else:
        # Check duplicates by ABHA ID or phone hash to avoid duplicate inserts
        dup_query = []
        if doc["abha_id"]:
            dup_query.append({"abha_id": doc["abha_id"]})
        if doc["phone_hash"]:
            dup_query.append({"phone_hash": doc["phone_hash"]})

        dup_existing = None
        if dup_query:
            dup_existing = await db.patients.find_one({"$or": dup_query})

        if dup_existing:
            mongo_id = dup_existing["_id"]
            logger.info("Patient %s mapped to existing server record %s via unique field match", patient.local_id, str(mongo_id))
            existing_updated = dup_existing.get("updated_at")
            if existing_updated:
                existing_updated = existing_updated.replace(tzinfo=timezone.utc) if not existing_updated.tzinfo else existing_updated
                
            if existing_updated and existing_updated > doc["updated_at"]:
                # Server is newer
                async with sessionmaker() as session:
                    await session.execute(
                        update(SqlPatient)
                        .where(SqlPatient.local_id == patient.local_id)
                        .values(
                            cloud_id=str(mongo_id),
                            abha_id=dup_existing.get("abha_id"),
                            name=dup_existing.get("name"),
                            age=dup_existing.get("age"),
                            gender=dup_existing.get("sex"),
                            phone_hash=dup_existing.get("phone_hash"),
                            village=dup_existing.get("village_code"),
                            district_code=dup_existing.get("district_code"),
                            waist_cm=dup_existing.get("waist_cm"),
                            family_history_flag=dup_existing.get("family_history_flag", False),
                            occupation_transition_flag=dup_existing.get("occupation_transition_flag", False),
                            preferred_lang=dup_existing.get("preferred_lang", "en"),
                            last_advice_index=dup_existing.get("last_advice_index", 0),
                            last_session_date=dup_existing.get("last_session_date"),
                            last_tier=dup_existing.get("last_tier"),
                            created_at=dup_existing.get("created_at"),
                            updated_at=dup_existing.get("updated_at"),
                            sync_status="synced"
                        )
                    )
                    await session.commit()
            else:
                await db.patients.replace_one({"_id": mongo_id}, doc)
        else:
            # New insert
            res = await db.patients.insert_one(doc)
            mongo_id = res.inserted_id
            logger.info("Patient %s synced (new insert)", patient.local_id)

    # 3. Update SQLite SqlPatient and SqlSyncQueue status
    async with sessionmaker() as session:
        await session.execute(
            update(SqlPatient)
            .where(SqlPatient.local_id == patient.local_id)
            .values(cloud_id=str(mongo_id), sync_status="synced")
        )
        await session.execute(
            update(SqlSyncQueue)
            .where(SqlSyncQueue.queue_id == item.queue_id)
            .values(sync_state="synced")
        )
        await session.commit()

    return True


async def _sync_screening(db: AsyncIOMotorDatabase, sessionmaker, item: SqlSyncQueue) -> bool:
    async with sessionmaker() as session:
        screening = await session.get(SqlScreening, item.entity_id)
        if not screening:
            # Screening was deleted, mark sync_queue item as synced
            await session.execute(
                update(SqlSyncQueue)
                .where(SqlSyncQueue.queue_id == item.queue_id)
                .values(sync_state="synced")
            )
            await session.commit()
            return True

        patient_local_id = screening.patient_local_id
        patient = await session.get(SqlPatient, patient_local_id)

    patient_cloud_id = None
    if patient:
        patient_cloud_id = patient.cloud_id
        if not patient_cloud_id:
            # Sync parent patient first
            async with sessionmaker() as session:
                patient_queue_item = (
                    await session.execute(
                        select(SqlSyncQueue)
                        .where(SqlSyncQueue.entity_type == "patient")
                        .where(SqlSyncQueue.entity_id == patient_local_id)
                        .where(SqlSyncQueue.sync_state != "synced")
                    )
                ).scalars().first()

            if patient_queue_item:
                logger.info("Patient %s needs sync before screening %s", patient_local_id, screening.local_screening_id)
                patient_synced = await _sync_patient(db, sessionmaker, patient_queue_item)
                if not patient_synced:
                    logger.warning("Could not sync patient %s; delaying screening sync", patient_local_id)
                    return False
                
                async with sessionmaker() as session:
                    p = await session.get(SqlPatient, patient_local_id)
                    if p:
                        patient_cloud_id = p.cloud_id
            else:
                # Patient has no sync queue item, but we should sync them on the fly
                from uuid import uuid4
                logger.info("Syncing patient %s on the fly for screening %s", patient_local_id, screening.local_screening_id)
                mock_queue = SqlSyncQueue(
                    queue_id=str(uuid4()),
                    entity_type="patient",
                    entity_id=patient_local_id,
                    operation_type="insert",
                    payload_json="{}",
                    sync_state="pending"
                )
                patient_synced = await _sync_patient(db, sessionmaker, mock_queue)
                if patient_synced:
                    async with sessionmaker() as session:
                        p = await session.get(SqlPatient, patient_local_id)
                        if p:
                            patient_cloud_id = p.cloud_id

    if not patient_cloud_id:
        # Check MongoDB directly for parent patient
        existing_patient = await db.patients.find_one({"local_id": patient_local_id})
        if existing_patient:
            patient_cloud_id = str(existing_patient["_id"])
            async with sessionmaker() as session:
                await session.execute(
                    update(SqlPatient)
                    .where(SqlPatient.local_id == patient_local_id)
                    .values(cloud_id=patient_cloud_id)
                )
                await session.commit()
        else:
            logger.warning("Cannot sync screening %s: patient %s not found on server or locally",
                           screening.local_screening_id, patient_local_id)
            return False

    doc = {
        "local_id": screening.local_screening_id,
        "patient_id": patient_cloud_id,
        "asha_id": screening.asha_id,
        "village_code": screening.village_code,
        "idrs_score": screening.idrs_score,
        "voice_score": screening.voice_risk,
        "rppg_hrv_flag": screening.rppg_risk,
        "composite_score": screening.composite_risk,
        "tier": screening.tier,
        "idrs_detail": screening.idrs_detail,
        "voice_detail": screening.voice_detail,
        "rppg_detail": screening.rppg_detail,
        "component_breakdown": screening.component_breakdown,
        "prediabetes_trajectory": screening.prediabetes_trajectory,
        "created_at": screening.created_at.replace(tzinfo=timezone.utc),
        "updated_at": screening.updated_at.replace(tzinfo=timezone.utc),
        "version": screening.version,
        "is_deleted": screening.is_deleted,
        "last_modified_by": "asha"
    }

    # 4. Check if screening exists in MongoDB
    existing = await db.sessions.find_one({"local_id": screening.local_screening_id})
    if existing:
        existing_updated = existing.get("updated_at")
        if existing_updated:
            existing_updated = existing_updated.replace(tzinfo=timezone.utc) if not existing_updated.tzinfo else existing_updated
            local_updated = doc["updated_at"]

            # If server has newer copy, update SQLite to server fields
            if existing_updated > local_updated:
                logger.info("Conflict: Server copy is newer for screening %s. Local updated to server state.", screening.local_screening_id)
                async with sessionmaker() as session:
                    await session.execute(
                        update(SqlScreening)
                        .where(SqlScreening.local_screening_id == screening.local_screening_id)
                        .values(
                            cloud_screening_id=str(existing["_id"]),
                            idrs_score=existing.get("idrs_score"),
                            voice_risk=existing.get("voice_score"),
                            rppg_risk=existing.get("rppg_hrv_flag"),
                            composite_risk=existing.get("composite_score"),
                            tier=existing.get("tier"),
                            idrs_detail=existing.get("idrs_detail"),
                            voice_detail=existing.get("voice_detail"),
                            rppg_detail=existing.get("rppg_detail"),
                            component_breakdown=existing.get("component_breakdown"),
                            prediabetes_trajectory=existing.get("prediabetes_trajectory"),
                            created_at=existing.get("created_at"),
                            updated_at=existing.get("updated_at"),
                            sync_status="synced"
                        )
                    )
                    await session.execute(
                        update(SqlSyncQueue)
                        .where(SqlSyncQueue.queue_id == item.queue_id)
                        .values(sync_state="synced")
                    )
                    await session.commit()
                return True

        # SQLite is newer, replace in MongoDB
        mongo_id = existing["_id"]
        await db.sessions.replace_one({"_id": mongo_id}, doc)
        logger.info("Screening %s synced (updated existing)", screening.local_screening_id)
    else:
        # New insert
        res = await db.sessions.insert_one(doc)
        mongo_id = res.inserted_id
        logger.info("Screening %s synced (new insert)", screening.local_screening_id)

    # 5. Update SQLite screening and sync_queue status
    async with sessionmaker() as session:
        await session.execute(
            update(SqlScreening)
            .where(SqlScreening.local_screening_id == screening.local_screening_id)
            .values(cloud_screening_id=str(mongo_id), sync_status="synced")
        )
        await session.execute(
            update(SqlSyncQueue)
            .where(SqlSyncQueue.queue_id == item.queue_id)
            .values(sync_state="synced")
        )
        await session.commit()

    return True

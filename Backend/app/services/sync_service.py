"""
Sync service.

Devices run offline-first against SQLite and push a queue of pending records
when connectivity returns. Each record is upserted into MongoDB keyed by
`local_id`.

Conflict resolution policy:
  * Doctor edits  -> server wins  (clinical authority)
  * ASHA field data -> device wins (freshest ground truth)

A conflict is recorded whenever an incoming record collides with an existing
server record that was last modified by the *other* authority.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List

from motor.motor_asyncio import AsyncIOMotorDatabase

from app.schemas.misc import (
    SyncConflict,
    SyncPullResult,
    SyncPushResult,
    SyncRecord,
)

logger = logging.getLogger("novacare.sync")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _coerce_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str):
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return _utcnow()
    return _utcnow()


async def push_records(
    db: AsyncIOMotorDatabase,
    asha_id: str,
    records: List[SyncRecord],
) -> SyncPushResult:
    synced = 0
    conflicts: List[SyncConflict] = []

    for rec in records:
        coll = db[rec.collection]
        incoming_updated = _coerce_dt(rec.updated_at) if rec.updated_at else _utcnow()

        existing = await coll.find_one({"local_id": rec.local_id})

        if existing is None:
            # New record — straightforward insert.
            payload = dict(rec.payload)
            payload["local_id"] = rec.local_id
            payload["last_modified_by"] = rec.source
            payload["updated_at"] = incoming_updated
            payload.setdefault("created_at", incoming_updated)
            await coll.insert_one(payload)
            synced += 1
            continue

        existing_source = existing.get("last_modified_by", "asha")

        # Decide the winner per policy.
        resolution = _resolve(existing_source, rec.source)

        if resolution == "device_wins":
            update = dict(rec.payload)
            update["last_modified_by"] = rec.source
            update["updated_at"] = incoming_updated
            await coll.update_one(
                {"local_id": rec.local_id},
                {"$set": update},
            )
            synced += 1
            if existing_source != rec.source:
                conflicts.append(
                    SyncConflict(
                        local_id=rec.local_id,
                        collection=rec.collection,
                        reason=f"server copy last edited by {existing_source}",
                        resolution="device_wins",
                    )
                )
        else:  # server_wins
            conflicts.append(
                SyncConflict(
                    local_id=rec.local_id,
                    collection=rec.collection,
                    reason="server holds an authoritative doctor edit",
                    resolution="server_wins",
                )
            )

    # Update the sync cursor so /pull knows what's new.
    await db.sync_cursors.update_one(
        {"asha_id": asha_id},
        {"$set": {"asha_id": asha_id, "last_push_at": _utcnow()}},
        upsert=True,
    )

    logger.info("sync push asha=%s synced=%d conflicts=%d", asha_id, synced, len(conflicts))
    return SyncPushResult(synced=synced, conflicts=conflicts)


def _resolve(existing_source: str, incoming_source: str) -> str:
    """
    Policy table:
      incoming doctor  -> server side (doctor) authority => server_wins unless
                          incoming is also doctor (then accept => device_wins).
      incoming asha    -> device wins UNLESS server copy was a doctor edit.
    """
    if existing_source == "doctor" and incoming_source == "asha":
        # Doctor's clinical edit must not be clobbered by field data.
        return "server_wins"
    # Doctor incoming, or asha-on-asha, or doctor-on-doctor: accept incoming.
    return "device_wins"


async def pull_updates(
    db: AsyncIOMotorDatabase,
    asha_id: str,
    since: datetime | None,
) -> SyncPullResult:
    query: Dict[str, Any] = {}
    if since is not None:
        query["updated_at"] = {"$gt": since}

    # ASHA pulls records relevant to them (their own pushes + doctor edits).
    patients = await db.patients.find(query).to_list(length=1000)
    sessions = await db.sessions.find(query).to_list(length=2000)

    def _clean(doc: Dict[str, Any]) -> Dict[str, Any]:
        d = dict(doc)
        d["id"] = str(d.pop("_id", None))
        # Never push raw PII back down; phone_hash stays, raw phone never stored.
        d.pop("phone", None)
        return d

    return SyncPullResult(
        asha_id=asha_id,
        since=since,
        patients=[_clean(p) for p in patients],
        sessions=[_clean(s) for s in sessions],
        server_time=_utcnow(),
    )

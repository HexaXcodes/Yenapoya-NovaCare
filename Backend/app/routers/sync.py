"""
Sync router.

  POST /sync/push            ASHA app pushes its SQLite queue when online
  GET  /sync/pull/{asha_id}  pull server-side updates since last sync

Push upserts each record into MongoDB by local_id and returns
{synced, conflicts}. Pull returns records changed since the `since` timestamp.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.deps import require_asha
from app.core.responses import AppError, ok
from app.database import get_db
from app.schemas.misc import SyncPushRequest
from app.services import sync_service

router = APIRouter(prefix="/sync", tags=["sync"])


@router.post("/push")
async def sync_push(
    body: SyncPushRequest,
    db: AsyncIOMotorDatabase = Depends(get_db),
    principal=Depends(require_asha),
):
    # The token's asha_id is authoritative; ignore any spoofed body value.
    asha_id = principal.get("asha_id", body.asha_id)
    result = await sync_service.push_records(db, asha_id, body.records)
    return ok(result.model_dump())


@router.get("/pull/{asha_id}")
async def sync_pull(
    asha_id: str,
    since: str | None = Query(default=None, description="ISO timestamp"),
    db: AsyncIOMotorDatabase = Depends(get_db),
    principal=Depends(require_asha),
):
    if principal.get("asha_id") != asha_id:
        raise AppError("Cannot pull another worker's data", status_code=403)

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise AppError("Invalid 'since' timestamp", status_code=400)

    result = await sync_service.pull_updates(db, asha_id, since_dt)
    return ok(result.model_dump())

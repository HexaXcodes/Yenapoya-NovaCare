"""
Campaigns router (doctor / district officer).

  POST /campaigns/              create outreach campaign
  GET  /campaigns/              list campaigns for the doctor's district
  PUT  /campaigns/{id}          update campaign
  GET  /campaigns/{id}/coverage villages + ASHA assignments
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.deps import require_doctor
from app.core.responses import AppError, ok
from app.database import get_db
from app.repositories import misc_repo
from app.schemas.misc import CampaignCreate, CampaignUpdate

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.post("/")
async def create_campaign(
    body: CampaignCreate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    principal=Depends(require_doctor),
):
    created = await misc_repo.create_campaign(
        db,
        {
            "name": body.name,
            "district_code": body.district_code,
            "village_codes": body.village_codes,
            "asha_assignments": body.asha_assignments,
            "scheduled_date": body.scheduled_date,
            "notes": body.notes,
            "created_by": principal.get("doctor_id"),
        },
    )
    return ok(created)


@router.get("/")
async def list_campaigns(
    district: str = Query(default=None),
    db: AsyncIOMotorDatabase = Depends(get_db),
    principal=Depends(require_doctor),
):
    # Default to the doctor's own district from the token.
    district_code = district or principal.get("district_code")
    if not district_code:
        raise AppError("district is required", status_code=400)
    items = await misc_repo.list_campaigns(db, district_code)
    return ok({"district_code": district_code, "campaigns": items})


@router.put("/{campaign_id}")
async def update_campaign(
    campaign_id: str,
    body: CampaignUpdate,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_doctor),
):
    updated = await misc_repo.update_campaign(db, campaign_id, body.model_dump())
    if updated is None:
        raise AppError("Campaign not found", status_code=404)
    return ok(updated)


@router.get("/{campaign_id}/coverage")
async def campaign_coverage(
    campaign_id: str,
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_doctor),
):
    campaign = await misc_repo.get_campaign(db, campaign_id)
    if campaign is None:
        raise AppError("Campaign not found", status_code=404)

    assignments = campaign.get("asha_assignments", {})
    covered = set(campaign.get("village_codes", []))
    assigned = {v for villages in assignments.values() for v in villages}
    unassigned = sorted(covered - assigned)

    return ok(
        {
            "campaign_id": campaign_id,
            "village_codes": campaign.get("village_codes", []),
            "asha_assignments": assignments,
            "unassigned_villages": unassigned,
            "asha_count": len(assignments),
        }
    )

"""
Heatmap router (doctor / district officer).

  GET /heatmap/villages   GeoJSON FeatureCollection of village risk aggregates
  GET /heatmap/trends     time-series risk trend per village

Filters (query params): district, tier, age_min, age_max, condition, from_date.
Each Feature carries properties:
  {village_code, name, risk_density, tier_counts, total_screened,
   red_pct, amber_pct, green_pct, last_screening_date}

Village geometry comes from the `villages` collection. If a village has no
stored geometry, we emit a Point at its stored centroid (or [0,0] as a last
resort) so the frontend can still place a marker.
"""
from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Query
from motor.motor_asyncio import AsyncIOMotorDatabase

from app.core.deps import require_doctor
from app.core.responses import ok
from app.database import get_db
from app.repositories import misc_repo, session_repo

router = APIRouter(prefix="/heatmap", tags=["heatmap"])


def _build_match(
    district: Optional[str],
    tier: Optional[str],
    age_min: Optional[int],
    age_max: Optional[int],
    condition: Optional[str],
    from_date: Optional[date],
    to_date: Optional[date],
) -> Dict[str, Any]:
    match: Dict[str, Any] = {}
    if district:
        match["district_code"] = district
    if tier:
        match["tier"] = tier.upper()
    if condition:
        match["condition"] = condition
    # Age filters require sessions to carry patient age (denormalised at write).
    age_q: Dict[str, Any] = {}
    if age_min is not None:
        age_q["$gte"] = age_min
    if age_max is not None:
        age_q["$lte"] = age_max
    if age_q:
        match["patient_age"] = age_q
    # Date range on created_at.
    date_q: Dict[str, Any] = {}
    if from_date:
        date_q["$gte"] = datetime.combine(from_date, time.min, tzinfo=timezone.utc)
    if to_date:
        date_q["$lte"] = datetime.combine(to_date, time.max, tzinfo=timezone.utc)
    if date_q:
        match["created_at"] = date_q
    return match


def _pct(part: int, whole: int) -> float:
    return round((part / whole) * 100, 1) if whole else 0.0


@router.get("/villages")
async def heatmap_villages(
    district: Optional[str] = Query(default=None),
    tier: Optional[str] = Query(default=None),
    age_min: Optional[int] = Query(default=None),
    age_max: Optional[int] = Query(default=None),
    condition: Optional[str] = Query(default=None),
    from_date: Optional[date] = Query(default=None),
    to_date: Optional[date] = Query(default=None),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_doctor),
):
    match = _build_match(district, tier, age_min, age_max, condition, from_date, to_date)
    aggregates = await session_repo.aggregate_villages(db, match)

    # Index village geometry by code for quick lookup.
    villages = await misc_repo.all_villages(db)
    geo_by_code = {v.get("village_code"): v for v in villages}

    features = []
    for agg in aggregates:
        code = agg["_id"]
        total = agg.get("total", 0)
        red = agg.get("red", 0)
        amber = agg.get("amber", 0)
        green = agg.get("green", 0)
        # Risk density weights RED heavier than AMBER.
        risk_density = round((red * 1.0 + amber * 0.5) / total, 3) if total else 0.0

        vmeta = geo_by_code.get(code, {})
        geometry = vmeta.get("geometry")
        if geometry is None:
            centroid = vmeta.get("centroid") or [0.0, 0.0]
            geometry = {"type": "Point", "coordinates": centroid}

        features.append(
            {
                "type": "Feature",
                "geometry": geometry,
                "properties": {
                    "village_code": code,
                    "name": vmeta.get("name", code),
                    "risk_density": risk_density,
                    "tier_counts": {"red": red, "amber": amber, "green": green},
                    "total_screened": total,
                    "red_pct": _pct(red, total),
                    "amber_pct": _pct(amber, total),
                    "green_pct": _pct(green, total),
                    "last_screening_date": agg.get("last_screening_date"),
                },
            }
        )

    feature_collection = {"type": "FeatureCollection", "features": features}
    return ok(feature_collection)


@router.get("/trends")
async def heatmap_trends(
    village_code: Optional[str] = Query(default=None),
    db: AsyncIOMotorDatabase = Depends(get_db),
    _principal=Depends(require_doctor),
):
    series = await session_repo.time_series(db, village_code)
    points = [
        {
            "village_code": row["_id"]["village_code"],
            "month": row["_id"]["month"],
            "total": row.get("total", 0),
            "red": row.get("red", 0),
            "amber": row.get("amber", 0),
            "green": row.get("green", 0),
        }
        for row in series
    ]
    return ok({"trends": points})

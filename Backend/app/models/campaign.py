"""MongoDB campaign document helpers."""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def campaign_doc(
    *,
    name: str,
    district_code: str,
    village_codes: List[str],
    asha_assignments: Dict[str, List[str]],
    scheduled_date: Optional[date],
    notes: Optional[str],
    created_by: Optional[str],
) -> Dict[str, Any]:
    now = utcnow()
    return {
        "name": name,
        "district_code": district_code,
        "village_codes": village_codes,
        "asha_assignments": asha_assignments,
        "scheduled_date": scheduled_date.isoformat() if scheduled_date else None,
        "status": "draft",
        "notes": notes,
        "created_by": created_by,
        "created_at": now,
        "updated_at": now,
    }


def serialize_campaign(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return {}
    return {
        "id": str(doc.get("_id")),
        "name": doc.get("name"),
        "district_code": doc.get("district_code"),
        "village_codes": doc.get("village_codes", []),
        "asha_assignments": doc.get("asha_assignments", {}),
        "scheduled_date": doc.get("scheduled_date"),
        "status": doc.get("status", "draft"),
        "notes": doc.get("notes"),
        "created_at": doc.get("created_at"),
    }

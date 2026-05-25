"""MongoDB session document helpers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def session_doc(
    *,
    patient_id: str,
    asha_id: Optional[str],
    village_code: str,
    local_id: Optional[str],
) -> Dict[str, Any]:
    now = utcnow()
    return {
        "patient_id": patient_id,
        "asha_id": asha_id,
        "village_code": village_code,
        "local_id": local_id,
        # Filled progressively by /idrs, /voice, /rppg, then /result.
        "idrs_score": None,
        "voice_score": None,
        "rppg_hrv_flag": None,
        "idrs_detail": None,
        "voice_detail": None,
        "rppg_detail": None,
        "tier": None,
        "composite_score": None,
        "component_breakdown": None,
        "prediabetes_trajectory": None,
        "created_at": now,
        "updated_at": now,
    }


def serialize_session(doc: Dict[str, Any]) -> Dict[str, Any]:
    if not doc:
        return {}
    out = dict(doc)
    out["id"] = str(out.pop("_id", None))
    return out

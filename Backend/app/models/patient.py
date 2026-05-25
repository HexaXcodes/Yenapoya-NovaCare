"""
MongoDB document shapes (helpers, not ORM).

Motor returns plain dicts; these helpers centralise field names and provide
serialisation that strips PII (no raw phone numbers leave the DB layer).
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def patient_doc(
    *,
    abha_id: Optional[str],
    phone_hash: Optional[str],
    name: Optional[str],
    age: Optional[int],
    sex: Optional[str],
    village_code: str,
    district_code: Optional[str],
    waist_cm: Optional[float],
    family_history_flag: bool,
    occupation_transition_flag: bool,
    preferred_lang: str,
    local_id: Optional[str],
) -> Dict[str, Any]:
    now = utcnow()
    return {
        "abha_id": abha_id,
        "phone_hash": phone_hash,
        "name": name,
        "age": age,
        "sex": sex,
        "village_code": village_code,
        "district_code": district_code,
        "waist_cm": waist_cm,
        "family_history_flag": family_history_flag,
        "occupation_transition_flag": occupation_transition_flag,
        "preferred_lang": preferred_lang,
        "local_id": local_id,
        "last_advice_index": 0,
        "last_session_date": None,
        "created_at": now,
        "updated_at": now,
    }


def serialize_patient(doc: Dict[str, Any]) -> Dict[str, Any]:
    """Convert a Mongo patient doc into an API-safe dict (no raw PII)."""
    if not doc:
        return {}
    return {
        "id": str(doc.get("_id")),
        "abha_id": doc.get("abha_id"),
        # phone_hash is internal; expose only a masked placeholder if present.
        "phone_masked": "****" + "" if doc.get("phone_hash") else None,
        "name": doc.get("name"),
        "age": doc.get("age"),
        "sex": doc.get("sex"),
        "village_code": doc.get("village_code"),
        "district_code": doc.get("district_code"),
        "waist_cm": doc.get("waist_cm"),
        "family_history_flag": doc.get("family_history_flag", False),
        "occupation_transition_flag": doc.get("occupation_transition_flag", False),
        "preferred_lang": doc.get("preferred_lang", "en"),
        "last_advice_index": doc.get("last_advice_index", 0),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }

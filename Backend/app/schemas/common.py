"""
Shared Pydantic v2 helpers, including a MongoDB ObjectId type that
serialises cleanly to/from strings.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Any, Dict, Optional

from bson import ObjectId
from pydantic import BaseModel, BeforeValidator, Field, PlainSerializer

__all__ = ["PyObjectId", "SyncBaseModel", "PatientSyncModel", "ScreeningSyncModel"]


def _validate_object_id(v: Any) -> str:
    if isinstance(v, ObjectId):
        return str(v)
    if isinstance(v, str):
        # Accept both raw 24-char hex and already-stringified ids.
        return v
    raise ValueError("Invalid ObjectId")


# A string field that tolerates ObjectId input and always serialises to str.
PyObjectId = Annotated[
    str,
    BeforeValidator(_validate_object_id),
    PlainSerializer(lambda x: str(x), return_type=str),
]


class SyncBaseModel(BaseModel):
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = Field(default=1)
    is_deleted: bool = Field(default=False)


class PatientSyncModel(SyncBaseModel):
    local_id: str
    cloud_id: Optional[str] = None
    abha_id: Optional[str] = None
    name: Optional[str] = None
    age: Optional[int] = None
    gender: Optional[str] = None  # maps to sex
    phone: Optional[str] = None
    village: Optional[str] = None  # maps to village_code
    sync_status: str = "pending"

    # additional fields from patient to avoid losing data
    phone_hash: Optional[str] = None
    district_code: Optional[str] = None
    waist_cm: Optional[float] = None
    family_history_flag: bool = False
    occupation_transition_flag: bool = False
    preferred_lang: str = "en"
    last_advice_index: int = 0
    last_session_date: Optional[datetime] = None
    last_tier: Optional[str] = None


class ScreeningSyncModel(SyncBaseModel):
    local_screening_id: str
    cloud_screening_id: Optional[str] = None
    patient_local_id: str
    idrs_score: Optional[float] = None
    voice_risk: Optional[float] = None  # maps to voice_score
    rppg_risk: Optional[bool] = None  # maps to rppg_hrv_flag
    composite_risk: Optional[float] = None  # maps to composite_score
    sync_status: str = "pending"

    # additional fields from screening
    asha_id: Optional[str] = None
    village_code: Optional[str] = None
    tier: Optional[str] = None
    idrs_detail: Optional[Dict[str, Any]] = None
    voice_detail: Optional[Dict[str, Any]] = None
    rppg_detail: Optional[Dict[str, Any]] = None
    component_breakdown: Optional[Dict[str, Any]] = None
    prediabetes_trajectory: Optional[Dict[str, Any]] = None

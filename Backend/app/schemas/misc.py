"""Diet, follow-up, heatmap, campaign, sync, notification, and TTS schemas."""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --------------------------------------------------------------------------- #
# Diet
# --------------------------------------------------------------------------- #
class DietAdviceOut(BaseModel):
    patient_id: str
    region: str
    profile: str  # e.g. prediabetic
    income_band: str  # low_income | mid_income
    lang: str
    week_index: int
    advice: str
    tips: List[str]


# --------------------------------------------------------------------------- #
# Follow-up
# --------------------------------------------------------------------------- #
class FollowupDueItem(BaseModel):
    patient_id: str
    name: Optional[str] = None
    village_code: str
    tier: Optional[str] = None
    last_session_date: Optional[datetime] = None
    due_date: Optional[datetime] = None


class FollowupCompleteRequest(BaseModel):
    patient_id: str
    session_id: Optional[str] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class MissedRunResult(BaseModel):
    scanned: int
    notified: int
    patients: List[str]


# --------------------------------------------------------------------------- #
# Heatmap
# --------------------------------------------------------------------------- #
class HeatmapFilters(BaseModel):
    district: Optional[str] = None
    tier: Optional[str] = None
    age_min: Optional[int] = None
    age_max: Optional[int] = None
    condition: Optional[str] = None
    from_date: Optional[date] = None
    to_date: Optional[date] = None


# --------------------------------------------------------------------------- #
# Campaigns
# --------------------------------------------------------------------------- #
class CampaignCreate(BaseModel):
    name: str = Field(..., max_length=120)
    district_code: str = Field(..., max_length=32)
    village_codes: List[str] = Field(default_factory=list)
    asha_assignments: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="map of asha_id -> list of village_codes",
    )
    scheduled_date: Optional[date] = None
    notes: Optional[str] = Field(default=None, max_length=1000)


class CampaignUpdate(BaseModel):
    name: Optional[str] = None
    village_codes: Optional[List[str]] = None
    asha_assignments: Optional[Dict[str, List[str]]] = None
    scheduled_date: Optional[date] = None
    status: Optional[str] = Field(default=None, pattern="^(draft|active|completed|cancelled)$")
    notes: Optional[str] = None


class CampaignOut(BaseModel):
    id: str
    name: str
    district_code: str
    village_codes: List[str]
    asha_assignments: Dict[str, List[str]]
    scheduled_date: Optional[date] = None
    status: str = "draft"
    notes: Optional[str] = None
    created_at: Optional[datetime] = None


# --------------------------------------------------------------------------- #
# Sync
# --------------------------------------------------------------------------- #
class SyncRecord(BaseModel):
    local_id: str = Field(..., max_length=64)
    collection: str = Field(..., pattern="^(patients|sessions)$")
    payload: Dict[str, Any]
    source: str = Field(default="asha", pattern="^(asha|doctor)$")
    updated_at: Optional[datetime] = None


class SyncPushRequest(BaseModel):
    asha_id: str
    records: List[SyncRecord]


class SyncConflict(BaseModel):
    local_id: str
    collection: str
    reason: str
    resolution: str  # server_wins | device_wins


class SyncPushResult(BaseModel):
    synced: int
    conflicts: List[SyncConflict]


class SyncPullResult(BaseModel):
    asha_id: str
    since: Optional[datetime] = None
    patients: List[dict]
    sessions: List[dict]
    server_time: datetime


# --------------------------------------------------------------------------- #
# Notifications
# --------------------------------------------------------------------------- #
class SmsRequest(BaseModel):
    phone: str = Field(..., min_length=8, max_length=15)
    message: str = Field(..., max_length=480)
    lang: str = "en"


class PushRequest(BaseModel):
    device_token: str
    title: str = Field(..., max_length=120)
    body: str = Field(..., max_length=480)
    data: Optional[Dict[str, str]] = None


class NotifyResult(BaseModel):
    provider: str
    status: str
    reference: Optional[str] = None


# --------------------------------------------------------------------------- #
# TTS
# --------------------------------------------------------------------------- #
class TtsRequest(BaseModel):
    text: str = Field(..., max_length=2000)
    lang: str = Field(default="en", max_length=5)


class TtsResult(BaseModel):
    provider: str
    lang: str
    audio_base64: Optional[str] = None
    audio_url: Optional[str] = None
    cached: bool = False

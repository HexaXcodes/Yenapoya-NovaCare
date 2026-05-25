"""Patient schemas (Pydantic v2)."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel, Field


class PatientCreate(BaseModel):
    # Identity: prefer ABHA; otherwise a phone (hashed before storage).
    abha_id: Optional[str] = Field(default=None, max_length=64)
    phone: Optional[str] = Field(default=None, min_length=8, max_length=15)

    name: Optional[str] = Field(default=None, max_length=120)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    sex: Optional[str] = Field(default=None, pattern="^(male|female|other)$")
    village_code: str = Field(..., max_length=32)
    district_code: Optional[str] = Field(default=None, max_length=32)

    # Health context used by the prediabetes trajectory model.
    waist_cm: Optional[float] = Field(default=None, ge=30, le=200)
    family_history_flag: bool = False
    occupation_transition_flag: bool = False

    preferred_lang: str = Field(default="en", max_length=5)
    # Device-assigned local id for offline-first sync.
    local_id: Optional[str] = Field(default=None, max_length=64)


class PatientUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=120)
    age: Optional[int] = Field(default=None, ge=0, le=120)
    sex: Optional[str] = Field(default=None, pattern="^(male|female|other)$")
    village_code: Optional[str] = Field(default=None, max_length=32)
    district_code: Optional[str] = Field(default=None, max_length=32)
    waist_cm: Optional[float] = Field(default=None, ge=30, le=200)
    family_history_flag: Optional[bool] = None
    occupation_transition_flag: Optional[bool] = None
    preferred_lang: Optional[str] = Field(default=None, max_length=5)


class PatientOut(BaseModel):
    id: str
    abha_id: Optional[str] = None
    phone_masked: Optional[str] = None
    name: Optional[str] = None
    age: Optional[int] = None
    sex: Optional[str] = None
    village_code: str
    district_code: Optional[str] = None
    waist_cm: Optional[float] = None
    family_history_flag: bool = False
    occupation_transition_flag: bool = False
    preferred_lang: str = "en"
    last_advice_index: int = 0
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class PatientHistoryOut(BaseModel):
    patient_id: str
    sessions: List[dict]

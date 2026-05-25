"""
SQLAlchemy (async) models for the SQLite offline-first local storage.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SqlPatient(Base):
    __tablename__ = "patients"

    local_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cloud_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    abha_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    age: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gender: Mapped[str | None] = mapped_column(String(16), nullable=True)  # maps to sex
    phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    village: Mapped[str] = mapped_column(String(32), index=True)  # maps to village_code
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    sync_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    # Extra fields to prevent data loss and map fully to Pydantic Patient model
    phone_hash: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    district_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    waist_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    family_history_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    occupation_transition_flag: Mapped[bool] = mapped_column(Boolean, default=False)
    preferred_lang: Mapped[str] = mapped_column(String(8), default="en")
    last_advice_index: Mapped[int] = mapped_column(Integer, default=0)
    last_session_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_tier: Mapped[str | None] = mapped_column(String(16), nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class SqlScreening(Base):
    __tablename__ = "screenings"

    local_screening_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    cloud_screening_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    patient_local_id: Mapped[str] = mapped_column(String(64), index=True)
    idrs_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    voice_risk: Mapped[float | None] = mapped_column(Float, nullable=True)  # maps to voice_score
    rppg_risk: Mapped[bool | None] = mapped_column(Boolean, nullable=True)  # maps to rppg_hrv_flag
    composite_risk: Mapped[float | None] = mapped_column(Float, nullable=True)  # maps to composite_score
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    sync_status: Mapped[str] = mapped_column(String(16), default="pending", index=True)

    # Extra fields to prevent data loss and map fully to Session model
    asha_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    village_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(16), nullable=True, index=True)
    idrs_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    voice_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    rppg_detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    component_breakdown: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prediabetes_trajectory: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)


class SqlSyncQueue(Base):
    __tablename__ = "sync_queue"

    queue_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)  # 'patient' or 'screening'
    entity_id: Mapped[str] = mapped_column(String(64), index=True)
    operation_type: Mapped[str] = mapped_column(String(16))  # 'insert' or 'update'
    payload_json: Mapped[str] = mapped_column(Text)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    last_attempt_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sync_state: Mapped[str] = mapped_column(String(16), default="pending", index=True)

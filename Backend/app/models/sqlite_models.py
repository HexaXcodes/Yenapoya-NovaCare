"""
SQLAlchemy (async) models for the SQLite mirror.

These tables mirror the MongoDB collections used on-device. On the server,
SQLite serves as a relational fallback and as the staging area for the sync
queue. The schema deliberately mirrors the Mongo document shape via a JSON
payload column plus the columns we filter/sort on.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LocalPatient(Base):
    __tablename__ = "local_patients"

    local_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    abha_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_hash: Mapped[str | None] = mapped_column(String(128), nullable=True)
    village_code: Mapped[str] = mapped_column(String(32), index=True)
    district_code: Mapped[str | None] = mapped_column(String(32), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    synced: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class LocalSession(Base):
    __tablename__ = "local_sessions"

    local_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    patient_local_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    asha_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    village_code: Mapped[str] = mapped_column(String(32), index=True)
    idrs_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    voice_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    rppg_hrv_flag: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    tier: Mapped[str | None] = mapped_column(String(8), nullable=True, index=True)
    composite_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    synced: Mapped[bool] = mapped_column(Boolean, default=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)


class SyncQueue(Base):
    """A durable record of every push the device made (audit / replay)."""

    __tablename__ = "sync_queue"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    asha_id: Mapped[str] = mapped_column(String(40), index=True)
    local_id: Mapped[str] = mapped_column(String(64), index=True)
    collection: Mapped[str] = mapped_column(String(32))
    source: Mapped[str] = mapped_column(String(16), default="asha")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)

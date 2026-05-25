import asyncio
import os
import pytest
from datetime import datetime, timezone
from sqlalchemy import select
from unittest.mock import patch
from bson import ObjectId

from app.config import settings
from app.database import (
    connect_sqlite,
    close_sqlite,
    get_sql_sessionmaker,
)
from app.models.sqlite_models import SqlPatient, SqlScreening, SqlSyncQueue
from app.repositories import patient_repo, session_repo
from app.services.sync_worker import sync_once


class MockMongoCollection:
    def __init__(self):
        self.records = []

    async def delete_many(self, query):
        filtered = []
        for r in self.records:
            keep = False
            for k, v in query.items():
                if r.get(k) != v:
                    keep = True
            if keep:
                filtered.append(r)
        deleted_count = len(self.records) - len(filtered)
        self.records = filtered

        class DummyResult:
            deleted_count = deleted_count

        return DummyResult()

    async def find_one(self, query):
        if "$or" in query:
            for sub_query in query["$or"]:
                for r in self.records:
                    match = True
                    for k, v in sub_query.items():
                        if r.get(k) != v:
                            match = False
                    if match:
                        return r
            return None

        for r in self.records:
            match = True
            for k, v in query.items():
                if r.get(k) != v:
                    match = False
            if match:
                return r
        return None

    async def insert_one(self, doc):
        if "_id" not in doc:
            doc["_id"] = ObjectId()
        self.records.append(doc)

        class DummyResult:
            inserted_id = doc["_id"]

        return DummyResult()

    async def replace_one(self, query, doc):
        idx = -1
        for i, r in enumerate(self.records):
            match = True
            for k, v in query.items():
                if r.get(k) != v:
                    match = False
            if match:
                idx = i
                break
        if idx != -1:
            if "_id" not in doc:
                doc["_id"] = self.records[idx]["_id"]
            self.records[idx] = doc
        else:
            if "_id" not in doc:
                doc["_id"] = ObjectId()
            self.records.append(doc)


class MockMongoDatabase:
    def __init__(self):
        self.patients = MockMongoCollection()
        self.sessions = MockMongoCollection()

    def __getitem__(self, name):
        if name == "patients":
            return self.patients
        elif name == "sessions":
            return self.sessions
        raise KeyError(name)


@pytest.fixture
async def db_setup():
    # Setup test DB
    settings.SQLITE_URL = "sqlite+aiosqlite:///./test_temp.db"
    mock_db = MockMongoDatabase()

    with patch("app.database.connect_mongo", return_value=None), \
         patch("app.database.close_mongo", return_value=None), \
         patch("app.database.get_db", return_value=mock_db):
        await connect_sqlite()
        yield mock_db
        await close_sqlite()

    # Cleanup test db files
    for suffix in ["", "-journal", "-wal", "-shm"]:
        path = f"./test_temp.db{suffix}"
        if os.path.exists(path):
            try:
                os.remove(path)
            except Exception:
                pass


@pytest.mark.asyncio
async def test_offline_first_flow(db_setup):
    db = db_setup
    phone_hash = "test_phone_hash_12345"

    # 1. Create Patient offline (written to SQLite first)
    patient_data = {
        "local_id": "test-patient-uuid-111",
        "abha_id": "1234-5678-9012",
        "phone_hash": phone_hash,
        "name": "Ramu",
        "age": 45,
        "sex": "male",
        "village_code": "VIL001",
        "district_code": "DIS001",
        "waist_cm": 85.0,
        "family_history_flag": False,
        "occupation_transition_flag": False,
        "preferred_lang": "hi",
    }

    created = await patient_repo.create_patient(db, patient_data)
    assert created["name"] == "Ramu"
    assert created["id"] == "test-patient-uuid-111"

    # Assert SqlPatient and SqlSyncQueue records exist in SQLite
    sessionmaker = get_sql_sessionmaker()
    async with sessionmaker() as session:
        p = await session.get(SqlPatient, "test-patient-uuid-111")
        assert p is not None
        assert p.sync_status == "pending"

        q = (
            await session.execute(
                select(SqlSyncQueue).where(SqlSyncQueue.entity_id == "test-patient-uuid-111")
            )
        ).scalars().first()
        assert q is not None
        assert q.entity_type == "patient"
        assert q.sync_state == "pending"

    # 2. Create Screening/Session offline (written to SQLite first)
    session_data = {
        "local_id": "test-screening-uuid-222",
        "patient_id": "test-patient-uuid-111",
        "village_code": "VIL001",
        "asha_id": "asha-1",
    }
    created_sess = await session_repo.create_session(db, session_data)
    assert created_sess["local_id"] == "test-screening-uuid-222"

    # Update screening with IDRS details
    updated_sess = await session_repo.update_session(
        db,
        "test-screening-uuid-222",
        {"idrs_score": 60, "tier": "AMBER", "composite_score": 45.0},
    )
    assert updated_sess["idrs_score"] == 60
    assert updated_sess["tier"] == "AMBER"

    # Assert SQLite entries
    async with sessionmaker() as session:
        s = await session.get(SqlScreening, "test-screening-uuid-222")
        assert s is not None
        assert s.sync_status == "pending"
        assert s.idrs_score == 60

    # 3. Perform background synchronization
    # Run sync_once to push SqlPatient and SqlScreening to MongoDB
    with patch("app.services.sync_worker.get_db", return_value=db):
        await sync_once()

    # Assert SQLite is now synced
    async with sessionmaker() as session:
        p = await session.get(SqlPatient, "test-patient-uuid-111")
        assert p.sync_status == "synced"
        assert p.cloud_id is not None

        s = await session.get(SqlScreening, "test-screening-uuid-222")
        assert s.sync_status == "synced"
        assert s.cloud_screening_id is not None

    # Assert MongoDB has the records
    mongo_patient = await db.patients.find_one({"local_id": "test-patient-uuid-111"})
    assert mongo_patient is not None
    assert mongo_patient["name"] == "Ramu"

    mongo_sess = await db.sessions.find_one({"local_id": "test-screening-uuid-222"})
    assert mongo_sess is not None
    assert mongo_sess["idrs_score"] == 60
    assert mongo_sess["patient_id"] == str(mongo_patient["_id"])  # linked correctly

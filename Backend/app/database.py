"""
Database layer.

Two stores live side by side:

* MongoDB (via Motor, async) — the cloud source of truth for synced
  records, heatmap aggregates, campaigns, notifications, etc.
* SQLite (via SQLAlchemy async) — mirrors the device-side queue. On the
  server it is used as a fallback / staging area and for any relational
  needs (e.g. the sync queue table).

Both are initialised on app startup and disposed on shutdown.
"""
from __future__ import annotations

import logging
from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings

logger = logging.getLogger("novacare.db")


class _MongoState:
    client: Optional[AsyncIOMotorClient] = None
    db: Optional[AsyncIOMotorDatabase] = None


class _SQLState:
    engine: Optional[AsyncEngine] = None
    sessionmaker: Optional[async_sessionmaker[AsyncSession]] = None


mongo_state = _MongoState()
sql_state = _SQLState()


# --------------------------------------------------------------------------- #
# MongoDB
# --------------------------------------------------------------------------- #
async def connect_mongo() -> None:
    if mongo_state.client is not None:
        return
    logger.info("Connecting to MongoDB at %s", settings.MONGO_URI)
    mongo_state.client = AsyncIOMotorClient(settings.MONGO_URI, uuidRepresentation="standard")
    mongo_state.db = mongo_state.client[settings.MONGO_DB]
    await _ensure_indexes(mongo_state.db)


async def close_mongo() -> None:
    if mongo_state.client is not None:
        mongo_state.client.close()
        mongo_state.client = None
        mongo_state.db = None
        logger.info("MongoDB connection closed")


def get_db() -> AsyncIOMotorDatabase:
    """FastAPI dependency: return the live Mongo database handle."""
    if mongo_state.db is None:
        raise RuntimeError("MongoDB is not initialised. Call connect_mongo() first.")
    return mongo_state.db


async def _ensure_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create the indexes the app relies on (idempotent)."""
    await db.patients.create_index("abha_id", sparse=True)
    await db.patients.create_index("phone_hash")
    await db.patients.create_index("local_id", unique=True, sparse=True)

    await db.sessions.create_index("patient_id")
    await db.sessions.create_index("village_code")
    await db.sessions.create_index("local_id", unique=True, sparse=True)
    await db.sessions.create_index([("created_at", -1)])
    await db.sessions.create_index("tier")

    await db.villages.create_index("village_code", unique=True)
    await db.campaigns.create_index("district_code")
    await db.notifications.create_index([("created_at", -1)])
    await db.otps.create_index("phone")
    await db.otps.create_index("expires_at", expireAfterSeconds=0)
    await db.doctors.create_index("email", unique=True)
    await db.sync_cursors.create_index("asha_id", unique=True)
    logger.info("MongoDB indexes ensured")


# --------------------------------------------------------------------------- #
# SQLite (async) — relational mirror / sync queue
# --------------------------------------------------------------------------- #
async def connect_sqlite() -> None:
    if sql_state.engine is not None:
        return
    logger.info("Initialising SQLite engine at %s", settings.SQLITE_URL)
    sql_state.engine = create_async_engine(settings.SQLITE_URL, echo=False, future=True)
    sql_state.sessionmaker = async_sessionmaker(
        sql_state.engine, expire_on_commit=False, class_=AsyncSession
    )
    # Import here to avoid circular import at module load time.
    from app.models.sqlite_models import Base

    async with sql_state.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("SQLite tables ensured")


async def close_sqlite() -> None:
    if sql_state.engine is not None:
        await sql_state.engine.dispose()
        sql_state.engine = None
        sql_state.sessionmaker = None
        logger.info("SQLite engine disposed")


def get_sql_sessionmaker() -> async_sessionmaker[AsyncSession]:
    if sql_state.sessionmaker is None:
        raise RuntimeError("SQLite is not initialised. Call connect_sqlite() first.")
    return sql_state.sessionmaker


async def get_sql_session() -> AsyncSession:
    """FastAPI dependency: yield a SQLite session."""
    maker = get_sql_sessionmaker()
    async with maker() as session:
        yield session

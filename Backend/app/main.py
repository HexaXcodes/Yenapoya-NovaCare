"""
NovaCare backend — FastAPI application entry point.

Wires up:
  * lifespan: connect Mongo + SQLite, start scheduler
  * CORS restricted to configured frontend origins
  * a uniform {success, data, error} envelope for errors too
  * all routers
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.core.responses import AppError, fail
from app.database import (
    close_mongo,
    close_sqlite,
    connect_mongo,
    connect_sqlite,
)
from app.routers import (
    auth,
    campaigns,
    diet,
    followup,
    heatmap,
    i18n,
    notifications,
    patients,
    risk,
    sessions,
    sync,
)
from app.scheduler import start_scheduler, stop_scheduler

logging.basicConfig(
    level=logging.DEBUG if settings.DEBUG else logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s :: %(message)s",
)
# Silence noisy third-party logs to keep the console clean
for logger_name in ["pymongo", "aiosqlite", "apscheduler", "watchfiles", "motor", "urllib3"]:
    logging.getLogger(logger_name).setLevel(logging.WARNING)

logger = logging.getLogger("novacare")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting %s (%s)", settings.APP_NAME, settings.APP_ENV)
    await connect_mongo()
    await connect_sqlite()
    start_scheduler()
    from app.services.sync_worker import start_sync_worker
    await start_sync_worker()
    try:
        yield
    finally:
        from app.services.sync_worker import stop_sync_worker
        await stop_sync_worker()
        stop_scheduler()
        await close_sqlite()
        await close_mongo()
        logger.info("Shutdown complete")


app = FastAPI(
    title=f"{settings.APP_NAME} API",
    version="1.0.0",
    description="Rural NCD early-detection screening backend.",
    lifespan=lifespan,
)

# ---- CORS: only configured frontend origins ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["*"],
)


# ---- Uniform error envelopes ----
@app.exception_handler(AppError)
async def app_error_handler(_: Request, exc: AppError):
    return JSONResponse(status_code=exc.status_code, content=fail(exc.message))


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_: Request, exc: StarletteHTTPException):
    return JSONResponse(status_code=exc.status_code, content=fail(str(exc.detail)))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_: Request, exc: RequestValidationError):
    # Compact, non-PII validation summary.
    msgs = "; ".join(
        f"{'.'.join(str(p) for p in e['loc'][1:])}: {e['msg']}" for e in exc.errors()
    )
    return JSONResponse(status_code=422, content=fail(f"Validation error: {msgs}"))


@app.exception_handler(Exception)
async def unhandled_error_handler(_: Request, exc: Exception):
    logger.exception("Unhandled error")
    return JSONResponse(status_code=500, content=fail("Internal server error"))


# ---- Health ----
@app.get("/health", tags=["health"])
async def health():
    from app.database import check_mongo_health, check_sqlite_health, get_pending_sync_count

    mongo_ok = await check_mongo_health()
    sqlite_ok = await check_sqlite_health()
    pending_count = await get_pending_sync_count()

    overall_ok = mongo_ok and sqlite_ok
    status = "ok" if overall_ok else "degraded"

    return {
        "success": True,
        "data": {
            "status": status,
            "env": settings.APP_ENV,
            "sqlite_status": "connected" if sqlite_ok else "disconnected",
            "mongodb_status": "connected" if mongo_ok else "disconnected",
            "pending_sync_count": pending_count
        },
        "error": None
    }


# ---- Routers ----
app.include_router(auth.router)
app.include_router(patients.router)
app.include_router(sessions.router)
app.include_router(risk.router)
app.include_router(diet.router)
app.include_router(followup.router)
app.include_router(heatmap.router)
app.include_router(campaigns.router)
app.include_router(sync.router)
app.include_router(notifications.router)
app.include_router(i18n.i18n_router)
app.include_router(i18n.tts_router)

"""
APScheduler setup.

A single daily job runs the missed-followup scan, which sends re-engagement
SMS to overdue, non-green patients. The scheduler is started/stopped with the
app lifespan.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.database import get_db
from app.routers.followup import run_missed_followups

logger = logging.getLogger("novacare.scheduler")

scheduler: AsyncIOScheduler | None = None


async def _daily_missed_followups() -> None:
    try:
        db = get_db()
        result = await run_missed_followups(db)
        logger.info("Daily missed-followup job: %s", result)
    except Exception:  # noqa: BLE001
        logger.exception("Daily missed-followup job failed")


def start_scheduler() -> None:
    global scheduler
    if scheduler is not None:
        return
    scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
    # Every day at 09:00 IST.
    scheduler.add_job(
        _daily_missed_followups,
        CronTrigger(hour=9, minute=0),
        id="missed_followups",
        replace_existing=True,
    )
    scheduler.start()
    logger.info("APScheduler started (missed-followups @ 09:00 IST daily)")


def stop_scheduler() -> None:
    global scheduler
    if scheduler is not None:
        scheduler.shutdown(wait=False)
        scheduler = None
        logger.info("APScheduler stopped")

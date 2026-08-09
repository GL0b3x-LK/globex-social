"""APScheduler jobs — runs inside the FastAPI process (one Railway service).

Two jobs, both timezone-aware (settings.timezone):
  * draft job (daily, draft_hour): draft calendar entries in the lead window and
    send previews to the approver's WhatsApp.
  * publish job (daily, publish_hour): publish approved posts whose calendar
    date is today, then notify the approver.

Gated by settings.scheduler_enabled so dev servers and tests never fire drafts.
"""

from __future__ import annotations

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.logging_config import get_logger
from app.workflows import scheduled

log = get_logger("app.scheduler")

_scheduler: AsyncIOScheduler | None = None


async def _draft_job() -> None:
    count = await scheduled.draft_due_posts()
    log.info("draft job done", extra={"drafted": count})


async def _publish_job() -> None:
    count = await scheduled.publish_due_posts()
    log.info("publish job done", extra={"published": count})


def start() -> AsyncIOScheduler | None:
    """Start the scheduler if enabled; returns it (or None when disabled)."""
    global _scheduler
    settings = get_settings()
    if not settings.scheduler_enabled:
        log.info("scheduler disabled (SCHEDULER_ENABLED not set)")
        return None
    if _scheduler is not None:
        return _scheduler

    sched = AsyncIOScheduler(timezone=settings.timezone)
    sched.add_job(
        _draft_job,
        CronTrigger(hour=settings.draft_hour, minute=0, timezone=settings.timezone),
        id="calendar_draft",
        coalesce=True,
        misfire_grace_time=3600 * 6,  # a restart within 6h still runs today's job
    )
    sched.add_job(
        _publish_job,
        CronTrigger(hour=settings.publish_hour, minute=0, timezone=settings.timezone),
        id="calendar_publish",
        coalesce=True,
        misfire_grace_time=3600 * 6,
    )
    sched.start()
    _scheduler = sched
    log.info(
        "scheduler started",
        extra={
            "draft_hour": settings.draft_hour,
            "publish_hour": settings.publish_hour,
            "lead_days": settings.draft_lead_days,
            "tz": settings.timezone,
        },
    )
    return sched


def stop() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        log.info("scheduler stopped")

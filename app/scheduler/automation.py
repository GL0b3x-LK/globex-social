"""APScheduler jobs — runs inside the FastAPI process (one Railway service).

Two jobs, both timezone-aware (settings.timezone):
  * draft job (daily, draft_hour): draft calendar entries in the lead window and
    send previews to the approver's WhatsApp.
  * publish job (daily, publish_hour): publish approved posts whose calendar
    date is today, then notify the approver.

Gated by settings.scheduler_enabled so dev servers and tests never fire drafts.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.config import get_settings
from app.logging_config import get_logger
from app.workflows import redelivery, scheduled

log = get_logger("app.scheduler")

_scheduler: AsyncIOScheduler | None = None

# How often to chase previews that never arrived. Frequent enough that a reopened
# 24h window is used while the operator is still at their phone, sparse enough
# that a genuinely blocked account is not retried into its own message cap.
_REDELIVERY_MINUTES = 20


def _test_grid(interval_hours: float, tz_name: str, start_at: str | None = None) -> IntervalTrigger:
    """The test-run interval, anchored to local midnight (or to ``start_at``).

    An unanchored IntervalTrigger counts from the moment the scheduler starts, so
    every deploy pushed the next test post a full interval into the future — a
    day with three deploys silently skipped slots, and nothing in the logs said
    so. Anchoring fixes the grid (…, 08:00, 10:00, 12:00, …), so a restart
    resumes the schedule instead of restarting it.

    ``start_at`` moves the anchor off midnight and suppresses every slot before
    it — used to line the run up with the moment Twilio's message cap frees
    capacity, so the first post of the day is one somebody can actually receive.
    An unparseable value falls back to midnight rather than stopping the run;
    a stopped scheduler is a far worse failure than a mistimed one.
    """
    tz = ZoneInfo(tz_name)
    anchor = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    if start_at:
        try:
            parsed = datetime.fromisoformat(start_at)
            anchor = parsed if parsed.tzinfo else parsed.replace(tzinfo=tz)
        except ValueError:
            log.error(
                "TEST_START_AT is not an ISO datetime; anchoring to midnight",
                extra={"value": start_at},
            )
    return IntervalTrigger(hours=interval_hours, start_date=anchor, timezone=tz_name)


async def _draft_job() -> None:
    count = await scheduled.draft_due_posts()
    log.info("draft job done", extra={"drafted": count})


async def _publish_job() -> None:
    count = await scheduled.publish_due_posts()
    log.info("publish job done", extra={"published": count})


async def _redelivery_job() -> None:
    """Re-send previews that were rendered but never reached anyone.

    Runs on both the test and the client schedules: the causes (a closed 24h
    window, a lapsed sandbox join, the daily message cap) all clear on their own
    with time, and until this existed the post simply stayed unseen for good.
    """
    sent = await redelivery.retry_undelivered()
    if sent:
        log.info("re-delivery job done", extra={"delivered": sent})


async def _test_job() -> None:
    """Internal test run: one calendar post per interval, in approved order.

    Stops itself when the calendar is exhausted so a forgotten test run cannot
    keep firing against an empty calendar.
    """
    more = await scheduled.draft_next_for_test()
    if not more and _scheduler is not None:
        _scheduler.remove_job("calendar_test")
        log.info("test run finished; job removed")


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
    # Owed previews are chased on every schedule — a post nobody saw is the same
    # failure whether it came from the test run or the client calendar.
    sched.add_job(
        _redelivery_job,
        IntervalTrigger(minutes=_REDELIVERY_MINUTES, timezone=settings.timezone),
        id="redelivery",
        coalesce=True,
        max_instances=1,
    )

    if settings.test_mode:
        # The internal run replaces the calendar jobs rather than joining them:
        # test posts already carry today's date, so the daily publish sweep would
        # have nothing to do, and the daily draft would post on real dates too.
        trigger = _test_grid(
            settings.test_interval_hours, settings.timezone, settings.test_start_at
        )
        sched.add_job(
            _test_job,
            trigger,
            id="calendar_test",
            coalesce=True,
            max_instances=1,  # a slow draft must not overlap the next interval
            misfire_grace_time=int(settings.test_interval_hours * 3600),
        )
        sched.start()
        _scheduler = sched
        log.warning(
            "TEST MODE scheduler started — one calendar post per interval",
            extra={
                "every_hours": settings.test_interval_hours,
                "recipients": len(settings.approval_recipients_list),
                "tz": settings.timezone,
                # Logged so a deploy always says, on the spot, when the next post
                # is due — the failure it replaces was invisible until someone
                # noticed a post had not arrived.
                "next_post": str(sched.get_job("calendar_test").next_run_time),
            },
        )
        return sched

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

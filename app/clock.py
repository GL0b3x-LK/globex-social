"""Wall-clock helpers in the client's timezone (settings.timezone, New York).

The server does not keep the client's clock: Railway runs in UTC, so a bare
``date.today()`` there rolls over at 8pm New York. That is not a cosmetic
difference for a calendar whose posts go out at 1am — a post approved at 9pm on
the 13th looked to the code like the 14th had already arrived, and published
four hours before its slot. Every "what day is it" and "has its moment come"
question therefore goes through here, in the timezone the calendar is written in.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.config import get_settings


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def now() -> datetime:
    """The current moment, timezone-aware, in the client's timezone."""
    return datetime.now(tz())


def today() -> date:
    """The client's calendar date — not the server's."""
    return now().date()


def next_working_day(from_day: date) -> date:
    """The next Monday-to-Friday day strictly after ``from_day``.

    The draft lead is a working day, not a day: a post due on Monday must preview
    on Friday, because nobody is reading WhatsApp on Sunday morning to approve it.
    Friday's 7am draft therefore covers Saturday, Sunday and Monday.

    Weekends only — public holidays are not modelled, so a post falling the day
    after one previews on the holiday itself.
    """
    day = from_day + timedelta(days=1)
    while day.weekday() >= 5:  # 5 = Saturday, 6 = Sunday
        day += timedelta(days=1)
    return day


def publish_moment(publish_on: date | str) -> datetime:
    """The instant a post scheduled for ``publish_on`` may go live.

    Its date at ``settings.publish_hour`` local — the same instant the publish
    job fires — so an approval arriving before it is held and one arriving after
    it goes out immediately, with no window in between where both are true.
    """
    day = date.fromisoformat(publish_on) if isinstance(publish_on, str) else publish_on
    return datetime.combine(day, time(hour=get_settings().publish_hour), tzinfo=tz())


def is_due(publish_on: date | str) -> bool:
    """Has this post's publish moment arrived?"""
    return now() >= publish_moment(publish_on)

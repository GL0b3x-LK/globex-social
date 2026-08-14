"""The client's clock: working-day leads and the 1am publish moment.

These are the two rules the whole schedule rests on — a post previews on the
previous WORKING day and goes out at 1am New York on its own date — and both
were wrong when read off the server's UTC clock.
"""

from __future__ import annotations

from datetime import date, datetime, time

import pytest

from app import clock


@pytest.mark.parametrize(
    ("day", "expected"),
    [
        (date(2026, 8, 14), date(2026, 8, 17)),  # Friday -> Monday
        (date(2026, 8, 15), date(2026, 8, 17)),  # Saturday -> Monday
        (date(2026, 8, 16), date(2026, 8, 17)),  # Sunday -> Monday
        (date(2026, 8, 17), date(2026, 8, 18)),  # Monday -> Tuesday
        (date(2026, 8, 13), date(2026, 8, 14)),  # Thursday -> Friday
    ],
)
def test_next_working_day_skips_the_weekend(day: date, expected: date) -> None:
    assert clock.next_working_day(day) == expected


def test_friday_lead_reaches_monday() -> None:
    """The window the 7am Friday draft job uses: Saturday, Sunday and Monday."""
    friday = date(2026, 8, 14)
    assert (clock.next_working_day(friday) - friday).days == 3


def test_publish_moment_is_1am_new_york() -> None:
    moment = clock.publish_moment("2026-08-17")
    assert (moment.hour, moment.minute) == (1, 0)
    assert moment.tzinfo is not None
    assert moment.utcoffset() is not None
    assert moment.date() == date(2026, 8, 17)


def test_publish_moment_survives_the_dst_boundary() -> None:
    """1am local stays 1am local either side of the November change — a UTC
    offset baked in once would drift the whole calendar by an hour."""
    before = clock.publish_moment(date(2026, 10, 15))
    after = clock.publish_moment(date(2026, 12, 15))
    assert before.hour == after.hour == 1
    assert before.utcoffset() != after.utcoffset()


def test_a_post_is_not_due_before_its_1am(monkeypatch: pytest.MonkeyPatch) -> None:
    """9pm the night before is already tomorrow in UTC — the mistake this guards."""
    monkeypatch.setattr(
        clock, "now", lambda: datetime.combine(date(2026, 8, 16), time(21), clock.tz())
    )
    assert not clock.is_due("2026-08-17")


def test_a_post_is_due_once_1am_has_passed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        clock, "now", lambda: datetime.combine(date(2026, 8, 17), time(1), clock.tz())
    )
    assert clock.is_due("2026-08-17")

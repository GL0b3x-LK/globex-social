"""Set (or preview) the calendar launch date — the go-live switch.

The calendar ships dormant: with no CALENDAR_LAUNCH_DATE the scheduler can't
draft or publish anything, so no post is silently skipped while the date is
being decided. This script shows exactly what a given launch date produces
before anything is changed.

    # what would happen if we go live on 7 Sept?
    python scripts/set_launch_date.py 2026-09-07

    # commit it to Railway production (also flips SCHEDULER_ENABLED on)
    python scripts/set_launch_date.py 2026-09-07 --apply

Anchored entries (holidays, trade shows, anniversaries, season-tied posts) keep
their real dates; any whose moment has already passed on launch day are dropped
and listed below. Floating evergreen posts re-flow into the remaining Mon/Wed/Fri
slots in the client-approved order, so none of them are ever lost.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import calendar_source  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("launch", help="first day of the calendar, YYYY-MM-DD")
    ap.add_argument(
        "--apply",
        action="store_true",
        help="write CALENDAR_LAUNCH_DATE + SCHEDULER_ENABLED=true to Railway",
    )
    args = ap.parse_args()

    launch = date.fromisoformat(args.launch)
    if launch < date.today():
        print(f"refusing: {launch} is in the past")
        return 1

    plan = calendar_source.schedule(launch)
    dropped = calendar_source.dropped_by_launch(launch)
    total = len(calendar_source.load_calendar())

    print(f"Launch {launch:%a %d %b %Y} — {len(plan)} of {total} posts scheduled")
    print(f"  first: {plan[0].post_date:%a %d %b} · {plan[0].title}")
    print(f"  last:  {plan[-1].post_date:%a %d %b %Y} · {plan[-1].title}")
    if dropped:
        print(f"\n  {len(dropped)} dated posts already past their moment — skipped:")
        for e in dropped:
            print(f"    {e.planned_date:%d %b} {e.title} ({e.category})")
    print("\n  first two weeks:")
    for e in plan[:6]:
        mark = "📌" if e.anchored else "  "
        print(f"    {mark} {e.post_date:%a %d %b}  [{e.category:9}] {e.title}")

    if not args.apply:
        print("\n(preview only — re-run with --apply to go live)")
        return 0

    cmd = [
        "railway",
        "variables",
        "--set",
        f"CALENDAR_LAUNCH_DATE={launch.isoformat()}",
        "--set",
        "SCHEDULER_ENABLED=true",
    ]
    print("\napplying to Railway…")
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout.strip() or result.stderr.strip())
    if result.returncode == 0:
        print(f"\nLive. First drafts reach WhatsApp at 07:00 New York, from {launch:%d %b}.")
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())

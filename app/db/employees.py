"""Employee query helpers. hire_date only — never DOB/age."""

from __future__ import annotations

from typing import Any

from app.db.client import get_supabase, maybe_row, rows

Row = dict[str, Any]
_TABLE = "employees"


def upsert_many(records: list[Row]) -> list[Row]:
    """Idempotent upsert keyed on unique name."""
    return rows(get_supabase().table(_TABLE).upsert(records, on_conflict="name").execute())


def list_active() -> list[Row]:
    return rows(
        get_supabase().table(_TABLE).select("*").eq("active", True).order("hire_date").execute()
    )


def get_by_name(name: str) -> Row | None:
    return maybe_row(get_supabase().table(_TABLE).select("*").eq("name", name).limit(1).execute())


def milestone_candidates(min_years: int, as_of_year: int) -> list[Row]:
    """Active employees with >= min_years tenure as of the given year.

    Year math is sufficient here; exact anniversary windowing is the scheduler's
    job (Phase 6). Returns rows annotated with a ``years`` field.
    """
    out: list[Row] = []
    for record in list_active():
        hire = record.get("hire_date")
        if not hire:
            continue
        years = as_of_year - int(str(hire)[:4])
        if years >= min_years:
            out.append({**record, "years": years})
    return out

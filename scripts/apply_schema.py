"""Apply app/db/schema.sql to Supabase Postgres, and ensure the Storage bucket.

Idempotent. Runtime db access uses supabase-py (PostgREST), which cannot run
DDL — so this one script connects directly via psycopg using SUPABASE_DB_URL
(Dashboard -> Settings -> Database -> Connection string -> URI).

Run:  .venv\\Scripts\\python.exe scripts\\apply_schema.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import psycopg

from app.config import get_settings
from app.db.client import get_supabase
from app.logging_config import configure_logging, get_logger

SCHEMA = Path(__file__).resolve().parent.parent / "app" / "db" / "schema.sql"
log = get_logger("apply_schema")


def apply_schema(db_url: str) -> None:
    sql = SCHEMA.read_text(encoding="utf-8")
    with psycopg.connect(db_url) as conn, conn.cursor() as cur:
        cur.execute(sql)  # psycopg3 executes multi-statement scripts
        conn.commit()
    log.info("schema applied", extra={"file": str(SCHEMA)})


def ensure_bucket() -> None:
    settings = get_settings()
    bucket = settings.supabase_storage_bucket
    try:
        get_supabase().storage.create_bucket(bucket, options={"public": True})
        log.info("storage bucket created", extra={"bucket": bucket})
    except Exception as exc:  # already exists -> idempotent no-op
        log.info("storage bucket ensure (likely exists)", extra={"bucket": bucket, "note": str(exc)})


def main() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.supabase_db_url:
        sys.exit(
            "SUPABASE_DB_URL not set. Supabase dashboard -> Settings -> Database -> "
            "Connection string -> URI (Session pooler). Add it to .env, then re-run."
        )
    apply_schema(settings.supabase_db_url)
    ensure_bucket()
    log.info("done")


if __name__ == "__main__":
    main()

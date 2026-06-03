"""FastAPI application: app factory, lifespan, correlation-id middleware, /health,
and the Twilio WhatsApp webhook routes.
"""
from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import get_settings
from app.db.client import ping as supabase_ping
from app.logging_config import configure_logging, correlation_id_var, get_logger
from app.messaging import webhook
from app.templates.renderer import renderer

log = get_logger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()  # fail-fast: missing required env raises here
    configure_logging(settings.log_level)
    log.info(
        "startup",
        extra={"environment": settings.environment, "model": settings.claude_model},
    )
    try:
        supabase_ping()
        log.info("supabase connection ok")
    except Exception as exc:
        # Don't crash the process — keep /health reachable so the failure is
        # observable. Supabase is a hard dependency; /health will report 503.
        log.error("supabase connection failed at startup", extra={"error": str(exc)})
    try:
        await renderer.start()
        log.info("playwright renderer started")
    except Exception as exc:
        log.error("renderer failed to start", extra={"error": str(exc)})
    yield
    await renderer.stop()
    log.info("shutdown")


app = FastAPI(title="Globex SM Automation", version="0.1.0", lifespan=lifespan)
app.include_router(webhook.router)


@app.middleware("http")
async def correlation_id_middleware(request: Request, call_next):
    cid = request.headers.get("X-Correlation-ID") or uuid.uuid4().hex[:12]
    token = correlation_id_var.set(cid)
    try:
        response = await call_next(request)
    finally:
        correlation_id_var.reset(token)
    response.headers["X-Correlation-ID"] = cid
    return response


@app.get("/health")
async def health() -> JSONResponse:
    settings = get_settings()
    checks: dict[str, str] = {"status": "ok", "environment": settings.environment}

    try:
        supabase_ping()
        checks["supabase"] = "connected"
    except Exception as exc:
        checks["supabase"] = "error"
        checks["status"] = "degraded"
        checks["supabase_error"] = str(exc)

    checks["anthropic"] = "configured" if settings.anthropic_api_key else "missing"

    status_code = 200 if checks["status"] == "ok" else 503
    return JSONResponse(checks, status_code=status_code)

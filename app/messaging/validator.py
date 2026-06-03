"""Twilio request-signature validation as a FastAPI dependency.

Rejects unsigned/forged webhook calls with 403. The signed URL is the externally
visible one, so we honour proxy headers (ngrok / Railway) when reconstructing it.
Validation can be disabled (`twilio_validate_signature=false`) for local dev only.
"""

from __future__ import annotations

from fastapi import HTTPException, Request
from twilio.request_validator import RequestValidator

from app.config import get_settings
from app.logging_config import get_logger

log = get_logger("app.messaging.validator")


def _public_url(request: Request) -> str:
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    host = (
        request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    )
    return f"{proto}://{host}{request.url.path}"


async def validate_twilio_request(request: Request) -> None:
    settings = get_settings()
    if not settings.twilio_validate_signature:
        return
    signature = request.headers.get("X-Twilio-Signature", "")
    form = await request.form()
    params = {key: str(value) for key, value in form.items()}
    validator = RequestValidator(settings.twilio_auth_token)
    if not validator.validate(_public_url(request), params, signature):
        log.warning("rejected: invalid Twilio signature", extra={"path": request.url.path})
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")

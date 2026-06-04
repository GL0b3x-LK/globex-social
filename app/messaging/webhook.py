"""Twilio WhatsApp webhooks: inbound messages + outbound delivery-status callbacks.

The inbound endpoint acks Twilio immediately with empty TwiML (well inside the 15s
window) and does the slow work (generate → render → upload → send preview) in a
FastAPI background task. Both endpoints require a valid Twilio signature.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import Response

from app.logging_config import get_logger
from app.messaging.validator import validate_twilio_request
from app.workflows import on_demand

log = get_logger("app.messaging.webhook")

router = APIRouter(prefix="/webhooks/twilio", tags=["twilio"])

_EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'
_XML = "application/xml"


@router.post("/message")
async def incoming_message(
    request: Request,
    background: BackgroundTasks,
    _: None = Depends(validate_twilio_request),
) -> Response:
    form = await request.form()
    from_phone = str(form.get("From", ""))
    body = str(form.get("Body", "") or "")
    try:
        num_media = int(str(form.get("NumMedia", "0") or "0"))
    except ValueError:
        num_media = 0
    # Carry the content-type so the handler can tell a voice note (audio/*) from a
    # photo (image/*) without downloading first — Twilio sends it in the form.
    media = [
        (str(form[f"MediaUrl{i}"]), str(form.get(f"MediaContentType{i}", "") or ""))
        for i in range(num_media)
        if form.get(f"MediaUrl{i}")
    ]
    log.info(
        "inbound message",
        extra={"from": from_phone, "num_media": num_media, "message_sid": form.get("MessageSid")},
    )
    background.add_task(on_demand.handle_incoming_message, from_phone, body, media)
    return Response(content=_EMPTY_TWIML, media_type=_XML)


@router.post("/status")
async def status_callback(
    request: Request,
    _: None = Depends(validate_twilio_request),
) -> Response:
    form = await request.form()
    log.info(
        "delivery status",
        extra={"message_sid": form.get("MessageSid"), "status": form.get("MessageStatus")},
    )
    return Response(content=_EMPTY_TWIML, media_type=_XML)

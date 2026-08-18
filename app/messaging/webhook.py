"""Twilio WhatsApp webhooks: inbound messages + outbound delivery-status callbacks.

The inbound endpoint acks Twilio immediately with empty TwiML (well inside the 15s
window) and does the slow work (generate → render → upload → send preview) in a
FastAPI background task. Both endpoints require a valid Twilio signature.
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import Response

from app.logging_config import get_logger
from app.messaging import history
from app.messaging.validator import validate_twilio_request
from app.workflows import on_demand, redelivery

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
    message_sid = str(form.get("MessageSid", "") or "") or None
    # Present only when Karen swipe-replied to an earlier message (<7 days old).
    reply_to_sid = str(form.get("OriginalRepliedMessageSid", "") or "") or None
    log.info(
        "inbound message",
        extra={
            "from": from_phone,
            "num_media": num_media,
            "message_sid": message_sid,
            "reply_to_sid": reply_to_sid,
        },
    )
    background.add_task(
        on_demand.handle_incoming_message,
        from_phone,
        body,
        media,
        message_sid=message_sid,
        reply_to_sid=reply_to_sid,
    )
    return Response(content=_EMPTY_TWIML, media_type=_XML)


# Twilio's terminal failure states. A send is accepted with a 201 and a SID and
# only fails later, out of band — the whole reason a preview could be "sent"
# successfully and never arrive.
_FAILED_STATUSES = frozenset({"failed", "undelivered"})


@router.post("/status")
async def status_callback(
    request: Request,
    _: None = Depends(validate_twilio_request),
) -> Response:
    """Twilio's out-of-band verdict on a message we already think we sent.

    Believing the 201 is what hid three days of undelivered scheduled posts: the
    window was shut, WhatsApp rejected each one after the fact, and nothing in
    the system ever learned. A failure here marks the post as owed again, which
    is all the existing re-delivery job needs to chase it.
    """
    form = await request.form()
    sid = str(form.get("MessageSid") or "")
    status = str(form.get("MessageStatus") or "")
    error = form.get("ErrorCode")
    if status not in _FAILED_STATUSES:
        log.info("delivery status", extra={"message_sid": sid, "status": status})
        return Response(content=_EMPTY_TWIML, media_type=_XML)

    log.error(
        "message failed after Twilio accepted it",
        extra={"message_sid": sid, "status": status, "error_code": error},
    )
    row = await history.by_sid(sid) if sid else None
    post_id = (row or {}).get("post_id")
    phone = (row or {}).get("phone_number")
    if post_id and phone:
        await redelivery.record(str(post_id), str(phone), delivered=False)
        log.info("queued for re-delivery", extra={"post_id": post_id, "to": phone})
    return Response(content=_EMPTY_TWIML, media_type=_XML)

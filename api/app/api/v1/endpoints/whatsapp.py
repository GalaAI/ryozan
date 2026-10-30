"""Meta WhatsApp Cloud API webhook.

This endpoint is separate from Twilio SMS because WhatsApp Cloud API is a Meta
asset with its own phone-number id, webhook verification token, and payload
shape. It still archives inbound messages into Courier so staff have one
conversation surface instead of another provider-specific inbox.
"""

import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.user import BTTCOUser
from app.services.sms import normalise_phone

logger = logging.getLogger("bttco.whatsapp")

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])


@router.get("/webhook")
async def verify_whatsapp_webhook(
    hub_mode: str | None = Query(None, alias="hub.mode"),
    hub_verify_token: str | None = Query(None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(None, alias="hub.challenge"),
):
    settings = get_settings()
    if hub_mode == "subscribe" and hub_verify_token and hub_verify_token == settings.META_WHATSAPP_WEBHOOK_VERIFY_TOKEN:
        return Response(content=hub_challenge or "", media_type="text/plain")
    raise HTTPException(status_code=403, detail="Invalid WhatsApp webhook verification token")


@router.post("/webhook")
async def handle_whatsapp_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    body = await request.body()
    app_secret = settings.META_WHATSAPP_APP_SECRET
    if not app_secret:
        is_local_dev = settings.DEBUG or settings.APP_ENV.strip().lower() in {
            "development",
            "dev",
            "local",
            "test",
        }
        if is_local_dev:
            logger.warning("meta.whatsapp.webhook.no_secret_configured - accepting unsigned webhook in local/dev")
        else:
            logger.error("meta.whatsapp.webhook.no_secret_configured - rejecting webhook in non-dev environment")
            raise HTTPException(status_code=503, detail="WhatsApp webhook signing secret not configured")
    elif not _valid_meta_signature(
        body,
        request.headers.get("x-hub-signature-256", ""),
        app_secret,
    ):
        raise HTTPException(status_code=403, detail="Invalid Meta webhook signature")

    payload = json.loads(body.decode("utf-8") or "{}")
    archived = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            contacts_by_wa_id = {
                str(contact.get("wa_id")): ((contact.get("profile") or {}).get("name") or str(contact.get("wa_id")))
                for contact in value.get("contacts", [])
            }
            for message in value.get("messages", []):
                if await _archive_message(db, message, contacts_by_wa_id):
                    archived += 1

    await db.commit()
    return {"status": "ok", "archived": archived}


def _valid_meta_signature(body: bytes, signature_header: str, app_secret: str) -> bool:
    if not signature_header.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(app_secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature_header)


async def _archive_message(
    db: AsyncSession,
    message: dict,
    contacts_by_wa_id: dict[str, str],
) -> bool:
    message_id = message.get("id")
    from_wa = str(message.get("from") or "")
    text = ((message.get("text") or {}).get("body") or "").strip()
    if not message_id or not from_wa or not text:
        return False

    duplicate = await db.scalar(
        select(Message.id).where(
            Message.message_type == "whatsapp",
            Message.metadata_json.contains(message_id),
        )
    )
    if duplicate:
        return False

    e164 = normalise_phone(from_wa) or (f"+{from_wa}" if from_wa.isdigit() else from_wa)
    contact_name = contacts_by_wa_id.get(from_wa) or e164
    owner = await _resolve_owner(db, e164)
    sender = await _resolve_sender(db, e164, contact_name)
    conversation = await _resolve_conversation(db, owner, sender, contact_name, e164)

    now = datetime.now(UTC)
    db.add(
        Message(
            conversation_id=conversation.id,
            sender_id=sender.id,
            content=text,
            message_type="whatsapp",
            metadata_json=json.dumps(
                {
                    "channel": "whatsapp",
                    "direction": "inbound",
                    "from": e164,
                    "wa_id": from_wa,
                    "meta_message_id": message_id,
                    "received_at": now.isoformat(),
                }
            ),
            created_at=now,
            updated_at=now,
        )
    )
    conversation.updated_at = now
    return True


async def _resolve_owner(db: AsyncSession, phone_e164: str) -> BTTCOUser:
    staff = await db.execute(
        select(BTTCOUser)
        .where(BTTCOUser.role.in_(("founder", "admin")))
        .order_by(desc(BTTCOUser.role == "founder"), BTTCOUser.created_at.asc())
        .limit(1)
    )
    owner = staff.scalar_one_or_none()
    if owner:
        return owner

    fallback = await db.scalar(select(BTTCOUser).order_by(BTTCOUser.created_at.asc()).limit(1))
    if not fallback:
        raise HTTPException(
            status_code=500, detail=f"No Courier owner available for WhatsApp message from {phone_e164}"
        )
    return fallback


async def _resolve_sender(db: AsyncSession, phone_e164: str, contact_name: str) -> BTTCOUser:
    users = await db.execute(select(BTTCOUser).where(BTTCOUser.phone.is_not(None)))
    for user in users.scalars():
        if normalise_phone(user.phone) == phone_e164:
            return user

    email = f"whatsapp-{phone_e164.lstrip('+')}@ryozan.app"
    existing = await db.scalar(select(BTTCOUser).where(BTTCOUser.email == email))
    if existing:
        return existing

    user = BTTCOUser(
        email=email,
        name=contact_name or phone_e164,
        role="collector",
        phone=phone_e164,
        password_hash="whatsapp-inbound-placeholder",
    )
    db.add(user)
    await db.flush()
    return user


async def _resolve_conversation(
    db: AsyncSession,
    owner: BTTCOUser,
    sender: BTTCOUser,
    contact_name: str,
    phone_e164: str,
) -> Conversation:
    existing = await db.execute(
        select(Conversation)
        .join(ConversationParticipant, ConversationParticipant.conversation_id == Conversation.id)
        .where(
            Conversation.name == f"WhatsApp: {phone_e164}",
            ConversationParticipant.user_id == owner.id,
        )
        .limit(1)
    )
    conversation = existing.scalar_one_or_none()
    if conversation:
        return conversation

    conversation = Conversation(
        name=f"WhatsApp: {phone_e164}",
        is_group=False,
        created_by=owner.id,
    )
    db.add(conversation)
    await db.flush()
    db.add(ConversationParticipant(conversation_id=conversation.id, user_id=owner.id, role="owner"))
    if sender.id != owner.id:
        db.add(ConversationParticipant(conversation_id=conversation.id, user_id=sender.id, role="member"))
    logger.info("Created WhatsApp Courier thread for %s (%s)", contact_name, phone_e164)
    return conversation

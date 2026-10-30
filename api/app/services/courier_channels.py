"""Courier channel and agent tool registry.

Net-new because the existing Courier surface exposes ad hoc channel status from
the conversations endpoint, while the next phase needs one shared registry for
business Twilio lanes, personal open-source lanes, and content-only agents.
This file does not send messages; it describes which tools are available,
configured, and safe for Courier to expose.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.services.sms import mask_sms_sender, public_sms_lane_status

if TYPE_CHECKING:
    from app.core.config import Settings


@dataclass(frozen=True)
class CourierRouteContext:
    email_route: str | None = None
    email_forward_to: str | None = None
    email_verified: bool = False
    instagram_url: str | None = None
    facebook_url: str | None = None


def build_channel_status(settings: Settings, context: CourierRouteContext) -> dict[str, Any]:
    """Return current Courier channel status and provider tool catalog."""

    twilio_credentials = bool(settings.TWILIO_ACCOUNT_SID and settings.TWILIO_AUTH_TOKEN)
    sms_lanes = public_sms_lane_status(settings)
    bella_lane_ready = any(lane["id"] == "bella" and lane["status"] == "ready" for lane in sms_lanes)
    sms_configured = bool(twilio_credentials and settings.TWILIO_MESSAGING_SERVICE_SID)
    sms_connected = bool(sms_configured and bella_lane_ready)
    whatsapp_configured = bool(twilio_credentials and settings.TWILIO_FROM_NUMBER)
    email_configured = bool(settings.RESEND_API_KEY or settings.SENDGRID_API_KEY)
    bttco_voice_configured = bool(
        twilio_credentials and settings.ELEVENLABS_API_KEY and settings.ELEVENLABS_BTTCO_AGENT_ID
    )
    personal_voice_configured = bool(
        settings.JAMBONZ_ACCOUNT_SID
        and settings.JAMBONZ_API_KEY
        and settings.ELEVENLABS_API_KEY
        and settings.ELEVENLABS_JUSTIN_AGENT_ID
    )

    channels = [
        {
            "id": "email",
            "label": "Email",
            "kind": "email",
            "connected": bool(context.email_verified),
            "configured": email_configured,
            "route": context.email_route,
            "forward_to": context.email_forward_to,
            "status": "verified" if context.email_verified else "needs_setup",
            "direction": "inbound_outbound",
            "provider": "resend_sendgrid",
            "lane": "bttco_business",
        },
        {
            "id": "sms",
            "label": "SMS",
            "kind": "messaging",
            "connected": sms_connected,
            "configured": sms_configured,
            "route": "Configured sender lanes" if sms_connected else None,
            "forward_to": "Courier",
            "status": "connected" if sms_connected else "needs_twilio_sender",
            "direction": "inbound_outbound",
            "provider": "twilio",
            "lane": "bttco_business",
        },
        {
            "id": "whatsapp",
            "label": "WhatsApp Business",
            "kind": "messaging",
            "connected": whatsapp_configured,
            "configured": whatsapp_configured,
            "route": mask_sms_sender(settings.TWILIO_FROM_NUMBER) if settings.TWILIO_FROM_NUMBER else None,
            "forward_to": "Courier",
            "status": "connected" if whatsapp_configured else "needs_twilio_whatsapp_sender",
            "direction": "inbound_outbound",
            "provider": "twilio_whatsapp",
            "lane": "bttco_business",
        },
        {
            "id": "instagram",
            "label": "Instagram",
            "kind": "social",
            "connected": bool(context.instagram_url),
            "configured": bool(context.instagram_url),
            "route": context.instagram_url,
            "forward_to": None,
            "status": "profile_linked" if context.instagram_url else "needs_oauth",
            "direction": "inbound_reference",
            "provider": "meta",
            "lane": "bttco_business",
        },
        {
            "id": "facebook",
            "label": "Facebook",
            "kind": "social",
            "connected": bool(context.facebook_url),
            "configured": bool(context.facebook_url),
            "route": context.facebook_url,
            "forward_to": None,
            "status": "profile_linked" if context.facebook_url else "needs_oauth",
            "direction": "inbound_reference",
            "provider": "meta",
            "lane": "bttco_business",
        },
        {
            "id": "twilio_voice",
            "label": "Business Voice",
            "kind": "voice",
            "connected": bttco_voice_configured,
            "configured": bool(twilio_credentials and settings.ELEVENLABS_API_KEY),
            "route": mask_sms_sender(settings.TWILIO_FROM_NUMBER) if settings.TWILIO_FROM_NUMBER else None,
            "forward_to": "Courier voice agent",
            "status": "ready_for_voice_webhook" if bttco_voice_configured else "needs_twilio_elevenlabs_voice_setup",
            "direction": "inbound_outbound",
            "provider": "twilio_elevenlabs",
            "lane": "bttco_business",
        },
        {
            "id": "justin_personal_assistant",
            "label": "Justin Personal Assistant",
            "kind": "voice",
            "connected": personal_voice_configured,
            "configured": bool(
                settings.JAMBONZ_ACCOUNT_SID or settings.JAMBONZ_API_KEY or settings.ELEVENLABS_JUSTIN_AGENT_ID
            ),
            "route": None,
            "forward_to": "Jambonz or SIP personal lane",
            "status": "ready_for_personal_voice_webhook" if personal_voice_configured else "planned_open_source_lane",
            "direction": "inbound_outbound",
            "provider": "jambonz_sip_elevenlabs",
            "lane": "justin_personal",
        },
        {
            "id": "auragg_content_agents",
            "label": "AuraGG Content Agents",
            "kind": "content",
            "connected": False,
            "configured": False,
            "route": "ryozan-collector / AuraGG",
            "forward_to": "Herald content engine",
            "status": "planned_content_only_lane",
            "direction": "draft_schedule_publish",
            "provider": "herald_discord_social",
            "lane": "auragg_content",
        },
    ]

    return {
        "channels": channels,
        "sms_lanes": sms_lanes,
        "tools": build_tool_catalog(settings),
        "policy": {
            "archive_by_default": True,
            "deletes_remove_participant_only": True,
            "audit_required": True,
            "business_channels_use_twilio": True,
            "personal_channels_prefer_open_source_sip": True,
            "content_agents_never_contact_clients_without_approval": True,
        },
    }


def build_tool_catalog(settings: Settings) -> list[dict[str, Any]]:
    public_base = settings.API_PUBLIC_BASE_URL.rstrip("/")
    bttco_voice_configured = bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and settings.ELEVENLABS_API_KEY
        and settings.ELEVENLABS_BTTCO_AGENT_ID
    )
    sms_lanes = public_sms_lane_status(settings)
    bella_sms_ready = any(lane["id"] == "bella" and lane["status"] == "ready" for lane in sms_lanes)
    sms_transport_ready = bool(
        settings.TWILIO_ACCOUNT_SID
        and settings.TWILIO_AUTH_TOKEN
        and settings.TWILIO_MESSAGING_SERVICE_SID
        and bella_sms_ready
    )
    return [
        {
            "id": "bttco_sms_inbound",
            "label": "Ryozan SMS inbound",
            "provider": "twilio",
            "lane": "bttco_business",
            "status": "ready" if sms_transport_ready else "needs_twilio_sender_lanes",
            "webhook_url": f"{public_base}/api/v1/sms/webhook/inbound",
            "capabilities": ["receive", "thread", "opt_out", "help", "status_tracking"],
        },
        {
            "id": "bttco_sms_outbound",
            "label": "Ryozan SMS outbound",
            "provider": "twilio",
            "lane": "bttco_business",
            "status": "ready" if sms_transport_ready else "needs_twilio_sender_lanes",
            "endpoint": "/api/v1/conversations/{conversation_id}/channel-send",
            "capabilities": ["send", "audit", "opt_in_gate", "status_tracking"],
        },
        {
            "id": "bttco_whatsapp_business",
            "label": "Ryozan WhatsApp Business",
            "provider": "twilio_whatsapp",
            "lane": "bttco_business",
            "status": "ready" if settings.TWILIO_ACCOUNT_SID and settings.TWILIO_FROM_NUMBER else "needs_twilio_sender",
            "webhook_url": f"{public_base}/api/v1/sms/webhook/inbound",
            "capabilities": ["receive", "thread", "send_freeform", "status_tracking", "bella_auto_reply"],
        },
        {
            "id": "bttco_voice_assistant",
            "label": "Ryozan voice assistant",
            "provider": "twilio_elevenlabs",
            "lane": "bttco_business",
            "status": "ready" if bttco_voice_configured else "planned",
            "webhook_url": f"{public_base}/api/v1/voice/twilio/inbound",
            "capabilities": ["call_screen", "summarize", "handoff", "missed_call_textback"],
        },
        {
            "id": "justin_personal_open_voice",
            "label": "Justin personal open-source voice",
            "provider": "jambonz_sip_elevenlabs",
            "lane": "justin_personal",
            "status": "ready" if settings.JAMBONZ_ACCOUNT_SID and settings.ELEVENLABS_JUSTIN_AGENT_ID else "planned",
            "capabilities": ["personal_assistant", "screening", "experiments", "discord_alerts"],
        },
        {
            "id": "auragg_pure_content",
            "label": "AuraGG pure content agents",
            "provider": "herald",
            "lane": "auragg_content",
            "status": "planned",
            "capabilities": ["ideate", "draft", "schedule", "repurpose", "publish_after_approval"],
        },
    ]

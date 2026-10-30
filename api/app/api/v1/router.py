"""
V1 API router — mounts all endpoint groups.

Add new endpoint modules here as features land.
"""

from fastapi import APIRouter

from app.api.v1.endpoints import (
    admin_api_usage,
    ai,
    ai_memories,
    archive,
    artists,
    auth,
    booking_addons,
    bookings,
    broadcasts,
    calendar,
    club,
    contacts,
    conversations,
    crm,
    dashboard,
    documents,
    econ,
    email_automations,
    email_connect,
    email_inbound,
    email_log,
    email_templates,
    flash,
    gala_chat,
    gift_cards,
    google_auth,
    notification_preferences,
    notifications,
    phone_agents,
    pinterest,
    plaid,
    pos,
    push,
    quests,
    quotes,
    references,
    response_templates,
    rewards,
    scrolls,
    session_types,
    sms,
    sms_templates,
    social_reposts,
    stash,
    stripe_connect,
    stripe_webhooks,
    users,
    viber,
    waiver,
    whatsapp,
    workspaces,
)
from app.api.v1.endpoints import (
    messages as _messages_endpoints,
)

api_router = APIRouter()

api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(artists.router)
api_router.include_router(conversations.router)
api_router.include_router(contacts.router)
api_router.include_router(crm.router)
api_router.include_router(_messages_endpoints.router)
api_router.include_router(bookings.router)
api_router.include_router(booking_addons.router)
api_router.include_router(notifications.router)
api_router.include_router(phone_agents.router)
api_router.include_router(notification_preferences.router)
api_router.include_router(documents.router)
api_router.include_router(waiver.router)
api_router.include_router(stripe_connect.router)
api_router.include_router(gift_cards.router)
api_router.include_router(quotes.router)
api_router.include_router(stripe_webhooks.router)
api_router.include_router(quests.router)
api_router.include_router(rewards.router)
api_router.include_router(rewards.wallet_router)
api_router.include_router(dashboard.router)
api_router.include_router(pos.router)
api_router.include_router(econ.router)
api_router.include_router(broadcasts.router)
api_router.include_router(social_reposts.router)
api_router.include_router(email_inbound.router)
api_router.include_router(email_connect.router)
api_router.include_router(archive.router)
api_router.include_router(push.router)
api_router.include_router(club.router)
api_router.include_router(flash.router)
api_router.include_router(email_automations.router)
api_router.include_router(ai.router)
api_router.include_router(ai_memories.router)
api_router.include_router(calendar.router)
api_router.include_router(gala_chat.router)
api_router.include_router(google_auth.router)
api_router.include_router(plaid.router)
api_router.include_router(pinterest.router)
api_router.include_router(email_log.router)
api_router.include_router(email_templates.router)
api_router.include_router(references.router)
api_router.include_router(scrolls.router)
api_router.include_router(session_types.router)
api_router.include_router(stash.router)
api_router.include_router(admin_api_usage.router)
api_router.include_router(response_templates.router)
api_router.include_router(sms.router)
api_router.include_router(sms_templates.router)
api_router.include_router(workspaces.router)
api_router.include_router(viber.router)
api_router.include_router(whatsapp.router)

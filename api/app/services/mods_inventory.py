"""Ryozan's Mods inventory, built on Ark's inventory Mold contract.

Net-new justification: Ryozan had no index of its Mods. The shape is Ark's
Mold base contract (packages/shared-types/src/mold.ts in GalaAI/ark): one
instance config (item type, title, eyebrow, agent, tabs with code prefixes)
plus a feed of entries with a tier and honest gaps. Ryozan cannot import
@ark/ui, so the contract is ported here and rendered with @ryozan/ui.

Each entry says what the Mod does, where its code lives, its tier, and
whether this install has it connected. Nothing here reads a secret value;
connection checks only ask whether a setting is present.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.config import get_settings

if TYPE_CHECKING:
    from collections.abc import Callable

# Ark's tier ladder, lowest to highest.
TIERS = ("unknown", "planned", "scaffolded", "implemented", "validated", "integrated", "runtime-proven")

MOLD_INSTANCE = {
    "id": "ryozan-mods",
    "itemType": "mod",
    "title": "Mods",
    "eyebrow": "RYOZAN // MODS",
    "headline": "Every Mod Ryozan runs, connects or plans",
    "agent": "gala",
    "tabs": [
        {"id": "shop", "label": "Shop", "prefix": "SH"},
        {"id": "agents", "label": "Agents", "prefix": "AG"},
        {"id": "channels", "label": "Channels", "prefix": "CH"},
        {"id": "money", "label": "Money", "prefix": "MN"},
        {"id": "connections", "label": "Connections", "prefix": "CN"},
        {"id": "ark", "label": "Ark", "prefix": "AK"},
    ],
    "source": "feed",
}


def _set(*values: str | None) -> bool:
    return all(bool((value or "").strip()) for value in values)


@dataclass(frozen=True)
class ModDefinition:
    tab: str
    name: str
    alt: str
    tier: str
    source: str
    # None means this Mod has no setting to check; the UI says so instead of guessing.
    connected: Callable[[], bool] | None = None
    gaps: tuple[tuple[str, str], ...] = field(default_factory=tuple)
    route: str | None = None


def _integration(option_id: str) -> Callable[[], bool]:
    def check() -> bool:
        from app.api.v1.endpoints.integrations import integration_options

        return any(item.id == option_id and item.configured for item in integration_options(None).integrations)

    return check


def _settings(*names: str) -> Callable[[], bool]:
    return lambda: _set(*(getattr(get_settings(), name, "") for name in names))


MODS: tuple[ModDefinition, ...] = (
    # The shop's own Mods: the dashboard sections.
    ModDefinition(
        "shop",
        "Huddle",
        "Today, books, clients, availability and aftercare in one hub.",
        "implemented",
        "app/src/pages/dashboard/HuddleLayout.tsx",
        route="/dashboard/huddle",
    ),
    ModDefinition(
        "shop",
        "Hustle",
        "Flash sheets, the store and claims.",
        "implemented",
        "app/src/pages/dashboard/HustlePage.tsx",
        route="/dashboard/hustle",
    ),
    ModDefinition(
        "shop",
        "Courier",
        "The shop inbox across email, SMS, WhatsApp and in-app threads.",
        "implemented",
        "app/src/pages/dashboard/CourierLayout.tsx",
        route="/dashboard/courier",
    ),
    ModDefinition(
        "shop",
        "Guild",
        "Shop crew and progression.",
        "scaffolded",
        "app/src/pages/dashboard/GuildPage.tsx",
        gaps=(("Leaderboards, classes and gauntlets are names on a coming-soon page.", "ROADMAP.md Later"),),
        route="/dashboard/guild",
    ),
    ModDefinition(
        "shop",
        "Vault",
        "Payouts, Stripe and the Eco Mod finance view.",
        "implemented",
        "app/src/pages/dashboard/VaultPage.tsx",
        route="/dashboard/vault",
    ),
    ModDefinition(
        "shop",
        "Herald",
        "Announcements and social reposts.",
        "implemented",
        "app/src/pages/dashboard/HeraldsPage.tsx",
        route="/dashboard/herald",
    ),
    ModDefinition(
        "shop",
        "Rewards and RYO coin",
        "Quests, rewards and the club wallet.",
        "implemented",
        "api/app/services/reward_engine.py",
        gaps=(("No off switch yet for studios that want the core alone.", "ROADMAP.md Next"),),
    ),
    # Agents.
    ModDefinition(
        "agents",
        "Gala",
        "The agent crew coordinator in the portal.",
        "implemented",
        "api/app/api/v1/endpoints/gala_chat.py",
    ),
    ModDefinition(
        "agents", "Booky", "Per-artist booking policy agents follow.", "implemented", "api/app/services/booky_policy.py"
    ),
    ModDefinition(
        "agents",
        "Ryozan MCP server",
        "Role-scoped shop tools for agents over OAuth 2.1.",
        "implemented",
        "api/app/mcp/tools.py",
    ),
    ModDefinition(
        "agents",
        "Model routing",
        "Nebius by default; an Ark Model Gateway can take over.",
        "implemented",
        "api/app/services/ai_routing.py",
        connected=_settings("ARK_MODEL_GATEWAY_KEY"),
    ),
    ModDefinition(
        "agents",
        "Web search",
        "Tavily search for agents.",
        "implemented",
        "api/app/services/web_search.py",
        connected=_settings("TAVILY_API_KEY"),
    ),
    ModDefinition(
        "agents",
        "Phone agents",
        "ElevenLabs voice agents on the shop line.",
        "implemented",
        "api/app/api/v1/endpoints/phone_agents.py",
        connected=_settings("ELEVENLABS_API_KEY"),
    ),
    # Channels.
    ModDefinition(
        "channels",
        "Shop email",
        "Email Foundry templates sent through the platform account.",
        "implemented",
        "api/app/services/email_foundry.py",
        connected=_integration("shop_email"),
    ),
    ModDefinition(
        "channels",
        "Email forwarding",
        "Inbound shop email into Courier.",
        "implemented",
        "api/app/api/v1/endpoints/email_inbound.py",
        connected=_integration("email_forwarding"),
    ),
    ModDefinition(
        "channels",
        "SMS",
        "Twilio text messages in Courier.",
        "implemented",
        "api/app/services/sms.py",
        connected=_settings("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"),
    ),
    ModDefinition(
        "channels",
        "WhatsApp",
        "Meta WhatsApp Business messages.",
        "implemented",
        "api/app/api/v1/endpoints/whatsapp.py",
        connected=_settings("META_WHATSAPP_PHONE_NUMBER_ID", "META_WHATSAPP_ACCESS_TOKEN"),
        gaps=(("services/whatsapp.py is unreferenced code.", "ROADMAP.md Next"),),
    ),
    ModDefinition(
        "channels",
        "Viber",
        "Founder-only Viber API.",
        "implemented",
        "api/app/api/v1/endpoints/viber.py",
        gaps=(("No portal screen yet.", "api/app/api/v1/endpoints/viber.py"),),
    ),
    ModDefinition(
        "channels",
        "Push",
        "Web push notifications.",
        "implemented",
        "api/app/services/push.py",
        connected=_integration("push"),
    ),
    # Money.
    ModDefinition(
        "money",
        "Stripe Connect and POS",
        "Deposits, day-of checkout and payouts.",
        "implemented",
        "api/app/api/v1/endpoints/stripe_connect.py",
        connected=_integration("stripe_connect"),
    ),
    ModDefinition(
        "money",
        "Clover",
        "Read-only Clover sales.",
        "implemented",
        "api/app/api/v1/endpoints/econ.py",
        connected=_settings("CLOVER_ACCESS_TOKEN"),
        gaps=(("Order changes stay read-only until an approval table exists.", "ROADMAP.md Later"),),
    ),
    ModDefinition(
        "money",
        "Mercury",
        "Read-only Mercury banking.",
        "implemented",
        "api/app/api/v1/endpoints/econ.py",
        connected=_settings("MERCURY_API_TOKEN"),
        gaps=(("Payments stay read-only until an approval table exists.", "ROADMAP.md Later"),),
    ),
    ModDefinition(
        "money",
        "Plaid",
        "Bank account link.",
        "implemented",
        "api/app/api/v1/endpoints/plaid.py",
        connected=_integration("plaid"),
    ),
    ModDefinition(
        "money",
        "Ledger",
        "Double-entry ledger library.",
        "scaffolded",
        "packages/ryozan-econ",
        gaps=(("Tested, but the API does not import it yet.", "ROADMAP.md Next"),),
    ),
    ModDefinition(
        "money",
        "Finance writes",
        "Mercury payments, Clover order changes, payroll and tax filing.",
        "planned",
        "ROADMAP.md",
        gaps=(("Waits on an approval table.", "ROADMAP.md Later"),),
    ),
    # Connections.
    ModDefinition(
        "connections",
        "Google Calendar",
        "Calendar sync for artist availability.",
        "implemented",
        "api/app/services/calendar_sync.py",
        connected=_integration("google_calendar"),
    ),
    ModDefinition(
        "connections",
        "Google sign-in",
        "Sign in with a linked Google account.",
        "implemented",
        "api/app/api/v1/endpoints/google_auth.py",
        connected=_integration("google_signin_link"),
    ),
    ModDefinition(
        "connections",
        "Pinterest",
        "Reference boards on bookings.",
        "implemented",
        "api/app/api/v1/endpoints/pinterest.py",
        connected=_integration("pinterest"),
    ),
    ModDefinition(
        "connections",
        "Meta pages",
        "Facebook and Instagram pages for Herald.",
        "implemented",
        "api/app/core/config.py",
        connected=_settings("META_FACEBOOK_PAGE_ID", "META_FACEBOOK_PAGE_ACCESS_TOKEN"),
    ),
    # Ark.
    ModDefinition(
        "ark",
        "Matrix Mod",
        "Eden simulations: Clones play your clients, artists and owner on a test shop.",
        "implemented",
        "GalaAI/ark packages/matrix-mod",
        connected=_settings("ARK_MATRIX_MOD_URL"),
        route="/dashboard/mods?view=matrix",
    ),
    ModDefinition(
        "ark",
        "Ark Pack bridge",
        "Sends Herald reposts into Ark's AInbox.",
        "implemented",
        "api/app/services/pack_bridge.py",
        connected=_settings("ARK_PACK_BRIDGE_URL", "ARK_PACK_BRIDGE_KEY"),
    ),
)


def build_mods_feed() -> dict:
    """Ark's buildMoldFeed rules: PREFIX-NN codes per tab, A to Z, honest gaps."""
    prefixes = {tab["id"]: tab["prefix"] for tab in MOLD_INSTANCE["tabs"]}
    warnings: list[str] = []
    entries: list[dict] = []
    for tab in MOLD_INSTANCE["tabs"]:
        mods = sorted((mod for mod in MODS if mod.tab == tab["id"]), key=lambda mod: mod.name.lower())
        for index, mod in enumerate(mods, start=1):
            if mod.tier not in TIERS:
                warnings.append(f"{mod.name} has an unknown tier {mod.tier}.")
            connected: bool | None = None
            if mod.connected is not None:
                try:
                    connected = bool(mod.connected())
                except Exception:  # noqa: BLE001 - a broken check reads as unknown, never as connected
                    connected = None
                    warnings.append(f"Could not check whether {mod.name} is connected.")
            entries.append(
                {
                    "code": f"{prefixes[mod.tab]}-{index:02d}",
                    "kind": "mod",
                    "group": mod.tab,
                    "tab": mod.tab,
                    "name": mod.name,
                    "alt": mod.alt,
                    "status": mod.tier,
                    "tier": mod.tier,
                    "source": mod.source,
                    "connected": connected,
                    "route": mod.route,
                    "gaps": [list(gap) for gap in mod.gaps],
                }
            )
    return {"status": "live", "entries": entries, "warnings": warnings}

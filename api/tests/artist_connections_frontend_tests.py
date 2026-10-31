"""Focused source contract for the artist Connections workflow.

No existing frontend test owns the cross-entry routing and callback guarantees for
this mobile surface. This small contract keeps every entry on the shared component
and guards the truthful status and existing-client boundaries without adding a test UI.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CONNECTIONS = ROOT / "app" / "src" / "components" / "ArtistConnections.tsx"
MOBILE_PORTAL = ROOT / "app" / "src" / "pages" / "dashboard" / "MobilePortalHome.tsx"
DASHBOARD_STYLES = ROOT / "app" / "src" / "styles" / "dashboard.css"
ARTIST_PROFILE = ROOT / "app" / "src" / "pages" / "dashboard" / "ArtistProfileEditor.tsx"
HUSTLE = ROOT / "app" / "src" / "pages" / "dashboard" / "HustlePage.tsx"
VAULT = ROOT / "app" / "src" / "pages" / "dashboard" / "VaultPage.tsx"
POS_MODAL = ROOT / "app" / "src" / "components" / "POSModal.tsx"


class ArtistConnectionsFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.connections = CONNECTIONS.read_text(encoding="utf-8")
        cls.portal = MOBILE_PORTAL.read_text(encoding="utf-8")
        cls.styles = DASHBOARD_STYLES.read_text(encoding="utf-8")
        cls.artist_profile = ARTIST_PROFILE.read_text(encoding="utf-8")
        cls.hustle = HUSTLE.read_text(encoding="utf-8")
        cls.vault = VAULT.read_text(encoding="utf-8")
        cls.pos_modal = POS_MODAL.read_text(encoding="utf-8")

    def test_existing_personal_connection_clients_remain_the_authorities(self):
        for call in (
            "readOffered(offered, 'stripe_connect', stripeConnect.status)",
            "stripeConnect.onboard()",
            "readOffered(offered, 'email_forwarding', emailConnect.status)",
            "readOffered(offered, 'google_calendar', googleAuth.getStatus)",
            "googleAuth.getConnectUrl()",
            "readOffered(offered, 'pinterest', pinterestAuth.getStatus)",
            "pinterestAuth.getConnectUrl()",
        ):
            self.assertIn(call, self.connections)

        self.assertIn("Promise.allSettled", self.connections)
        self.assertIn('<EmailConnectQuest mode="full" />', self.connections)
        # Only live services render; the static Venue.ink handoff card was retired.
        self.assertIn("isShownConnection", self.connections)
        self.assertNotIn("Venue.ink", self.connections)
        for shop_wide_connector in (
            "Clover",
            "Mercury",
            "Plaid",
            "Google Business",
            "Drive",
            "Dropbox",
        ):
            self.assertNotIn(shop_wide_connector, self.connections)

    def test_stripe_states_only_call_fully_enabled_accounts_ready(self):
        for state_copy in (
            "Checking",
            "Unavailable",
            "Not connected",
            "Setup incomplete",
            "Payment ready",
        ):
            self.assertIn(state_copy, self.connections)

        self.assertIn("!status.value.routing_ready", self.connections)
        self.assertIn("!status.value.charges_enabled", self.connections)
        self.assertIn("!status.value.payouts_enabled", self.connections)
        self.assertIn("status.value.provider_state === 'unavailable'", self.connections)
        self.assertIn("const requirementCount = status.value.requirements_due", self.connections)
        self.assertIn("requirementCount === 1 ? 'requirement' : 'requirements'", self.connections)
        self.assertIn("stripe.value.provider_state === 'unavailable'", self.connections)
        unavailable_action = self.connections.index("stripe.value.provider_state === 'unavailable'")
        setup_action = self.connections.index("stripe.value.connected ? 'Continue setup' : 'Start setup'")
        self.assertLess(unavailable_action, setup_action)

    def test_stripe_callbacks_refresh_exactly_once_and_clear_after_return(self):
        self.assertEqual(self.connections.count("stripeConnect.onboard()"), 1)
        self.assertIn("handledStripeRefreshRef.current === callbackKey", self.connections)
        self.assertIn("handledStripeRefreshRef.current = callbackKey", self.connections)
        self.assertIn("void openStripeOnboarding(true)", self.connections)
        self.assertIn("if (!active || stripeReturn !== 'return') return", self.connections)
        self.assertIn("params.delete('stripe')", self.connections)
        self.assertIn(
            "navigate(`${location.pathname}${search ? `?${search}` : ''}`, { replace: true })", self.connections
        )

    def test_hub_xar_and_pos_share_one_connections_component(self):
        self.assertEqual(self.portal.count("<ArtistConnections />"), 1)
        # Collectors have no Hub tab, so their Connections sheet opens over You.
        self.assertIn("`?portal=${isCollector ? 'you' : 'hub'}&workflow=connections`", self.portal)
        self.assertIn("card.id === 'artist-connections'", self.portal)
        self.assertIn(
            "onOpenConnections={canUseArtistConnections ? () => openArtistConnections() : undefined}", self.portal
        )
        self.assertIn('data-tour="connections"', self.portal)
        self.assertIn("Stripe · Google · Pinterest · Email", self.portal)

    def test_connections_controls_are_phone_sized_and_pos_is_a_handoff(self):
        self.assertIn(".ryozan-artist-connections", self.styles)
        self.assertIn(".ryozan-artist-connection__copy > button", self.styles)
        self.assertGreaterEqual(self.styles.count("min-height: 44px"), 2)
        self.assertIn(".ryozan-pos-connections-entry", self.styles)
        self.assertIn("Open Connections", self.portal)

    def test_legacy_artist_surfaces_route_to_the_single_owner(self):
        self.assertNotIn("stripeConnect.onboard()", self.artist_profile)
        self.assertNotIn("stripeConnect.onboard()", self.hustle)
        self.assertNotIn("stripeConnect.onboard()", self.vault)
        self.assertEqual(self.connections.count("stripeConnect.onboard()"), 1)
        self.assertEqual(self.vault.count("stripeConnect.adminOnboard("), 1)
        self.assertNotIn("stripeConnect.adminOnboard(", self.hustle)
        for source in (self.artist_profile, self.hustle, self.vault):
            self.assertIn("portal=hub&workflow=connections", source)

        self.assertIn("!canSwitchCreator && (activeStashTab === 'econ'", self.hustle)
        self.assertIn('<span className="pos-int-dot" />\n              Stripe', self.pos_modal)


if __name__ == "__main__":
    unittest.main()

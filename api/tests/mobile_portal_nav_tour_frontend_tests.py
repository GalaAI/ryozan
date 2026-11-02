"""Phone contract for the shared Portal nav and artist tour overlay.

No existing focused test owns the unread-badge footprint or keeps the tour above
the in-flow bottom nav. This source contract guards those regression boundaries
without duplicating the shared TabBar or introducing a second navigation owner.
"""

import tarfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MOBILE_PORTAL = ROOT / "app" / "src" / "pages" / "dashboard" / "MobilePortalHome.tsx"
GALA_PANEL = ROOT / "app" / "src" / "components" / "GalaPanel.tsx"
API_CLIENT = ROOT / "app" / "src" / "lib" / "api.ts"
DASHBOARD_STYLES = ROOT / "app" / "src" / "styles" / "dashboard.css"
UI_ARCHIVE = ROOT / "app" / "vendor" / "ryozan-ui-0.2.2.tgz"
DASHBOARD_HOME = ROOT / "app" / "src" / "pages" / "dashboard" / "DashboardHome.tsx"


class MobilePortalNavTourFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.portal = MOBILE_PORTAL.read_text(encoding="utf-8")
        cls.gala_panel = GALA_PANEL.read_text(encoding="utf-8")
        cls.api_client = API_CLIENT.read_text(encoding="utf-8")
        cls.styles = DASHBOARD_STYLES.read_text(encoding="utf-8")
        with tarfile.open(UI_ARCHIVE, "r:gz") as archive:
            tab_bar = archive.extractfile("package/src/components/TabBar.tsx")
            icon_button = archive.extractfile("package/src/components/IconButton.tsx")
            assert tab_bar is not None
            assert icon_button is not None
            cls.tab_bar = tab_bar.read().decode("utf-8").replace("\r\n", "\n")
            cls.icon_button = icon_button.read().decode("utf-8").replace("\r\n", "\n")
        cls.dashboard_home = DASHBOARD_HOME.read_text(encoding="utf-8")

    def test_phone_nav_caps_badge_data_and_fixes_its_visual_footprint(self):
        self.assertEqual(self.portal.count("badge: visibleUnreadCount"), 2)
        self.assertIn('className="ryozan-tab-bar__badge"', self.tab_bar)
        self.assertIn("width: '24px'", self.tab_bar)
        self.assertIn("maxWidth: '24px'", self.tab_bar)
        self.assertIn("padding: 0", self.tab_bar)
        self.assertIn("t.badge > 99 ? '99+' : t.badge", self.tab_bar)
        self.assertIn("${t.label}, ${t.badge} unread", self.tab_bar)
        self.assertIn("badge > 99 ? '99+' : badge", self.icon_button)
        self.assertIn("maxWidth: '24px'", self.icon_button)

    def test_scope_changes_clear_old_bookings_and_terminal_rows_stay_closed(self):
        self.assertIn("setBookings([])", self.dashboard_home)
        self.assertIn("setUnreadCount(0)", self.dashboard_home)
        self.assertIn("'no_show', 'expired'", self.portal)
        self.assertEqual(
            self.portal.count("!terminalStatuses.has(booking.status.toLowerCase())"),
            4,
        )

    def test_loading_pass_does_not_claim_empty_live_booking_state(self):
        self.assertIn("titleAccent: 'Loading'", self.portal)
        self.assertIn("{ label: 'Deposit', value: 'Waiting' }", self.portal)
        self.assertIn("const pass: ComponentProps<typeof SessionPass> = loading", self.portal)

    def test_artist_tour_is_contained_above_the_shared_nav(self):
        self.assertIn('<div className="ryozan-artist-tour-layer">', self.portal)
        self.assertIn('<aside className="ryozan-artist-tour"', self.portal)
        self.assertIn(".ryozan-artist-tour-layer {", self.styles)
        self.assertIn("inset: 0 0 var(--ryozan-portal-nav-height) 0", self.styles)
        self.assertIn("overflow: hidden", self.styles)
        self.assertIn("pointer-events: none", self.styles)
        self.assertIn(".ryozan-artist-tour {", self.styles)
        self.assertIn("max-height: calc(100% - 24px)", self.styles)
        self.assertIn("pointer-events: auto", self.styles)

    def test_workflow_height_has_one_owner_and_pos_cannot_override_it(self):
        self.assertIn('placement="full"', self.portal)
        self.assertNotIn("dockedTop=", self.portal)
        self.assertNotIn("compactAgentWorkflow", self.portal)
        self.assertNotIn(
            ".ryozan-agent-chat-sheet:has(.ryozan-hub-workflow-card--pos) {",
            self.styles,
        )
        self.assertNotIn(
            ".ryozan-agent-chat-sheet:has(.ryozan-hub-workflow-card--pos .pos-numpad-wrap)",
            self.styles,
        )

    def test_authenticated_portal_ai_uses_the_role_correct_home_agent(self):
        self.assertIn("const canUsePortalAi = Boolean(user)", self.portal)
        self.assertNotIn("const canUsePortalAi = isFounderOrAdmin", self.portal)
        self.assertIn("const homeAgent = isCollector", self.portal)
        # Legacy ids stay routable; the visible names are the Bandits.
        self.assertIn("id: 'smoku', name: 'Rochishin', role: 'Tattoo guide'", self.portal)
        self.assertIn("\n    : isFounderOrAdmin\n      ? {", self.portal)
        self.assertIn("id: 'gala', name: 'Gala', role: 'Orchestrator'", self.portal)
        self.assertIn("id: 'spyder', name: 'Sōkō', role: 'Operator · back of house'", self.portal)
        self.assertIn("smoku: [", self.portal)

    def test_gala_actions_use_typed_intents_and_render_execution_receipts(self):
        self.assertIn("export type GalaActionIntent", self.api_client)
        self.assertIn("action_intent?: GalaActionIntent", self.api_client)
        self.assertIn("action_intent: actionIntent", self.gala_panel)
        self.assertIn("actionIntent: 'business_stats.read'", self.gala_panel)
        self.assertIn("actionIntent: 'schedule.read'", self.gala_panel)
        self.assertIn("<summary>Execution receipt</summary>", self.gala_panel)
        self.assertIn("card.id === 'report-up'", self.portal)
        self.assertIn("action_intent: 'business_stats.read'", self.portal)
        self.assertIn("<GalaStructuredReply response={response} />", self.portal)
        self.assertIn("No success receipt was created.", self.portal)


if __name__ == "__main__":
    unittest.main()

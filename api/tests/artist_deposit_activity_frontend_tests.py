"""Source contract for the artist-owned deposit activity surface.

No existing frontend test covers the boundary between artist booking deposits and
founder E-con. This contract keeps one shared activity owner in desktop Vault and
the phone Hub while retaining the shop-wide finance destination for managers.
"""

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
COMPONENT = ROOT / "app" / "src" / "components" / "ArtistDepositActivity.tsx"
VAULT = ROOT / "app" / "src" / "pages" / "dashboard" / "VaultPage.tsx"
MOBILE = ROOT / "app" / "src" / "pages" / "dashboard" / "MobilePortalHome.tsx"
HUDDLE = ROOT / "app" / "src" / "pages" / "dashboard" / "HuddleLayout.tsx"
STYLES = ROOT / "app" / "src" / "styles" / "dashboard.css"


class ArtistDepositActivityFrontendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.component = COMPONENT.read_text(encoding="utf-8")
        cls.vault = VAULT.read_text(encoding="utf-8")
        cls.mobile = MOBILE.read_text(encoding="utf-8")
        cls.huddle = HUDDLE.read_text(encoding="utf-8")
        cls.styles = STYLES.read_text(encoding="utf-8")

    def test_shared_component_reads_every_role_scoped_page_for_actual_artist(self):
        self.assertIn("bookingsApi.list({ limit: 100 })", self.component)
        self.assertIn("for (let skip = 100; skip < firstPage.total; skip += 100)", self.component)
        self.assertIn("booking.artist_id === user.id", self.component)
        self.assertIn("user?.role !== 'artist'", self.component)
        self.assertIn('id="artist-deposit-activity"', self.component)
        self.assertIn("{isArtist && <ArtistDepositActivity />}", self.vault)

    def test_shared_component_exposes_truthful_paid_due_and_failure_states(self):
        self.assertIn("Customer paid", self.component)
        self.assertIn("Quoted due", self.component)
        self.assertIn("Paid is the customer charge recorded after checkout", self.component)
        self.assertIn("'completed'", self.component)
        self.assertIn("'no_show'", self.component)
        self.assertIn("Paid · customer charge", self.component)
        self.assertIn("Due · quoted", self.component)
        self.assertIn("No zero-dollar history is being substituted", self.component)

    def test_artist_huddle_routes_to_phone_hub_while_managers_keep_econ(self):
        self.assertIn("userRole === 'artist'", self.huddle)
        self.assertIn("!closedDepositStatuses.has(booking.status.toLowerCase())", self.huddle)
        self.assertIn("/dashboard?portal=hub&workflow=deposits", self.huddle)
        self.assertIn("/dashboard/hustle?stash=econ&page=command&focus=deposits", self.huddle)
        self.assertIn("Deposit activity", self.huddle)

    def test_phone_hub_owns_artist_deposit_deep_link(self):
        self.assertIn("canViewArtistDeposits", self.mobile)
        self.assertIn("workflow=deposits", self.mobile)
        self.assertIn('<ArtistDepositActivity surface="workflow" />', self.mobile)
        self.assertIn("+Deposits", self.mobile)

    def test_phone_styles_keep_deposit_rows_bounded(self):
        self.assertIn(".artist-deposit-summary", self.styles)
        self.assertIn(".artist-deposit-row", self.styles)
        self.assertIn("@media (max-width: 420px)", self.styles)
        self.assertIn("grid-template-columns: 1fr;", self.styles)


if __name__ == "__main__":
    unittest.main()

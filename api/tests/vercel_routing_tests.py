import json
import re
import tarfile
import unittest
from pathlib import Path

from app.core.config import Settings


class VercelRoutingTests(unittest.TestCase):
    def test_vercel_configs_proxy_api_and_health_routes_to_backend(self):
        repo_root = Path(__file__).resolve().parents[2]
        root_config = json.loads((repo_root / "app" / "vercel.json").read_text())

        rewrites = {(entry["source"], entry["destination"]) for entry in root_config.get("rewrites", [])}

        self.assertIn(
            ("/api/:path*", "https://api.ryozan.app/api/:path*"),
            rewrites,
        )
        self.assertIn(
            ("/health", "https://api.ryozan.app/health"),
            rewrites,
        )
        self.assertIn(
            ("/health/:path*", "https://api.ryozan.app/health/:path*"),
            rewrites,
        )
        self.assertIn(
            ("/api/v1/bookings/", "https://api.ryozan.app/api/v1/bookings/"),
            rewrites,
        )
        self.assertIn(
            ("/api/v1/notifications/", "https://api.ryozan.app/api/v1/notifications/"),
            rewrites,
        )

    def test_spa_rewrite_excludes_debug_paths(self):
        repo_root = Path(__file__).resolve().parents[2]
        root_config = json.loads((repo_root / "app" / "vercel.json").read_text())
        spa_rewrites = [
            entry["source"]
            for entry in root_config.get("rewrites", [])
            if entry.get("destination") == "/index.html" and not entry.get("has")
        ]
        self.assertEqual(len(spa_rewrites), 1)

        rewrite_pattern = re.compile("^" + spa_rewrites[0] + "$")
        self.assertIsNone(rewrite_pattern.match("/debug"))
        self.assertIsNone(rewrite_pattern.match("/debug/status"))
        self.assertIsNotNone(rewrite_pattern.match("/dashboard"))

    def test_every_local_rewrite_target_exists_and_public_booking_uses_the_spa(self):
        repo_root = Path(__file__).resolve().parents[2]
        root_config = json.loads((repo_root / "app" / "vercel.json").read_text())
        rewrites = root_config.get("rewrites", [])
        for entry in rewrites:
            destination = entry["destination"]
            if destination.startswith("/") and destination != "/index.html":
                self.assertTrue(
                    (repo_root / "app" / "public" / destination.lstrip("/")).is_file(),
                    f"{entry['source']} rewrites to {destination}, which the build does not ship",
                )
        explicit = {entry["source"] for entry in rewrites}
        spa_source = next(
            entry["source"] for entry in rewrites if entry["destination"] == "/index.html" and not entry.get("has")
        )
        spa = re.compile("^" + spa_source + "$")
        # /book, /ask and /checkin are SPA routes now; the catch-all serves them.
        for path in ("/book", "/book/test-artist", "/book/sesh", "/book/confirm", "/ask", "/checkin"):
            self.assertNotIn(path, explicit)
            self.assertIsNotNone(spa.match(path), path)

    def test_app_subdomain_privacy_uses_product_policy(self):
        repo_root = Path(__file__).resolve().parents[2]
        root_config = json.loads((repo_root / "app" / "vercel.json").read_text())
        privacy_rewrites = [entry for entry in root_config.get("rewrites", []) if entry.get("source") == "/privacy"]

        self.assertEqual(
            privacy_rewrites[0],
            {
                "source": "/privacy",
                "has": [
                    {
                        "type": "host",
                        "value": "app.ryozan.app",
                    }
                ],
                "destination": "/index.html",
            },
        )
        self.assertEqual(privacy_rewrites[1]["destination"], "/privacy.html")

    def test_cors_regex_has_no_hosted_preview_default(self):
        # Preview-deploy origins are env-only; a fresh install accepts none.
        self.assertEqual(Settings(_env_file=None).CORS_ORIGIN_REGEX, "")
        pattern = re.compile(r"https://ryozan-app-[a-z0-9-]+\.vercel\.app")
        settings = Settings(_env_file=None, CORS_ORIGIN_REGEX=pattern.pattern)
        self.assertIsNotNone(re.fullmatch(settings.CORS_ORIGIN_REGEX, "https://ryozan-app-git-main.vercel.app"))
        self.assertIsNone(re.fullmatch(settings.CORS_ORIGIN_REGEX, "https://other-app.vercel.app"))

    def test_mobile_viewport_contract_matches_the_proven_login_geometry(self):
        repo_root = Path(__file__).resolve().parents[2]
        index_html = (repo_root / "app" / "index.html").read_text(encoding="utf-8")
        root_css = (repo_root / "app" / "src" / "index.css").read_text(encoding="utf-8")
        login_css = (repo_root / "app" / "src" / "components" / "LoginGate.css").read_text(encoding="utf-8")
        dashboard_css = (repo_root / "app" / "src" / "styles" / "dashboard.css").read_text(encoding="utf-8")
        public_css = (repo_root / "app" / "src" / "styles" / "v2-redesign.css").read_text(encoding="utf-8")

        self.assertIn(
            '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent" />',
            index_html,
        )
        self.assertRegex(root_css, r"#root\s*\{[^}]*height:\s*100dvh")
        self.assertIn("@media (display-mode: standalone)", root_css)
        self.assertRegex(
            root_css,
            r"html:root\[data-ryozan-app-route\][\s\S]*?height:\s*100lvh",
        )
        self.assertRegex(
            login_css,
            r"\.lg-root\s*\{[^}]*height:\s*100dvh;[^}]*min-height:\s*100svh",
        )
        self.assertRegex(
            dashboard_css,
            r"\.ryozan-mobile-portal-home\s*\{[^}]*height:\s*100%;[^}]*min-height:\s*0",
        )
        self.assertEqual(len(re.findall(r'<meta\s+name="viewport"', index_html)), 1)
        self.assertNotRegex(
            dashboard_css,
            r"body:has\(\.app-view--portal-home\)\s+#root\s*\{",
        )
        self.assertNotRegex(
            dashboard_css,
            r"\.app-view--portal-home\s*\{[^}]*position:\s*fixed",
        )
        self.assertRegex(public_css, r"\.hero\s*\{[^}]*height:\s*100dvh")

    def test_mobile_tab_bar_stays_in_shell_flow_and_contains_safe_area(self):
        repo_root = Path(__file__).resolve().parents[2]
        tab_bar_path = repo_root / "packages" / "ui" / "src" / "components" / "TabBar.tsx"
        portal_shell_path = repo_root / "packages" / "ui" / "src" / "screens" / "PortalShell.tsx"
        app_package = json.loads((repo_root / "app" / "package.json").read_text(encoding="utf-8"))
        ui_package = json.loads((repo_root / "packages" / "ui" / "package.json").read_text(encoding="utf-8"))
        vendor_name = f"ryozan-ui-{ui_package['version']}.tgz"
        vendor_path = repo_root / "app" / "vendor" / vendor_name

        self.assertEqual(app_package["dependencies"]["@ryozan/ui"], f"file:vendor/{vendor_name}")

        with tarfile.open(vendor_path, "r:gz") as archive:
            vendored_tab_bar = archive.extractfile("package/src/components/TabBar.tsx")
            vendored_portal_shell = archive.extractfile("package/src/screens/PortalShell.tsx")
            self.assertIsNotNone(vendored_tab_bar)
            self.assertIsNotNone(vendored_portal_shell)
            tab_bar = vendored_tab_bar.read().decode("utf-8").replace("\r\n", "\n")
            portal_shell = vendored_portal_shell.read().decode("utf-8").replace("\r\n", "\n")

        self.assertIn("position: 'relative'", tab_bar)
        self.assertNotIn("position: 'fixed'", tab_bar)
        self.assertNotIn("createPortal", portal_shell)
        self.assertIn(
            "max(var(--space-3), env(safe-area-inset-bottom, 0px))",
            tab_bar,
        )
        self.assertIn(
            "max(var(--space-3), env(safe-area-inset-bottom, 0px))",
            portal_shell,
        )

        if tab_bar_path.exists() and portal_shell_path.exists():
            self.assertEqual(tab_bar_path.read_text(encoding="utf-8"), tab_bar)
            self.assertEqual(portal_shell_path.read_text(encoding="utf-8"), portal_shell)


if __name__ == "__main__":
    unittest.main()

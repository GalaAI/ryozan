import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest

from fastapi import Response
from fastapi.testclient import TestClient

from main import _apply_security_headers, _should_serve_spa_path, app  # noqa: E402


class SpaFallbackSecurityTests(unittest.TestCase):
    def test_allows_legitimate_spa_routes(self):
        allowed = [
            "",
            "portal",
            "dashboard",
            "contact",
            "quote/public/token-123",
            "artists/justin",
        ]

        for path in allowed:
            with self.subTest(path=path):
                self.assertTrue(_should_serve_spa_path(path))

    def test_blocks_common_exploit_and_static_file_probes(self):
        blocked = [
            "wp-login.php",
            "wp-admin/css/",
            "wordpress/admin.php",
            "phpmyadmin/index.php",
            "admin/function.php",
            ".env",
            ".git/config",
            "uploads/shell.php",
            "backup.sql",
            "config.php",
            "logs/app.log",
            "../secret",
            "debug",
            "debug/status",
        ]

        for path in blocked:
            with self.subTest(path=path):
                self.assertFalse(_should_serve_spa_path(path))

    def test_security_headers_are_applied(self):
        response = Response()

        _apply_security_headers(response)

        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["X-Frame-Options"], "DENY")
        self.assertIn("max-age=31536000", response.headers["Strict-Transport-Security"])
        self.assertIn("frame-ancestors 'none'", response.headers["Content-Security-Policy"])
        self.assertIn("media-src 'self' blob: https:", response.headers["Content-Security-Policy"])
        self.assertIn("payment=()", response.headers["Permissions-Policy"])

    def test_public_docs_are_disabled_by_default(self):
        route_paths = {route.path for route in app.routes if hasattr(route, "path")}

        self.assertNotIn("/docs", route_paths)
        self.assertNotIn("/redoc", route_paths)
        self.assertNotIn("/openapi.json", route_paths)

    def test_config_health_is_not_public_by_default(self):
        client = TestClient(app)

        response = client.get("/health/config")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")


if __name__ == "__main__":
    unittest.main()

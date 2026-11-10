import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

import main
from app.api.v1.endpoints.studio import build_studio_settings
from app.core.config import Settings
from app.services import shop_pack
from app.services.shop_pack import PACK_MAX_BYTES, pack_file_path, pack_upstream_url

BASE = "https://pack.example.test/shops/example"
_REAL_ASYNC_CLIENT = httpx.AsyncClient
SCOPE = "bttco"


class _RecordingTransport(httpx.AsyncBaseTransport):
    """Answers every upstream request from a handler and records what was asked."""

    def __init__(self, handler):
        self.handler = handler
        self.requests: list[httpx.Request] = []

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


class ShopPackSettingsTests(unittest.TestCase):
    def test_blank_means_no_pack(self):
        settings = Settings(_env_file=None, SHOP_PACK_BASE_URL="", SHOP_PACK_SCOPE="")
        self.assertFalse(settings.shop_pack_enabled)

    def test_valid_pack_is_normalized(self):
        settings = Settings(_env_file=None, SHOP_PACK_BASE_URL=f"{BASE}/", SHOP_PACK_SCOPE="@bttco")
        self.assertTrue(settings.shop_pack_enabled)
        self.assertEqual(settings.SHOP_PACK_BASE_URL, BASE)
        self.assertEqual(settings.SHOP_PACK_SCOPE, SCOPE)

    def test_rejects_non_https_and_bad_scopes(self):
        bad = [
            {"SHOP_PACK_BASE_URL": "http://pack.example.test", "SHOP_PACK_SCOPE": SCOPE},
            {"SHOP_PACK_BASE_URL": f"{BASE}?x=1", "SHOP_PACK_SCOPE": SCOPE},
            {"SHOP_PACK_BASE_URL": "//pack.example.test", "SHOP_PACK_SCOPE": SCOPE},
            {"SHOP_PACK_BASE_URL": BASE, "SHOP_PACK_SCOPE": "BTTCO"},
            {"SHOP_PACK_BASE_URL": BASE, "SHOP_PACK_SCOPE": "bt/tco"},
            {"SHOP_PACK_BASE_URL": BASE, "SHOP_PACK_SCOPE": "-bttco"},
            {"SHOP_PACK_BASE_URL": BASE, "SHOP_PACK_SCOPE": ""},
            {"SHOP_PACK_BASE_URL": "", "SHOP_PACK_SCOPE": SCOPE},
        ]
        for values in bad:
            with self.subTest(values=values), self.assertRaises(ValidationError):
                Settings(_env_file=None, **values)

    def test_studio_settings_report_the_scope_only_with_a_pack(self):
        without = main.settings.model_copy(update={"SHOP_PACK_BASE_URL": "", "SHOP_PACK_SCOPE": ""})
        with_pack = main.settings.model_copy(update={"SHOP_PACK_BASE_URL": BASE, "SHOP_PACK_SCOPE": SCOPE})
        self.assertEqual(build_studio_settings(without).shop_pack_scope, "")
        self.assertEqual(build_studio_settings(with_pack).shop_pack_scope, SCOPE)


class ShopPackPathTests(unittest.TestCase):
    def test_accepts_files_under_the_scope(self):
        for path in (
            "@bttco/email-foundry/1.0.0/adapter.js",
            "@bttco/email-foundry/1.0.0/Email Foundry.dc.html",
            "@bttco/booking-flow/1.0.0/assets/thumbs/flash-crane.PNG",
            "@bttco/booking-flow/1.0.0/vendor/fonts/display.woff2",
        ):
            with self.subTest(path=path):
                self.assertEqual(pack_file_path(path, SCOPE), path)

    def test_rejects_other_scopes_traversal_and_unlisted_files(self):
        for path in (
            "",
            "@other/email-foundry/1.0.0/adapter.js",
            "@bttco-evil/email-foundry/1.0.0/adapter.js",
            "bttco/email-foundry/1.0.0/adapter.js",
            "/@bttco/email-foundry/1.0.0/adapter.js",
            "@bttco/../other/adapter.js",
            "@bttco/email-foundry/../../secret.js",
            "@bttco/email-foundry/./adapter.js",
            "@bttco//adapter.js",
            "@bttco/email-foundry\\..\\adapter.js",
            "@bttco/.env",
            "@bttco/email-foundry/1.0.0/.hidden.js",
            "@bttco/email-foundry/1.0.0/README.md",
            "@bttco/email-foundry/1.0.0/package.tgz",
            "@bttco/email-foundry/1.0.0/adapter",
            "@bttco/email-foundry/1.0.0/a\x00.js",
            "@bttco",
            "@bttco/",
        ):
            with self.subTest(path=path):
                self.assertIsNone(pack_file_path(path, SCOPE))

    def test_no_scope_serves_nothing(self):
        self.assertIsNone(pack_file_path("@bttco/a/b.js", ""))

    def test_upstream_url_preserves_the_path(self):
        self.assertEqual(
            pack_upstream_url("@bttco/email-foundry/1.0.0/Email Foundry.dc.html", f"{BASE}/"),
            f"{BASE}/@bttco/email-foundry/1.0.0/Email%20Foundry.dc.html",
        )
        self.assertEqual(
            pack_upstream_url("@bttco/a/b%2e%2e.js", BASE),
            f"{BASE}/@bttco/a/b%252e%252e.js",
        )


class ShopPackRouteTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(main.app)

    def _configure(self, base=BASE, scope=SCOPE):
        stack = [
            patch.object(main.settings, "SHOP_PACK_BASE_URL", base),
            patch.object(main.settings, "SHOP_PACK_SCOPE", scope),
        ]
        for item in stack:
            item.start()
            self.addCleanup(item.stop)

    def _upstream(self, handler) -> _RecordingTransport:
        transport = _RecordingTransport(handler)

        def client_factory(**kwargs):
            return _REAL_ASYNC_CLIENT(transport=transport, **kwargs)

        patcher = patch.object(shop_pack.httpx, "AsyncClient", side_effect=client_factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        return transport

    def test_no_pack_is_404_without_an_upstream_call(self):
        self._configure(base="", scope="")
        transport = self._upstream(lambda request: httpx.Response(200, content=b"x"))
        response = self.client.get("/dependencies/@bttco/email-foundry/1.0.0/adapter.js")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(transport.requests, [])

    def test_other_scope_and_traversal_are_404_without_an_upstream_call(self):
        self._configure()
        transport = self._upstream(lambda request: httpx.Response(200, content=b"x"))
        for path in (
            "/dependencies/@other/email-foundry/1.0.0/adapter.js",
            "/dependencies/@bttco/%2e%2e/secret.js",
            "/dependencies/@bttco/email-foundry/..%2F..%2Fsecret.js",
            "/dependencies/@bttco/email-foundry/1.0.0/README.md",
            "/dependencies/%2F%2Fevil.example.test/a.js",
        ):
            with self.subTest(path=path):
                self.assertEqual(self.client.get(path).status_code, 404)
        self.assertEqual(transport.requests, [])

    def test_streams_the_file_same_origin_with_an_extension_content_type(self):
        self._configure()
        transport = self._upstream(
            lambda request: httpx.Response(
                200,
                content=b"<html><head></head></html>",
                headers={"content-type": "application/octet-stream", "etag": '"v1"', "set-cookie": "a=b"},
            )
        )
        response = self.client.get("/dependencies/@bttco/email-foundry/1.0.0/Email%20Foundry.dc.html")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"<html><head></head></html>")
        self.assertEqual(response.headers["content-type"], "text/html; charset=utf-8")
        self.assertEqual(response.headers["cache-control"], "public, max-age=300")
        self.assertEqual(response.headers["etag"], '"v1"')
        self.assertNotIn("set-cookie", response.headers)
        self.assertEqual(str(transport.requests[0].url), f"{BASE}/@bttco/email-foundry/1.0.0/Email%20Foundry.dc.html")

    def test_revalidation_reaches_the_upstream_and_304_passes_back(self):
        self._configure()
        transport = self._upstream(lambda request: httpx.Response(304, headers={"etag": '"v1"'}))
        response = self.client.get(
            "/dependencies/@bttco/email-foundry/1.0.0/adapter.js", headers={"If-None-Match": '"v1"'}
        )
        self.assertEqual(response.status_code, 304)
        self.assertEqual(transport.requests[0].headers["if-none-match"], '"v1"')

    def test_missing_upstream_file_is_404_and_upstream_failure_is_502(self):
        self._configure()
        for status, expected in ((404, 404), (403, 404), (500, 502), (302, 502)):
            with self.subTest(status=status):
                self._upstream(lambda request, status=status: httpx.Response(status, headers={"location": "https://x"}))
                response = self.client.get("/dependencies/@bttco/email-foundry/1.0.0/adapter.js")
                self.assertEqual(response.status_code, expected)

    def test_unreachable_upstream_is_502(self):
        self._configure()

        def fail(request):
            raise httpx.ConnectTimeout("timed out", request=request)

        self._upstream(fail)
        response = self.client.get("/dependencies/@bttco/email-foundry/1.0.0/adapter.js")
        self.assertEqual(response.status_code, 502)

    def test_declared_size_past_the_cap_is_refused(self):
        self._configure()
        self._upstream(
            lambda request: httpx.Response(200, content=b"x", headers={"content-length": str(PACK_MAX_BYTES + 1)})
        )
        response = self.client.get("/dependencies/@bttco/booking-flow/1.0.0/assets/big.png")
        self.assertEqual(response.status_code, 502)

    def test_undeclared_size_is_cut_at_the_cap(self):
        self._configure()

        async def chunks():
            for _ in range(3):
                yield b"x" * (PACK_MAX_BYTES // 2)

        self._upstream(lambda request: httpx.Response(200, content=chunks()))
        with patch.object(shop_pack, "PACK_MAX_BYTES", PACK_MAX_BYTES):
            response = self.client.get("/dependencies/@bttco/booking-flow/1.0.0/assets/big.png")
        self.assertEqual(response.status_code, 200)
        self.assertLessEqual(len(response.content), PACK_MAX_BYTES)

    def test_svg_is_sandboxed(self):
        self._configure()
        self._upstream(lambda request: httpx.Response(200, content=b"<svg/>"))
        response = self.client.get("/dependencies/@bttco/email-foundry/1.0.0/assets/mark.svg")
        self.assertEqual(response.headers["content-type"], "image/svg+xml")
        self.assertEqual(response.headers["content-security-policy"], "sandbox")


if __name__ == "__main__":
    unittest.main()

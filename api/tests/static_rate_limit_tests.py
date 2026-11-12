"""Static files and SPA routes stay outside the per-IP API rate limit.

SlowAPI's middleware resolves a request to the last route that fully matches, so
/assets/*.js also resolves to the SPA catch-all. Without an exemption a judge who
clicks through a few screens spends the per-IP budget on assets, the next chunk
comes back as a JSON 429, and the portal crashes.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.testclient import TestClient
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.core.rate_limit import get_rate_limit_key, limiter

MAIN = (Path(__file__).resolve().parents[1] / "main.py").read_text(encoding="utf-8")


class StaticRateLimitTests(unittest.TestCase):
    def test_defaults_leave_room_for_a_browsing_session(self):
        defaults = [str(limit.limit) for group in limiter._default_limits for limit in group]
        self.assertIn("300 per 1 minute", defaults)

    def test_spa_catch_all_is_exempt_in_main(self):
        start = MAIN.index('@app.get("/{full_path:path}"')
        self.assertIn("@limiter.exempt", MAIN[start : MAIN.index("async def spa_fallback", start)])

    def test_exempt_catch_all_keeps_assets_out_of_the_budget(self):
        tight = Limiter(key_func=get_rate_limit_key, default_limits=["5/minute"])
        app = FastAPI()
        app.state.limiter = tight
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        app.add_middleware(SlowAPIMiddleware)
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "chunk.js").write_text("export {}", encoding="utf-8")
            app.mount("/assets", StaticFiles(directory=tmp), name="assets")

            @app.get("/{full_path:path}")
            @tight.exempt
            async def spa(full_path: str):
                return {"path": full_path}

            client = TestClient(app)
            statuses = {client.get("/assets/chunk.js").status_code for _ in range(12)}
        self.assertEqual(statuses, {200})


if __name__ == "__main__":
    unittest.main()

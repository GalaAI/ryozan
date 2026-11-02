"""
A route's database work commits before its response goes out.

FastAPI runs a request-scoped yield dependency's exit code after the response is
sent. For get_db that means a client could read before the write landed (a new
staff member's first request came back 401), and a failed commit still answered
success. Function scope commits first, so every get_db must use it.
"""

import os
import re
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from fastapi import Depends, FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.core import database  # noqa: E402

API_ROOT = Path(__file__).resolve().parents[1]


def _session_factory(session):
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    return MagicMock(return_value=context)


class CommitBeforeResponseTests(unittest.TestCase):
    def _client(self, session, events):
        app = FastAPI()

        @app.post("/write")
        async def write(db=Depends(database.get_db, scope="function")):
            events.append("handler")
            return {"ok": True}

        self.addCleanup(patch.stopall)
        patch.object(database, "async_session", _session_factory(session)).start()
        return TestClient(app, raise_server_exceptions=False)

    def test_a_failed_commit_is_not_reported_as_success(self):
        session = MagicMock(commit=AsyncMock(side_effect=RuntimeError("database went away")), rollback=AsyncMock(), close=AsyncMock())
        response = self._client(session, []).post("/write")
        self.assertEqual(response.status_code, 500)
        session.rollback.assert_awaited()

    def test_a_good_commit_answers_success(self):
        session = MagicMock(commit=AsyncMock(), rollback=AsyncMock(), close=AsyncMock())
        events = []
        response = self._client(session, events).post("/write")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(events, ["handler"])
        session.commit.assert_awaited_once()


class EveryRouteUsesFunctionScopeTests(unittest.TestCase):
    def test_no_request_scoped_get_db_remains(self):
        offenders = []
        for root in (API_ROOT / "app", API_ROOT / "main.py"):
            files = root.rglob("*.py") if root.is_dir() else [root]
            for path in files:
                if re.search(r"Depends\(\s*get_db\s*\)", path.read_text(encoding="utf-8")):
                    offenders.append(str(path.relative_to(API_ROOT)))
        self.assertEqual(offenders, [], 'use Depends(get_db, scope="function")')


if __name__ == "__main__":
    unittest.main()

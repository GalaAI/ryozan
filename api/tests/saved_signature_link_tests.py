"""
The saved artist signature is private in R2 and comes back as a signed viewing link.

Its lifetime must never be UPLOAD_PROOF_SECONDS, which is reserved for upload links (presign_get_url refuses it).
Both GET and PUT /users/me/signature must return a signed link, not a 500. Synthetic fixtures only.
"""

import os
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

from app.api.v1.endpoints import users  # noqa: E402
from app.services import storage  # noqa: E402

HOST = "assets.example.test"
USER_ID = uuid.UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee")
SIG_URL = f"https://{HOST}/signatures/{USER_ID}/artist-sig-{USER_ID}.png"


def _settings():
    return SimpleNamespace(
        R2_ACCOUNT_ID="account-id",
        R2_ACCESS_KEY_ID="access-key",
        R2_SECRET_ACCESS_KEY="secret-key",
        R2_BUCKET_NAME="fake-shop-bucket",
        R2_PUBLIC_DOMAIN=HOST,
    )


def _expires(url: str) -> str:
    return parse_qs(urlsplit(url).query)["X-Amz-Expires"][0]


class SavedSignatureLinkTests(unittest.IsolatedAsyncioTestCase):
    def test_lifetime_is_not_the_upload_lifetime(self):
        self.assertNotEqual(users.SIGNATURE_VIEW_SECONDS, storage.UPLOAD_PROOF_SECONDS)

    async def test_get_returns_a_signed_viewing_link(self):
        user = SimpleNamespace(id=USER_ID, role="artist", saved_signature_url=SIG_URL)
        with patch.object(storage, "get_settings", return_value=_settings()):
            result = await users.get_saved_signature(current_user=user)
        url = result["signature_url"]
        self.assertIn("X-Amz-Signature=", url)
        self.assertEqual(_expires(url), str(users.SIGNATURE_VIEW_SECONDS))

    async def test_put_returns_a_signed_viewing_link(self):
        user = SimpleNamespace(id=USER_ID, role="artist", saved_signature_url=None)
        db = SimpleNamespace(commit=AsyncMock(), refresh=AsyncMock())
        with (
            patch.object(storage, "get_settings", return_value=_settings()),
            patch.object(storage, "upload_file", AsyncMock(return_value=SIG_URL)),
        ):
            result = await users.save_signature(
                body={"signature_data": "data:image/png;base64,iVBORw0KGgo="}, db=db, current_user=user
            )
        url = result["signature_url"]
        self.assertIn("X-Amz-Signature=", url)
        self.assertEqual(_expires(url), str(users.SIGNATURE_VIEW_SECONDS))


if __name__ == "__main__":
    unittest.main()

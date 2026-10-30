"""Regression tests for the local operator account refresh path.

No existing test owns ``bootstrap_dev_login.py`` or ``sync_test_data.py``. This
focused module proves the two existing scripts preserve one coherent operator
identity instead of adding a parallel account or fixture system.
"""

import argparse
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.models.user import BTTCOUser
from scripts import bootstrap_dev_login, sync_test_data


class _Result:
    def __init__(self, scalar=None):
        self.scalar = scalar

    def scalar_one_or_none(self):
        return self.scalar

    def scalars(self):
        return self

    def first(self):
        return self.scalar


class _Session:
    def __init__(self, *results):
        self.results = list(results)
        self.flush = AsyncMock()
        self.commit = AsyncMock()

    async def execute(self, _statement):
        if not self.results:
            raise AssertionError("unexpected database query")
        return self.results.pop(0)


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_exc):
        return False


def _user(*, user_id=None, email, name, role, slug):
    return BTTCOUser(
        id=user_id or uuid.uuid4(),
        email=email,
        name=name,
        password_hash="test-hash",
        role=role,
        slug=slug,
    )


class MirroredOperatorIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_slug_match_preserves_real_local_identity_fields(self):
        local_id = uuid.uuid4()
        source_id = uuid.uuid4()
        justin = _user(
            user_id=local_id,
            email="founder@ryozan.app",
            name="Justin Walker",
            role="founder",
            slug="justin",
        )
        session = _Session(_Result(), _Result(), _Result(justin))
        id_map = {}

        await sync_test_data._upsert_user(
            session,
            {
                "id": str(source_id),
                "email": "client60105@example.test",
                "name": "Morgan Z.",
                "role": "founder",
                "slug": "justin",
                "membership_tier": "premium",
            },
            id_map,
        )

        self.assertEqual(id_map[source_id], local_id)
        self.assertEqual(justin.email, "founder@ryozan.app")
        self.assertEqual(justin.name, "Justin Walker")
        self.assertEqual(justin.role, "founder")
        self.assertEqual(justin.slug, "justin")
        self.assertEqual(justin.membership_tier, "premium")

    async def test_bootstrap_repairs_an_existing_founder_name_and_role(self):
        existing = _user(
            email="founder@ryozan.app",
            name="Morgan Z.",
            role="collector",
            slug="justin",
        )
        session = _Session(_Result(existing))
        args = argparse.Namespace(
            allow_nonlocal_db=False,
            email="founder@ryozan.app",
            password="local-only-password",
            name="Justin Walker",
            role="founder",
            slug="justin",
            with_admin=False,
        )

        with (
            patch.object(
                bootstrap_dev_login,
                "get_settings",
                return_value=SimpleNamespace(DATABASE_URL="postgresql+asyncpg://u:p@localhost/bttco"),
            ),
            patch.object(bootstrap_dev_login, "async_session", return_value=_SessionContext(session)),
        ):
            result = await bootstrap_dev_login.bootstrap_dev_login(args)

        self.assertEqual(result, 0)
        self.assertEqual(existing.name, "Justin Walker")
        self.assertEqual(existing.role, "founder")
        self.assertFalse(existing.must_reset_password)
        session.commit.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

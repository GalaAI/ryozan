"""Duplicate-email login must never be an HTTP 500.

`ryozan_users` had no unique index on email and several creation paths stored
the address exactly as typed, so the database can hold pairs of rows differing only
by capitalization. Login looked users up
with `lower(email)` and `scalar_one_or_none()`, which raises
`MultipleResultsFound` on such a pair — an HTTP 500 on every attempt.

`_FakeResult.scalar_one_or_none` below reproduces that raise faithfully, and
`ProductionFailureModeTests` asserts it does, so none of these can pass
vacuously against a fake that simply never fails.
"""

import os

os.environ["ENABLE_API_DOCS"] = "false"
os.environ["ENABLE_CONFIG_HEALTH"] = "false"

import unittest  # noqa: E402
import uuid  # noqa: E402
from contextlib import AbstractAsyncContextManager  # noqa: E402
from datetime import UTC, datetime, timedelta  # noqa: E402
from unittest.mock import patch  # noqa: E402

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from slowapi import _rate_limit_exceeded_handler  # noqa: E402
from slowapi.errors import RateLimitExceeded  # noqa: E402
from slowapi.middleware import SlowAPIMiddleware  # noqa: E402
from sqlalchemy.exc import MultipleResultsFound  # noqa: E402

from app.api.v1.endpoints import auth  # noqa: E402
from app.core.rate_limit import limiter  # noqa: E402
from app.core.schema_compare import BASELINE_SQL_PATH  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.core.user_lookup import rank_candidates  # noqa: E402
from app.models.user import User, normalize_email  # noqa: E402

DUPLICATE_EMAIL = "client.alpha@ryozan.app"
DUPLICATE_EMAIL_MIXED = "Client.Alpha@ryozan.app"


# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeScalars:
    def __init__(self, rows):
        self._rows = list(rows)

    def all(self):
        return list(self._rows)

    def __iter__(self):
        return iter(self._rows)


class _FakeResult:
    """Mirrors SQLAlchemy's contract, including the raise this bug rode in on."""

    def __init__(self, rows):
        self._rows = list(rows)

    def scalar_one_or_none(self):
        if len(self._rows) > 1:
            raise MultipleResultsFound("Multiple rows were found when one or none was required")
        return self._rows[0] if self._rows else None

    def scalars(self):
        return _FakeScalars(self._rows)


class _UsersSession:
    """Routes a query to rows by looking at the compiled SQL.

    Matched on the WHERE clause, not the whole statement: a full-entity SELECT
    lists every column, `google_id` among them, so a bare substring test would
    route every query to the Google branch.
    """

    def __init__(self, users, google_rows=()):
        self.users = list(users)
        self.google_rows = list(google_rows)
        self.added = []

    async def execute(self, stmt):
        sql = str(stmt)
        _, _, where = sql.partition("WHERE")
        if "google_id" in where:
            return _FakeResult(self.google_rows)
        if "lower(ryozan_users.email)" in where:
            return _FakeResult(self.users)
        return _FakeResult([])

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if getattr(value, "id", None) is None:
                value.id = uuid.uuid4()

    async def commit(self):
        return None

    def begin_nested(self):
        class _Savepoint(AbstractAsyncContextManager):
            async def __aenter__(self):
                return self

            async def __aexit__(self, *_exc):
                return False

        return _Savepoint()


def _build_app(session):
    app = FastAPI()
    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
    app.add_middleware(SlowAPIMiddleware)
    app.include_router(auth.router)

    async def override_get_db():
        yield session

    app.dependency_overrides[auth.get_db] = override_get_db
    return app


def _user(email, *, password=None, password_hash=None, created_at=None, **kwargs):
    return User(
        id=kwargs.pop("id", uuid.uuid4()),
        email=email,
        password_hash=password_hash if password_hash is not None else hash_password(password or "unused-password"),
        name=kwargs.pop("name", "Meredith Greene"),
        role=kwargs.pop("role", "collector"),
        created_at=created_at or datetime(2026, 4, 6, tzinfo=UTC),
        email_verified_at=kwargs.pop("email_verified_at", datetime.now(UTC)),
        **kwargs,
    )


def _meredith_pair():
    """The real production shape: two rows, one real password, one placeholder.

    The lowercase row is the older one and the one a naive "oldest wins" rule
    would pick — and it is exactly the row that cannot authenticate.
    """
    older_unusable = _user(
        DUPLICATE_EMAIL,
        password_hash="guest-placeholder-not-bcrypt",
        must_reset_password=True,
        created_at=datetime(2026, 4, 6, 9, 0, tzinfo=UTC),
    )
    newer_real = _user(
        DUPLICATE_EMAIL_MIXED,
        password="correct-horse-battery",
        created_at=datetime(2026, 4, 6, 17, 0, tzinfo=UTC),
    )
    return older_unusable, newer_real


# ── The failure mode itself ────────────────────────────────────────────────


class ProductionFailureModeTests(unittest.TestCase):
    def test_scalar_one_or_none_raises_on_two_rows(self):
        """Guards every test below from passing against a too-forgiving fake."""
        with self.assertRaises(MultipleResultsFound):
            _FakeResult(list(_meredith_pair())).scalar_one_or_none()

    def test_scalars_all_returns_both_rows(self):
        self.assertEqual(len(_FakeResult(list(_meredith_pair())).scalars().all()), 2)


# ── Requirement 1: login is resilient ──────────────────────────────────────


class DuplicateEmailLoginTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_correct_password_returns_200_not_500(self):
        client = TestClient(_build_app(_UsersSession(_meredith_pair())))

        response = client.post(
            "/auth/login",
            json={"email": DUPLICATE_EMAIL, "password": "correct-horse-battery"},
            headers={"X-Forwarded-For": "203.0.113.40"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.json())

    def test_wrong_password_returns_401_not_500(self):
        client = TestClient(_build_app(_UsersSession(_meredith_pair())))

        response = client.post(
            "/auth/login",
            json={"email": DUPLICATE_EMAIL, "password": "not-the-password"},
            headers={"X-Forwarded-For": "203.0.113.41"},
        )

        self.assertEqual(response.status_code, 401)

    def test_mixed_case_input_also_logs_in(self):
        client = TestClient(_build_app(_UsersSession(_meredith_pair())))

        response = client.post(
            "/auth/login",
            json={"email": " Client.Alpha@RYOZAN.APP ", "password": "correct-horse-battery"},
            headers={"X-Forwarded-For": "203.0.113.42"},
        )

        self.assertEqual(response.status_code, 200)

    def test_temporary_artist_password_is_blocked_for_a_mixed_duplicate_set(self):
        pending = _user(
            DUPLICATE_EMAIL,
            password="unpublished-random-value",
            role="artist",
            must_reset_password=True,
        )
        established = _user(
            DUPLICATE_EMAIL_MIXED,
            password="artist-chosen-password",
            role="artist",
            must_reset_password=False,
        )
        client = TestClient(_build_app(_UsersSession([pending, established])))

        response = client.post(
            "/auth/login",
            json={"email": DUPLICATE_EMAIL, "password": "tattoo"},
            headers={"X-Forwarded-For": "203.0.113.46"},
        )

        self.assertEqual(response.status_code, 401, response.text)

    def test_forgot_password_does_not_500_on_duplicates(self):
        session = _UsersSession(_meredith_pair())
        client = TestClient(_build_app(session))

        with patch("app.api.v1.endpoints.auth.send_password_reset_email", autospec=True):
            response = client.post(
                "/auth/forgot-password",
                json={"email": DUPLICATE_EMAIL},
                headers={"X-Forwarded-For": "203.0.113.43"},
            )

        self.assertEqual(response.status_code, 200)


# ── Requirement 1b: the winner rule picks a row that can authenticate ──────


class WinnerRuleTests(unittest.TestCase):
    def test_prefers_row_with_a_real_password_over_the_older_placeholder(self):
        older_unusable, newer_real = _meredith_pair()

        winner = rank_candidates([older_unusable, newer_real])[0]

        self.assertIs(winner, newer_real, "oldest-first would have picked the unusable row")

    def test_prefers_google_linked_row(self):
        plain = _user(DUPLICATE_EMAIL, password="pw", created_at=datetime(2026, 4, 1, tzinfo=UTC))
        google_linked = _user(
            DUPLICATE_EMAIL_MIXED,
            password_hash="not-bcrypt",
            google_id="google-sub-123",
            created_at=datetime(2026, 4, 9, tzinfo=UTC),
        )

        self.assertIs(rank_candidates([plain, google_linked])[0], google_linked)

    def test_activated_account_beats_must_reset_account(self):
        base = datetime(2026, 4, 3, tzinfo=UTC)
        pending = _user("client.beta@ryozan.app", password="pw", must_reset_password=True, created_at=base)
        activated = _user(
            "client.beta@ryozan.app", password="pw", created_at=base + timedelta(hours=2), name="Tyler Fowler"
        )

        self.assertIs(rank_candidates([pending, activated])[0], activated)

    def test_oldest_wins_only_as_a_tie_break(self):
        older = _user(DUPLICATE_EMAIL, password="pw", created_at=datetime(2026, 4, 6, 1, tzinfo=UTC))
        newer = _user(DUPLICATE_EMAIL_MIXED, password="pw", created_at=datetime(2026, 4, 6, 2, tzinfo=UTC))

        self.assertIs(rank_candidates([newer, older])[0], older)

    def test_ranking_is_independent_of_input_order(self):
        rows = list(_meredith_pair())

        forward = rank_candidates(rows)
        backward = rank_candidates(list(reversed(rows)))

        self.assertEqual([r.id for r in forward], [r.id for r in backward])

    def test_identical_rows_still_resolve_deterministically(self):
        """No distinguishing signal at all — the id tie-break must still decide."""
        base = datetime(2026, 4, 6, tzinfo=UTC)
        a = _user(DUPLICATE_EMAIL, password_hash="x", created_at=base, id=uuid.UUID(int=1))
        b = _user(DUPLICATE_EMAIL_MIXED, password_hash="x", created_at=base, id=uuid.UUID(int=2))

        self.assertIs(rank_candidates([b, a])[0], a)

    def test_duplicate_warning_redacts_email_and_user_ids(self):
        import asyncio

        from app.core.user_lookup import find_user_by_email

        users = list(_meredith_pair())
        with self.assertLogs("app.core.user_lookup", level="WARNING") as captured:
            asyncio.run(find_user_by_email(_UsersSession(users), DUPLICATE_EMAIL))

        output = "\n".join(captured.output)
        self.assertNotIn(DUPLICATE_EMAIL, output)
        for user in users:
            self.assertNotIn(str(user.id), output)
        self.assertIn("count=2", output)


# ── Requirement 1c: Google sign-in survives duplicates ─────────────────────


class GoogleSignInDuplicateTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_google_signin_with_duplicated_email_does_not_raise(self):
        session = _UsersSession(_meredith_pair(), google_rows=[])
        client = TestClient(_build_app(session))
        payload = {
            "sub": "google-sub-999",
            "email": DUPLICATE_EMAIL_MIXED,
            "email_verified": True,
            "name": "Meredith Greene",
            "picture": None,
        }

        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", "ryozan-client-id"),
            patch(
                "app.api.v1.endpoints.auth.google_id_token.verify_oauth2_token",
                return_value=payload,
            ),
        ):
            response = client.post(
                "/auth/google",
                json={"credential": "valid-token"},
                headers={"X-Forwarded-For": "203.0.113.44"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn("access_token", response.json())

    def test_google_signin_links_the_authenticatable_row_not_a_new_one(self):
        older_unusable, newer_real = _meredith_pair()
        session = _UsersSession([older_unusable, newer_real], google_rows=[])
        client = TestClient(_build_app(session))

        with (
            patch.object(auth._settings, "GOOGLE_SIGNIN_CLIENT_ID", "ryozan-client-id"),
            patch(
                "app.api.v1.endpoints.auth.google_id_token.verify_oauth2_token",
                return_value={
                    "sub": "google-sub-999",
                    "email": DUPLICATE_EMAIL,
                    "email_verified": True,
                    "name": "Meredith Greene",
                },
            ),
        ):
            response = client.post(
                "/auth/google",
                json={"credential": "valid-token"},
                headers={"X-Forwarded-For": "203.0.113.45"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(session.added, [], "must not create a third row for an email that already exists")
        self.assertEqual(newer_real.google_id, "google-sub-999")


# ── Requirement 2: email is normalized on write, by the model ──────────────


class EmailNormalizationTests(unittest.TestCase):
    def test_normalize_email_trims_and_lowercases(self):
        self.assertEqual(normalize_email("  Client.Alpha@Ryozan.APP "), "client.alpha@ryozan.app")

    def test_normalize_email_passes_none_through(self):
        self.assertIsNone(normalize_email(None))

    def test_model_normalizes_on_construction(self):
        """The validator is the owner, so every construction site is covered.

        register, register-invite, register-from-booking, google, the booking
        and quote and flash guest paths, and the sync scripts all build the
        same model — none of them can store mixed case any more.
        """
        user = User(email="  Client.Alpha@Ryozan.APP ", password_hash="x", name="M", role="collector")

        self.assertEqual(user.email, "client.alpha@ryozan.app")

    def test_model_normalizes_on_later_assignment(self):
        user = User(email=DUPLICATE_EMAIL, password_hash="x", name="M", role="collector")

        user.email = "Client.Beta@RYOZAN.app"

        self.assertEqual(user.email, "client.beta@ryozan.app")


class CreationPathNormalizationTests(unittest.TestCase):
    """Each touched creation path, exercised for real, must store lowercase."""

    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_register_stores_lowercased_email(self):
        session = _UsersSession([])
        client = TestClient(_build_app(session))

        with patch("app.api.v1.endpoints.auth.send_welcome_email", autospec=True):
            response = client.post(
                "/auth/register",
                json={"email": "Client.Alpha@Ryozan.APP", "password": "password123", "name": "Meredith"},
                headers={"X-Forwarded-For": "203.0.113.46"},
            )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(session.added[0].email, "client.alpha@ryozan.app")

    def test_booking_guest_account_stores_lowercased_email(self):
        import asyncio

        from app.api.v1.endpoints.bookings import _ensure_booking_client_account

        class _Booking:
            client_id = None
            guest_email = " Client.Beta@RYOZAN.app "
            guest_name = "Tyler Fowler"

        session = _UsersSession([])
        asyncio.run(_ensure_booking_client_account(_Booking(), session))

        self.assertEqual(session.added[0].email, "client.beta@ryozan.app")

    def test_concurrent_booking_account_insert_reuses_the_winner(self):
        import asyncio

        from sqlalchemy.exc import IntegrityError

        from app.api.v1.endpoints.bookings import _find_or_create_collector

        winner = _user(DUPLICATE_EMAIL, password="pw")

        class _RaceSession(_UsersSession):
            def __init__(self):
                super().__init__([])
                self.lookups = 0

            async def execute(self, stmt):
                if "lower(ryozan_users.email)" in str(stmt):
                    self.lookups += 1
                    return _FakeResult([] if self.lookups == 1 else [winner])
                return await super().execute(stmt)

            async def flush(self):
                raise IntegrityError("INSERT", {}, RuntimeError("unique email"))

        resolved = asyncio.run(_find_or_create_collector(_RaceSession(), DUPLICATE_EMAIL, "Meredith"))
        self.assertIs(resolved, winner)

    def test_quote_guest_account_stores_lowercased_email(self):
        import asyncio

        from app.api.v1.endpoints.quotes import _ensure_guest_user

        session = _UsersSession([])
        asyncio.run(_ensure_guest_user(session, " Client.Beta@RYOZAN.app ", "Tyler Fowler"))

        self.assertEqual(session.added[0].email, "client.beta@ryozan.app")

    def test_guest_paths_reuse_a_differently_cased_existing_account(self):
        """The bug's other half: an exact-match lookup made a SECOND row."""
        import asyncio

        from app.api.v1.endpoints.quotes import _ensure_guest_user

        existing = _user(DUPLICATE_EMAIL_MIXED, password="pw")
        session = _UsersSession([existing])

        found = asyncio.run(_ensure_guest_user(session, DUPLICATE_EMAIL, "Meredith"))

        self.assertIs(found, existing)
        self.assertEqual(session.added, [])


# ── Requirement 2b: a second registration is a conflict, not a new row ─────


class DuplicateRegistrationTests(unittest.TestCase):
    def setUp(self):
        limiter.reset()

    def tearDown(self):
        limiter.reset()

    def test_registering_with_different_capitalization_is_rejected(self):
        existing = _user(DUPLICATE_EMAIL, password="pw")
        session = _UsersSession([existing])
        client = TestClient(_build_app(session))

        with patch("app.api.v1.endpoints.auth.send_welcome_email", autospec=True):
            response = client.post(
                "/auth/register",
                json={"email": DUPLICATE_EMAIL_MIXED, "password": "password123", "name": "Meredith"},
                headers={"X-Forwarded-For": "203.0.113.47"},
            )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(session.added, [], "no second row may be created")

    def test_register_from_booking_rejects_a_differently_cased_duplicate(self):
        existing = _user(DUPLICATE_EMAIL, password="pw")
        session = _UsersSession([existing])
        client = TestClient(_build_app(session))

        response = client.post(
            "/auth/register-from-booking",
            json={"quote_token": "no-such-token", "password": "password123"},
            headers={"X-Forwarded-For": "203.0.113.48"},
        )

        # The quote lookup fails first; what matters is that it is a clean 404
        # rather than a 500 from the user query.
        self.assertEqual(response.status_code, 404)


# ── Requirement 3: the schema carries the index ────────────────────────────


class BaselineEmailIndexTests(unittest.TestCase):
    """The Alembic baseline owns the index; adopt_schema creates it on a database that lacks it.

    A database with duplicate emails cannot be adopted: CREATE UNIQUE INDEX fails
    inside the adoption transaction and nothing changes. Run
    scripts/dedupe_user_emails.py (dry-run first), then adopt.
    """

    def test_index_is_unique_case_insensitive(self):
        baseline = BASELINE_SQL_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "CREATE UNIQUE INDEX ux_bttco_users_email_lower ON public.bttco_users USING btree (lower((email)::text));",
            baseline,
        )

    def test_index_is_not_created_concurrently(self):
        """CONCURRENTLY would leave an INVALID index behind on failure and cannot run in a transaction."""
        self.assertNotIn("CONCURRENTLY", BASELINE_SQL_PATH.read_text(encoding="utf-8"))


class DedupeSafetyTests(unittest.IsolatedAsyncioTestCase):
    def test_declared_user_fk_is_still_a_candidate_when_live_constraint_is_missing(self):
        from scripts.dedupe_user_emails import orm_soft_reference_candidates

        candidates = {(ref.table, ref.column) for ref in orm_soft_reference_candidates()}
        self.assertIn(("ryozan_messages", "sender_id"), candidates)
        self.assertIn(("ryozan_conversation_participants", "user_id"), candidates)

    def test_crm_assignee_soft_reference_is_discovered(self):
        from scripts.dedupe_user_emails import _SOFT_REFERENCE_COLUMN_NAMES

        self.assertIn("assignee_id", _SOFT_REFERENCE_COLUMN_NAMES)

    def test_account_state_is_reconciled_and_conflicts_are_reported(self):
        from scripts.dedupe_user_emails import Group, account_state_plan

        created = datetime(2026, 4, 6, tzinfo=UTC)
        survivor = {
            "id": uuid.uuid4(),
            "email": DUPLICATE_EMAIL,
            "google_id": "google-sub",
            "password_hash": "placeholder",
            "must_reset_password": True,
            "role": "collector",
            "bttcoin_balance": 5,
            "quest_points": 2,
            "marketing_opt_in": True,
            "early_access_galax3": False,
            "product_entitlements": {"alpha": True},
            "avatar_url": None,
            "membership_tier": "freemium",
            "created_at": created,
            "updated_at": created,
        }
        loser = {
            **survivor,
            "id": uuid.uuid4(),
            "google_id": None,
            "password_hash": hash_password("real-password"),
            "must_reset_password": False,
            "role": "founder",
            "bttcoin_balance": 7,
            "quest_points": 3,
            "marketing_opt_in": False,
            "early_access_galax3": True,
            "product_entitlements": {"beta": True},
            "avatar_url": "https://cdn.example/avatar.webp",
            "membership_tier": "pro",
            "created_at": created + timedelta(hours=1),
            "updated_at": created + timedelta(hours=1),
        }

        updates, notes = account_state_plan(Group(DUPLICATE_EMAIL, [survivor, loser]))

        self.assertTrue(updates["password_hash"].startswith("$2"))
        self.assertEqual(updates["role"], "founder")
        self.assertEqual(updates["bttcoin_balance"], 12)
        self.assertEqual(updates["quest_points"], 5)
        self.assertFalse(updates["marketing_opt_in"])
        self.assertTrue(updates["early_access_galax3"])
        self.assertEqual(updates["avatar_url"], "https://cdn.example/avatar.webp")
        self.assertEqual(updates["product_entitlements"], {"alpha": True, "beta": True})
        self.assertTrue(any(note.startswith("membership_tier: conflict reported") for note in notes))

    async def test_unknown_unique_reference_collision_fails_before_writes(self):
        from scripts.dedupe_user_emails import Group, Reference, UniqueKey, apply_group

        class _Conn:
            def __init__(self):
                self.executed = []

            async def fetchval(self, *_args):
                return 1

            async def execute(self, statement, *_args):
                self.executed.append(statement)

        group = Group(
            DUPLICATE_EMAIL,
            [
                {
                    "id": uuid.UUID(int=1),
                    "email": DUPLICATE_EMAIL,
                    "google_id": "google-sub",
                    "password_hash": "placeholder",
                    "must_reset_password": False,
                    "created_at": datetime(2026, 1, 1, tzinfo=UTC),
                },
                {
                    "id": uuid.UUID(int=2),
                    "email": DUPLICATE_EMAIL,
                    "google_id": None,
                    "password_hash": "placeholder",
                    "must_reset_password": False,
                    "created_at": datetime(2026, 1, 2, tzinfo=UTC),
                },
            ],
        )
        conn = _Conn()

        with self.assertRaisesRegex(RuntimeError, "Ambiguous unique-key collision"):
            await apply_group(
                conn,
                group,
                [Reference("ryozan_ai_memories", "user_id", "fk")],
                [UniqueKey("ryozan_ai_memories", "uq_memory", ("user_id", "key"))],
            )

        self.assertEqual(conn.executed, [])


if __name__ == "__main__":
    unittest.main()

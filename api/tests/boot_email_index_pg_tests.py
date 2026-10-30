"""The boot path must create the unique email index, and survive not being able to.

Production has never run Alembic and has no `alembic_version` table, so
main.py's `_run_startup_migrations()` is the only path that reaches it. That
function wraps every raw statement in ONE try/except that prints
"(non-fatal)" and RETURNS — an exception anywhere in the block silently skips
every statement after it. The unique index is EXPECTED to fail while the
Meredith and Tyler duplicates still exist, so it must be unable to take the
rest of the boot down with it.

These run the real function in a subprocess against a real Postgres, exactly
as the CI "Schema boot" job does. Subprocess rather than in-process because
`get_settings()` is `lru_cache`d: a sibling test file that already imported it
would otherwise pin the wrong DATABASE_URL.

Set BTTCO_TEST_DATABASE_URL to point these at a database. Skipped when no
database is reachable, so the CI unit job (which has no Postgres service)
stays green.
"""

import os
import subprocess
import sys
import unittest
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]

# config.py derives DATABASE_SYNC_URL from DATABASE_URL, but both are set
# explicitly so a partial override can never silently test a different database.
TEST_DATABASE_URL = os.environ.get(
    "BTTCO_TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5439/nexus",
)

INDEX_NAME = "ux_bttco_users_email_lower"


def _child_env() -> dict:
    env = dict(os.environ)
    env["DATABASE_URL"] = TEST_DATABASE_URL
    env["DATABASE_SYNC_URL"] = TEST_DATABASE_URL
    env["ENABLE_API_DOCS"] = "false"
    env["ENABLE_CONFIG_HEALTH"] = "false"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def _run_python(script: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(API_ROOT),
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=600,
    )


def _database_available() -> bool:
    probe = _run_python(
        "import asyncio, asyncpg, os\n"
        "async def m():\n"
        "    c = await asyncpg.connect(os.environ['DATABASE_SYNC_URL'])\n"
        "    await c.close()\n"
        "asyncio.run(m())\n"
    )
    return probe.returncode == 0


DATABASE_AVAILABLE = _database_available()

_BOOT = "import asyncio\nfrom main import _run_startup_migrations\nasyncio.run(_run_startup_migrations())\n"

_SQL_TEMPLATE = (
    "import asyncio, asyncpg, os, json\n"
    "async def m():\n"
    "    c = await asyncpg.connect(os.environ['DATABASE_SYNC_URL'])\n"
    "    try:\n"
    "        rows = await c.fetch({query!r})\n"
    "        print('RESULT ' + json.dumps([[str(v) for v in r.values()] for r in rows]))\n"
    "    finally:\n"
    "        await c.close()\n"
    "asyncio.run(m())\n"
)

_EXEC_TEMPLATE = (
    "import asyncio, asyncpg, os\n"
    "async def m():\n"
    "    c = await asyncpg.connect(os.environ['DATABASE_SYNC_URL'])\n"
    "    try:\n"
    "        for s in {statements!r}:\n"
    "            await c.execute(s)\n"
    "    finally:\n"
    "        await c.close()\n"
    "asyncio.run(m())\n"
)


# Inserts a row the way the legacy data got in: raw SQL, email verbatim, so
# the ORM's normalizing validator cannot interfere. Column values are derived
# from information_schema rather than hardcoded, so this keeps working as
# bttco_users grows NOT NULL columns. The address arrives via argv, so nothing
# has to be escaped into the script text.
_INSERT_LEGACY_USER = """
import asyncio, asyncpg, os, sys

EMAIL = sys.argv[1]
FILLERS = {
    "integer": "0",
    "boolean": "false",
    "jsonb": "'{}'::jsonb",
    "timestamp with time zone": "now()",
    "uuid": "gen_random_uuid()",
    "character varying": "'x'",
    "text": "'x'",
}

async def main():
    conn = await asyncpg.connect(os.environ["DATABASE_SYNC_URL"])
    try:
        rows = await conn.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'bttco_users' AND is_nullable = 'NO' "
            "AND column_default IS NULL"
        )
        cols, vals = [], []
        for row in rows:
            cols.append('"' + row["column_name"] + '"')
            vals.append("$1" if row["column_name"] == "email" else FILLERS[row["data_type"]])
        sql = "INSERT INTO bttco_users (" + ",".join(cols) + ") VALUES (" + ",".join(vals) + ")"
        await conn.execute(sql, EMAIL)
    finally:
        await conn.close()

asyncio.run(main())
"""


def _insert_legacy_user(email: str) -> None:
    proc = subprocess.run(
        [sys.executable, "-c", _INSERT_LEGACY_USER, email],
        cwd=str(API_ROOT),
        env=_child_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    if proc.returncode != 0:
        raise AssertionError(f"insert failed: {proc.stderr}")


def _query(sql: str) -> list[list[str]]:
    import json

    proc = _run_python(_SQL_TEMPLATE.format(query=sql))
    if proc.returncode != 0:
        raise AssertionError(f"query failed: {proc.stderr}")
    line = next(ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT "))
    return json.loads(line[len("RESULT ") :])


def _execute(*statements: str) -> None:
    proc = _run_python(_EXEC_TEMPLATE.format(statements=list(statements)))
    if proc.returncode != 0:
        raise AssertionError(f"execute failed: {proc.stderr}")


def _index_exists() -> bool:
    rows = _query(f"SELECT 1 FROM pg_indexes WHERE indexname = '{INDEX_NAME}'")
    return bool(rows)


@unittest.skipUnless(DATABASE_AVAILABLE, f"no Postgres reachable at {TEST_DATABASE_URL}")
class BootEmailIndexTests(unittest.TestCase):
    """Ordered: each stage sets up the next. Hence the numeric prefixes."""

    def test_08_artist_merge_preserves_identities_history_and_reset_revocation(self):
        proc = _run_python("""
import asyncio, argparse, uuid
from datetime import UTC, datetime
from unittest.mock import patch
from fastapi import FastAPI
from httpx import AsyncClient, ASGITransport
from jose import jwt
from app.api.v1.endpoints import auth
from sqlalchemy import select, delete, text
from app.core.database import async_session, engine
from app.core.security import hash_password, create_access_token, password_hash_fingerprint
from app.core.user_lookup import authenticate_by_email, find_user_by_email, user_google_matches
from app.models.user import BTTCOUser, UserIdentityAlias
from app.models.message import Conversation, ConversationParticipant, Message
from scripts.dedupe_user_emails import run

async def main():
    ids=[uuid.uuid4() for _ in range(3)]
    emails=['artist-typo@merge.example.test','artist-main@merge.example.test','artist-other@merge.example.test']
    conversation_id=uuid.uuid4()
    async with async_session() as db:
        for i in range(3):
            db.add(BTTCOUser(id=ids[i],email=emails[i],name='Merge Fixture',role='artist' if i==0 else 'collector',
                slug='merge-fixture' if i==0 else None, booking_mode='venue.ink' if i==0 else None,
                booking_url='https://venue.ink/merge-fixture' if i==0 else None,
                google_id=f'merge-fixture-google-{i}' if i else None,email_verified_at=datetime.now(UTC) if i!=2 else None,password_hash=hash_password(f'password-{i}')))
        await db.flush()
        db.add(Conversation(id=conversation_id,created_by=ids[2]))
        await db.flush()
        for i in range(3):
            db.add(ConversationParticipant(conversation_id=conversation_id,user_id=ids[i],role='member'))
        db.add(Message(conversation_id=conversation_id,sender_id=ids[2],content='Preserved fixture message'))
        await db.commit()
    args=argparse.Namespace(artist_id=str(ids[0]),merge_user_id=[str(x) for x in ids[1:]],
        canonical_email=emails[1],email=None,apply=False)
    try:
        assert await run(args)==0
        async with async_session() as db:
            assert len((await db.execute(select(BTTCOUser).where(BTTCOUser.id.in_(ids)))).scalars().all())==3
        args.apply=True
        async with engine.begin() as conn:
            await conn.execute(text('CREATE TABLE bttco_merge_fixture_guard (user_id uuid REFERENCES bttco_users(id), marker text, UNIQUE(user_id,marker))'))
            for value in ids[:2]:
                await conn.execute(text("INSERT INTO bttco_merge_fixture_guard VALUES (:id,'collision')"),{'id':value})
        try:
            await run(args)
            raise AssertionError('Ambiguous collision was not refused')
        except RuntimeError as exc:
            assert 'Ambiguous unique-key collision' in str(exc)
        async with async_session() as db:
            assert len((await db.execute(select(BTTCOUser).where(BTTCOUser.id.in_(ids)))).scalars().all())==3
            assert not (await db.execute(select(UserIdentityAlias).where(UserIdentityAlias.user_id==ids[0]))).scalars().all()
        async with engine.begin() as conn:
            await conn.execute(text('DROP TABLE bttco_merge_fixture_guard'))
        assert await run(args)==0
        assert await run(args)==0
        async with async_session() as db:
            users=(await db.execute(select(BTTCOUser).where(BTTCOUser.id.in_(ids)))).scalars().all()
            assert len(users)==1
            artist=users[0]
            assert artist.id==ids[0] and artist.role=='artist' and artist.slug=='merge-fixture'
            assert artist.email==emails[1] and artist.booking_mode=='venue.ink'
            for i in (1,2):
                assert (await find_user_by_email(db,emails[i])).id==ids[0]
                assert (await db.execute(select(BTTCOUser).where(user_google_matches(f'merge-fixture-google-{i}')))).scalar_one().id==ids[0]
                if i==1:
                    assert (await authenticate_by_email(db,emails[i],f'password-{i}')).id==ids[0]
                else:
                    assert await authenticate_by_email(db,emails[i],f'password-{i}') is None
            aliases=(await db.execute(select(UserIdentityAlias).where(UserIdentityAlias.user_id==ids[0]))).scalars().all()
            assert len(aliases)==2 and all(alias.source_snapshot and alias.reference_snapshot for alias in aliases)
            app=FastAPI()
            app.include_router(auth.router)
            async def session_override():
                yield db
            app.dependency_overrides[auth.get_db]=session_override
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://fixture') as client:
                for i in (1,2):
                    with patch.object(auth._settings,'GOOGLE_SIGNIN_CLIENT_ID','fixture-audience'), patch.object(
                        auth.google_id_token,'verify_oauth2_token',return_value={
                            'sub':f'merge-fixture-google-{i}','email':emails[i],'email_verified':True,'name':'Merge Fixture'}):
                        response=await client.post('/auth/google',json={'credential':'verified-provider-fixture'})
                    assert response.status_code==200, response.text
                    claims=jwt.decode(response.json()['access_token'],auth._settings.SECRET_KEY,algorithms=[auth._settings.ALGORITHM])
                    assert claims['sub']==str(ids[0]) and claims['role']=='artist'
                    me=await client.get('/auth/me',headers={'Authorization':'Bearer '+response.json()['access_token']})
                    assert me.status_code==200 and me.json()['id']==str(ids[0]) and me.json()['role']=='artist'
                old=create_access_token({'sub':str(ids[2]),'typ':'access','pwf':password_hash_fingerprint(aliases[1].source_snapshot['merged']['password_hash'])})
                assert (await client.get('/auth/me',headers={'Authorization':'Bearer '+old})).status_code==401
            assert (await db.execute(select(Message).where(Message.conversation_id==conversation_id))).scalar_one().sender_id==ids[0]
            participants=(await db.execute(select(ConversationParticipant).where(ConversationParticipant.conversation_id==conversation_id))).scalars().all()
            assert len(participants)==1 and participants[0].last_read_at is None
            artist.password_hash=hash_password('replacement-password')
            await db.commit()
            for i in (1,2):
                assert await authenticate_by_email(db,emails[i],f'password-{i}') is None
                assert (await authenticate_by_email(db,emails[i],'replacement-password')).id==ids[0]
        print('ARTIST_MERGE_PASS')
    finally:
        async with engine.begin() as conn:
            await conn.execute(text('DROP TABLE IF EXISTS bttco_merge_fixture_guard'))
        async with async_session() as db:
            await db.execute(delete(Message).where(Message.conversation_id==conversation_id))
            await db.execute(delete(ConversationParticipant).where(ConversationParticipant.conversation_id==conversation_id))
            await db.execute(delete(Conversation).where(Conversation.id==conversation_id))
            await db.execute(delete(UserIdentityAlias).where(UserIdentityAlias.user_id.in_(ids)))
            await db.execute(delete(BTTCOUser).where(BTTCOUser.id.in_(ids)))
            await db.commit()
asyncio.run(main())
""")
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("ARTIST_MERGE_PASS", proc.stdout)

    def test_01_boot_creates_the_unique_index(self):
        proc = _run_python(_BOOT)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Startup migrations: OK", proc.stdout)
        self.assertIn("Unique index on lower(email): OK", proc.stdout)
        self.assertTrue(_index_exists())

    def test_02_boot_is_idempotent_across_two_runs(self):
        first = _run_python(_BOOT)
        second = _run_python(_BOOT)

        for label, proc in (("first", first), ("second", second)):
            self.assertEqual(proc.returncode, 0, f"{label}: {proc.stderr}")
            self.assertIn("Startup migrations: OK", proc.stdout, label)
            self.assertIn("Unique index on lower(email): OK", proc.stdout, label)
            self.assertNotIn("Startup migration error", proc.stdout, label)
        self.assertTrue(_index_exists())

    def test_03_index_rejects_a_second_row_differing_only_by_case(self):
        _execute("DELETE FROM bttco_users WHERE email ILIKE 'casetest@example.com'")
        _insert_legacy_user("casetest@example.com")

        with self.assertRaises(AssertionError) as caught:
            _insert_legacy_user("CaseTest@Example.com")

        self.assertIn("UniqueViolation", str(caught.exception))
        _execute("DELETE FROM bttco_users WHERE email ILIKE 'casetest@example.com'")

    def test_04_boot_completes_when_duplicates_block_the_index(self):
        """The whole point: a failed index must not skip the rest of the block."""
        _execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
        _execute("DELETE FROM bttco_users WHERE email ILIKE 'duptest@example.com'")
        _insert_legacy_user("duptest@example.com")
        _insert_legacy_user("DupTest@Example.com")

        try:
            proc = _run_python(_BOOT)

            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("Unique index on lower(email) NOT created", proc.stdout)
            self.assertIn("dedupe_user_emails.py", proc.stdout)
            # The outer handler was never reached, so nothing after the index
            # attempt was skipped.
            self.assertIn("Startup migrations: OK", proc.stdout)
            self.assertNotIn("Startup migration error (non-fatal)", proc.stdout)
            self.assertFalse(_index_exists(), "index must not exist while duplicates remain")
        finally:
            _execute("DELETE FROM bttco_users WHERE email ILIKE 'duptest@example.com'")

    def test_05_index_is_created_once_the_duplicates_are_gone(self):
        proc = _run_python(_BOOT)

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("Unique index on lower(email): OK", proc.stdout)
        self.assertTrue(_index_exists())

    def test_06_index_definition_is_unique_and_lowercased(self):
        rows = _query(f"SELECT indexdef FROM pg_indexes WHERE indexname = '{INDEX_NAME}'")

        self.assertEqual(len(rows), 1)
        definition = rows[0][0]
        self.assertIn("CREATE UNIQUE INDEX", definition)
        self.assertIn("lower(", definition)

    def test_07_dedupe_merges_state_and_per_user_unique_rows(self):
        email = "dedupe-integration@example.test"
        _execute(f"DROP INDEX IF EXISTS {INDEX_NAME}")
        _execute(f"DELETE FROM bttco_users WHERE email ILIKE '{email}'")
        _insert_legacy_user(email)
        _insert_legacy_user("Dedupe-Integration@Example.Test")

        try:
            user_ids = [
                row[0] for row in _query(f"SELECT id FROM bttco_users WHERE email ILIKE '{email}' ORDER BY email")
            ]
            self.assertEqual(len(user_ids), 2)
            survivor_id, loser_id = user_ids
            quest_id = _query("SELECT id FROM bttco_quests ORDER BY created_at LIMIT 1")[0][0]

            _execute(
                f"UPDATE bttco_users SET google_id = 'dedupe-google', bttcoin_balance = 5, quest_points = 2 "
                f"WHERE id = '{survivor_id}'",
                f"UPDATE bttco_users SET password_hash = '$2b$12$abcdefghijklmnopqrstuv12345678901234567890123456789', "
                f"bttcoin_balance = 7, quest_points = 3, early_access_galax3 = true WHERE id = '{loser_id}'",
                f"INSERT INTO bttco_notification_preferences "
                f"(id,user_id,email_bookings,email_messages,email_quests,email_marketing,push_bookings,push_messages,push_quests,updated_at) "
                f"VALUES (gen_random_uuid(),'{survivor_id}',true,true,true,true,true,true,true,now()),"
                f"(gen_random_uuid(),'{loser_id}',false,true,true,false,true,true,true,now())",
                f"INSERT INTO bttco_user_quests "
                f"(id,user_id,quest_id,progress,completed,claimed,completed_at,claimed_at,created_at) VALUES "
                f"(gen_random_uuid(),'{survivor_id}','{quest_id}',1,false,false,NULL,NULL,now()),"
                f"(gen_random_uuid(),'{loser_id}','{quest_id}',4,true,true,now(),now(),now())",
            )

            proc = subprocess.run(
                [
                    sys.executable,
                    "scripts/dedupe_user_emails.py",
                    "--email",
                    email,
                    "--apply",
                    "--i-understand-this-writes",
                ],
                cwd=str(API_ROOT),
                env=_child_env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=600,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            users = _query(
                f"SELECT bttcoin_balance, quest_points, early_access_galax3 FROM bttco_users "
                f"WHERE email ILIKE '{email}'"
            )
            self.assertEqual(users, [["12", "5", "True"]])
            preferences = _query(
                f"SELECT count(*), bool_and(email_bookings), bool_and(email_marketing) "
                f"FROM bttco_notification_preferences WHERE user_id IN ('{survivor_id}','{loser_id}')"
            )
            self.assertEqual(preferences, [["1", "False", "False"]])
            progress = _query(
                f"SELECT count(*), max(progress), bool_or(completed), bool_or(claimed) "
                f"FROM bttco_user_quests WHERE user_id IN ('{survivor_id}','{loser_id}')"
            )
            self.assertEqual(progress, [["1", "4", "True", "True"]])
        finally:
            _execute(f"DELETE FROM bttco_users WHERE email ILIKE '{email}'")
            boot = _run_python(_BOOT)
            self.assertEqual(boot.returncode, 0, boot.stdout + boot.stderr)


if __name__ == "__main__":
    unittest.main()

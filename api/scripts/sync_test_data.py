"""Sync scoped production-shaped test data into the local dev database.

This is net-new beside sync_public_state.py because the contracts are different.
sync_public_state.py copies only public artist profile fields from public API
responses. This script imports private booking, conversation, message,
notification, and referenced user rows from a gitignored .dev dump, and it
refuses to run unless DATABASE_URL points at a local database.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import bindparam, delete, func, select, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.sql.sqltypes import Boolean, Date, DateTime, Integer, Numeric

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.database import async_session
from app.core.security import hash_password
from app.models.booking import Booking
from app.models.message import Conversation, ConversationParticipant, Message
from app.models.notification import Notification
from app.models.user import BTTCOUser, normalize_email

SENSITIVE_USER_COLUMNS = {
    "password_hash",
    "google_refresh_token",
    "google_access_token",
    "stripe_account_id",
    "plaid_access_token",
    "must_reset_password",
}

USER_ID_REMAPS = (
    (Booking, ("client_id", "artist_id")),
    (Conversation, ("created_by",)),
    (ConversationParticipant, ("user_id",)),
    (Message, ("sender_id",)),
    (Notification, ("user_id",)),
)


def _is_local_database(url: str) -> bool:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def _load_dump(path: Path) -> dict:
    if not path.exists():
        raise FileNotFoundError(f"Prod test dump not found at {path}. Run dump_prod_test_data.py first.")
    return json.loads(path.read_text(encoding="utf-8"))


def _coerce_value(column, value):
    if value is None:
        return None
    column_type = column.type
    if isinstance(column_type, PG_UUID):
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    if isinstance(column_type, DateTime):
        if isinstance(value, datetime):
            return value
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if isinstance(column_type, Date):
        if isinstance(value, date):
            return value
        return date.fromisoformat(str(value))
    if isinstance(column_type, Numeric):
        return Decimal(str(value))
    if isinstance(column_type, Integer):
        return int(value)
    if isinstance(column_type, Boolean):
        return bool(value)
    if isinstance(column_type, JSONB) and isinstance(value, str):
        return json.loads(value)
    if isinstance(column_type, ARRAY) and isinstance(value, str):
        return json.loads(value)
    if isinstance(column_type, (ARRAY, JSONB)):
        return value
    return value


def _model_values(model, row: dict, *, drop_sensitive: bool = False) -> dict:
    values = {}
    for prop in model.__mapper__.column_attrs:
        column = prop.columns[0]
        row_key = column.name
        attr_key = prop.key
        if row_key not in row:
            continue
        if drop_sensitive and row_key in SENSITIVE_USER_COLUMNS:
            continue
        values[attr_key] = _coerce_value(column, row[row_key])
    return values


def _user_columns(model) -> tuple[str, ...]:
    for candidate, columns in USER_ID_REMAPS:
        if candidate is model:
            return columns
    return ()


def _foreign_keys(model) -> tuple[tuple[str, str, str], ...]:
    """(attribute, referenced table, referenced column) for every FK on model.

    Read off the mapper rather than listed by hand. USER_ID_REMAPS only ever
    described the *user* references because those are the ones needing an id
    remap, and validating just those left every other foreign key unchecked:
    bookings also point at conversations, and a scoped dump routinely contains
    a booking whose conversation fell outside the window.
    """
    found = []
    for prop in model.__mapper__.column_attrs:
        column = prop.columns[0]
        for fk in column.foreign_keys:
            found.append((prop.key, fk.column.table.name, fk.column.name))
    return tuple(found)


async def _import_rows(
    session,
    model,
    rows: list[dict],
    *,
    id_map: dict[uuid.UUID, uuid.UUID],
) -> int:
    """Upsert a model's rows, dropping any whose references are missing.

    Returns the number skipped. Validation happens for the whole batch before
    anything is added, because a mid-batch SELECT would autoflush the rows
    already queued and raise from there instead, which is what made the old
    failures so opaque.
    """
    prepared = []
    for row in rows:
        values = _model_values(model, row)
        if not values:
            continue
        for column_name in _user_columns(model):
            current = values.get(column_name)
            if current is not None:
                values[column_name] = id_map.get(current, current)
        prepared.append(values)

    foreign_keys = _foreign_keys(model)
    valid: dict[tuple[str, str], set] = {}
    if prepared and foreign_keys:
        with session.no_autoflush:
            for _attr, table_name, column_name in foreign_keys:
                key = (table_name, column_name)
                if key in valid:
                    continue
                wanted = {
                    values[attr]
                    for attr, t, c in foreign_keys
                    if (t, c) == key
                    for values in prepared
                    if values.get(attr) is not None
                }
                if not wanted:
                    valid[key] = set()
                    continue
                # Table and column names come from SQLAlchemy metadata, never
                # from the dump. The expanding bind keeps this working whatever
                # the key's type is, without hand-casting to uuid[].
                statement = text(f"SELECT {column_name} FROM {table_name} WHERE {column_name} IN :ids").bindparams(
                    bindparam("ids", expanding=True)
                )
                result = await session.execute(statement, {"ids": list(wanted)})
                valid[key] = set(result.scalars().all())

    # Rows in this batch satisfy same-table references once the batch lands.
    own_table = model.__table__.name
    for _attr, table_name, column_name in foreign_keys:
        if table_name == own_table:
            valid[(table_name, column_name)] |= {v["id"] for v in prepared if v.get("id")}

    skipped = 0
    for values in prepared:
        missing = next(
            (
                (attr, values[attr], table_name)
                for attr, table_name, column_name in foreign_keys
                if values.get(attr) is not None and values[attr] not in valid[(table_name, column_name)]
            ),
            None,
        )
        if missing:
            attr, value, table_name = missing
            print(f"  skipped {model.__name__} {values.get('id')}: {attr} {value} absent from {table_name}")
            skipped += 1
            continue

        result = await session.execute(select(model).where(model.id == values["id"]))
        existing = result.scalar_one_or_none()
        if existing:
            for key, value in values.items():
                if key != "id":
                    setattr(existing, key, value)
            continue
        session.add(model(**values))

    return skipped


async def _upsert_user(session, row: dict, id_map: dict[uuid.UUID, uuid.UUID]) -> None:
    values = _model_values(BTTCOUser, row, drop_sensitive=True)
    if not values:
        return

    prod_id = values["id"]
    email = values.get("email")
    slug = values.get("slug")
    matched_by_slug = False

    by_id = await session.execute(select(BTTCOUser).where(BTTCOUser.id == prod_id))
    user = by_id.scalar_one_or_none()

    if user is None and email:
        # Case-insensitive, and tolerant of the duplicate rows a not-yet-deduped
        # source can still carry: the model normalizes on write, so an exact
        # match against a mixed-case source address would miss the local row and
        # create a second one.
        by_email = await session.execute(select(BTTCOUser).where(func.lower(BTTCOUser.email) == normalize_email(email)))
        user = by_email.scalars().first()
        if user and user.id != prod_id:
            # This local row is the same person under a different primary key,
            # usually one bootstrap_dev_login created before the first mirror.
            #
            # Rewriting the local row's primary key to the production one is
            # what this used to do, and it cannot work against immediate
            # constraints: the dependent rows are updated to point at a user id
            # that does not exist yet, and Postgres rejects it. Reversing the
            # order just fails from the other side, because moving the key
            # orphans the rows still pointing at the old one.
            #
            # So keep the local key and translate the dump instead. Every
            # dependent row is remapped on the way in by _upsert_model.
            id_map[prod_id] = user.id

    if user is None and slug:
        # Email cannot link these rows. dump_prod_test_data.py pseudonymizes
        # addresses by default, so the founder arrives as something like
        # client60105@example.test while the local account created by
        # bootstrap_dev_login still carries the real one. The slug is the only
        # stable identity across that boundary, and it is unique, so a row
        # already holding this slug is this person.
        #
        # Without this the import tried to INSERT a second row carrying the
        # same slug and died on ryozan_users_slug_key.
        by_slug = await session.execute(select(BTTCOUser).where(BTTCOUser.slug == slug))
        user = by_slug.scalars().first()
        if user:
            matched_by_slug = True
            if user.id != prod_id:
                id_map[prod_id] = user.id

    if user:
        current_password_hash = user.password_hash
        for key, value in values.items():
            if key == "id":
                continue
            # A slug match joins a real local operator login to a pseudonymized
            # mirror row. Preserve the complete local identity contract while
            # still importing that person's non-identity product state.
            if matched_by_slug and key in {"email", "name", "role", "slug"}:
                continue
            setattr(user, key, value)
        user.password_hash = current_password_hash
        return

    if slug:
        # Last guard before insert: a local-only account may hold this slug
        # without being the same person, and slug is unique.
        clash = await session.execute(select(BTTCOUser).where(BTTCOUser.slug == slug))
        if clash.scalars().first() is not None:
            print(f"  slug '{slug}' already held locally; importing {prod_id} without one")
            values["slug"] = None

    user = BTTCOUser(
        **values,
        password_hash=hash_password("BTTCO-dev-sync-change-me"),
        must_reset_password=True,
    )
    session.add(user)


def _ids(rows: list[dict]) -> set[uuid.UUID]:
    return {uuid.UUID(str(row["id"])) for row in rows if row.get("id")}


async def _prune_synced_tables(session, dump: dict) -> None:
    """Remove local-only rows from the private sync tables.

    This is opt-in and local-DB-only. Users are intentionally not pruned because
    local login scaffolding may include dev-only accounts.
    """
    table_ids = {
        Notification: _ids(dump.get("notifications", [])),
        Message: _ids(dump.get("messages", [])),
        ConversationParticipant: _ids(dump.get("participants", [])),
        Booking: _ids(dump.get("bookings", [])),
        Conversation: _ids(dump.get("conversations", [])),
    }
    for model, keep_ids in table_ids.items():
        if keep_ids:
            await session.execute(delete(model).where(model.id.notin_(keep_ids)))
        else:
            await session.execute(delete(model))


async def sync_test_data(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not args.allow_nonlocal_db and not _is_local_database(settings.DATABASE_URL):
        print("Refusing to sync: DATABASE_URL is not local.")
        return 2

    dump_path = Path(args.dump_file).resolve()
    dump = _load_dump(dump_path)
    print(f"Loading prod-shaped test data from {dump_path}")

    async with async_session() as session:
        if args.prune:
            await _prune_synced_tables(session, dump)
            await session.flush()

        # prod user id -> local user id, for rows the local database already
        # holds under a different key. Empty on a clean mirror.
        id_map: dict[uuid.UUID, uuid.UUID] = {}
        for row in dump.get("users", []):
            await _upsert_user(session, row, id_map)
        await session.flush()

        skipped = 0
        for model, dump_key in (
            (Conversation, "conversations"),
            (ConversationParticipant, "participants"),
            (Message, "messages"),
            (Booking, "bookings"),
            (Notification, "notifications"),
        ):
            skipped += await _import_rows(session, model, dump.get(dump_key, []), id_map=id_map)
            # Flushed per model so the order above stays the dependency order:
            # users before everything, conversations before the participants,
            # messages and bookings that point at them. Each pass validates
            # against what the previous ones actually landed.
            await session.flush()

        if args.dry_run:
            await session.rollback()
            print("Dry run only. Rolled back local changes.")
            return 0

        await session.commit()

    print(
        "Sync complete: "
        f"users={len(dump.get('users', []))} bookings={len(dump.get('bookings', []))} "
        f"conversations={len(dump.get('conversations', []))} messages={len(dump.get('messages', []))} "
        f"notifications={len(dump.get('notifications', []))} "
        f"remapped_users={len(id_map)} skipped_rows={skipped} at {datetime.now(UTC).isoformat()}"
    )
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dump-file",
        default=str(Path(__file__).resolve().parents[2] / ".dev" / "state" / "prod-test-dump.json"),
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--prune", action="store_true", help="Remove local-only rows from synced private tables")
    parser.add_argument("--allow-nonlocal-db", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(sync_test_data(parse_args())))

"""Merge `ryozan_users` rows that share an email, ignoring capitalization.

WHY
---
`ryozan_users` has never had a unique index on email, and several account
creation paths stored the address exactly as the client typed it. Production
therefore holds pairs of rows differing only by capitalization. Login resolves
users with `lower(email)`, so such a pair used to raise `MultipleResultsFound`
and surface as an HTTP 500 -- the affected people could not sign in at all.

The application-side fix makes login duplicate-tolerant and normalizes every
write, but the duplicate rows themselves are real client records with real
bookings, waivers and messages attached. They have to be merged deliberately,
by a human, before the `ux_ryozan_users_email_lower` unique index can be
created.

WHAT IT DOES
------------
For each group of rows sharing `lower(email)`:

  1. Picks the survivor (see "SURVIVOR RULE").
  2. Repoints every reference to a losing row at the survivor.
  3. RESCUES CREDENTIALS, always: if the survivor has no `google_id` and a
     loser does, it is carried over; likewise a real bcrypt `password_hash`
     when the survivor holds only a placeholder. A merge must never leave
     someone with fewer ways to sign in than they had before.
  4. Reconciles earned, consent, entitlement, profile, OAuth and artist state;
     fills every missing survivor value and reports every ambiguous conflict.
  5. Deletes the losing rows.
  6. Rewrites the survivor's email to its normalized (lowercased) form.

SURVIVOR RULE
-------------
Deliberately NOT "oldest wins". Picking the row that cannot authenticate turns
a fixable outage into a permanent lockout. Ranked, first match wins:

  1. has `google_id`                (can sign in with Google)
  2. has a real bcrypt password hash
  3. is not flagged `must_reset_password`
  4. oldest `created_at`            (the original row, owns the history)
  5. lowest `id` as text            (total order -- always the same answer)

This is the same ordering `app/core/user_lookup.py` uses at request time, so
the row the running application already prefers is the row that survives.

REFERENCE DISCOVERY
-------------------
Referencing columns are DISCOVERED, never guessed:

  * hard references -- every FOREIGN KEY pointing at `ryozan_users.id`, read
    from `pg_constraint` in the live database.
  * soft references -- UUID columns that hold a user id without a FK
    constraint (`ryozan_sms_messages.sender_id`, `ryozan_api_usage.user_id`,
    `ryozan_users.proxy_for`, `ryozan_crm_pipeline_tasks.assignee_id`, ...).
    Derived from the ORM metadata by matching
    known user-reference column names, then verified to exist in the database.

Both lists are printed in full every run, so the reviewer can see exactly what
was considered before anything moves.

SAFETY
------
  * `--dry-run` is the DEFAULT. Nothing is written without `--apply`.
  * A single transaction: it all lands or none of it does.
  * Idempotent: a second run finds no groups and does nothing.
  * `--apply` refuses to run against a database it was not pointed at
    explicitly with `--i-understand-this-writes`.

USAGE
-----
    # look, change nothing (default)
    python scripts/dedupe_user_emails.py

    # inspect one address
    python scripts/dedupe_user_emails.py --email client.alpha@ryozan.app

    # actually merge
    python scripts/dedupe_user_emails.py --apply --i-understand-this-writes

Take a database backup before `--apply`. The delete is not reversible from
inside this script.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENABLE_API_DOCS", "false")
os.environ.setdefault("ENABLE_CONFIG_HEALTH", "false")

import asyncpg  # noqa: E402

from app.api.v1 import router as _router  # noqa: F401,E402 — registers every ORM model
from app.core.config import get_settings  # noqa: E402
from app.core.database import Base  # noqa: E402
from app.core.security import password_hash_fingerprint  # noqa: E402

USERS_TABLE = "ryozan_users"

# Column names that hold a ryozan_users.id without a FOREIGN KEY declaring it.
# Used only to filter ORM columns that are already UUID-typed and unconstrained;
# every hit is verified against the live database before it is touched.
_SOFT_REFERENCE_COLUMN_NAMES = frozenset(
    {
        "user_id",
        "artist_id",
        "client_id",
        "sender_id",
        "owner_id",
        "owner_user_id",
        "created_by",
        "created_by_id",
        "uploaded_by",
        "sent_by",
        "recipient_id",
        "assigned_to",
        "assignee_id",
        "artist_id_reviewed",
        "proxy_for",
    }
)

# Account state is reconciled before a losing row can be deleted. Nullable
# values are always gap-filled. These policy sets cover non-null fields where
# keeping only the survivor would otherwise discard earned or enabled state.
_SUM_FIELDS = frozenset({"bttcoin_balance", "quest_points"})
_OR_FIELDS = frozenset(
    {
        "early_access_galax3",
        "connected_apps_unlocked",
        "stripe_onboarding_complete",
        "booking_allow_same_day",
        "allow_prepay_full",
    }
)
_CONSERVATIVE_AND_FIELDS = frozenset({"marketing_opt_in"})
_ARRAY_UNION_FIELDS = frozenset({"reasons", "specialty", "portfolio_images"})
_JSON_MERGE_FIELDS = frozenset({"product_entitlements", "nexus_profile"})
_MAX_FIELDS = frozenset({"ai_monthly_message_cap"})
_ROLE_ORDER = {"collector": 0, "assistant": 1, "artist": 2, "admin": 3, "founder": 4}
_NON_STATE_FIELDS = frozenset({"id", "email"})

# Unique reference rows with deterministic, loss-aware merge policies. Any
# other live unique-key collision fails closed before a user row is deleted.
_KNOWN_UNIQUE_REFERENCE_TABLES = frozenset(
    {
        "ryozan_notification_preferences",
        "ryozan_user_quests",
        "ryozan_push_subscriptions",
    }
)


@dataclass
class Reference:
    table: str
    column: str
    kind: str  # "fk" | "soft"

    def __str__(self) -> str:
        return f"{self.table}.{self.column} ({self.kind})"


@dataclass(frozen=True)
class UniqueKey:
    table: str
    name: str
    columns: tuple[str, ...]


@dataclass
class Group:
    normalized_email: str
    rows: list[dict]
    canonical_id: UUID | None = None
    survivor: dict = field(init=False)
    losers: list[dict] = field(init=False)

    def __post_init__(self) -> None:
        ordered = sorted(self.rows, key=survivor_rank)
        if self.canonical_id is not None:
            ordered.sort(key=lambda row: row["id"] != self.canonical_id)
            if not ordered or ordered[0]["id"] != self.canonical_id:
                raise ValueError("Canonical artist is missing from the merge group")
            self.rows = ordered
        self.survivor = ordered[0]
        self.losers = ordered[1:]


def has_bcrypt_hash(row: dict) -> bool:
    return (row.get("password_hash") or "").startswith("$2")


def survivor_rank(row: dict) -> tuple:
    """The documented survivor rule as a sort key. Lower sorts first."""
    return (
        0 if row.get("google_id") else 1,
        0 if has_bcrypt_hash(row) else 1,
        1 if row.get("must_reset_password") else 0,
        row.get("created_at") or _far_future(),
        str(row.get("id")),
    )


def credential_rescues(group: Group) -> list[tuple[str, str]]:
    """Credentials a loser holds that the survivor does not. Always applied.

    Returned as (column, description) so the dry-run can show them before
    anything is written.
    """
    rescues: list[tuple[str, str]] = []
    if not group.survivor.get("google_id"):
        donor = next((r for r in group.losers if r.get("google_id")), None)
        if donor:
            rescues.append(("google_id", f"from {donor['id']}"))
    if not has_bcrypt_hash(group.survivor):
        donor = next(
            (
                r
                for r in group.losers
                if has_bcrypt_hash(r) and (group.canonical_id is None or r.get("email_verified_at"))
            ),
            None,
        )
        if donor:
            rescues.append(("password_hash", f"real bcrypt hash from {donor['id']}"))
    return rescues


def _present(value) -> bool:
    return value not in (None, "", [], {})


def _value_key(value) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, default=str)
    return repr(value)


def account_state_plan(group: Group) -> tuple[dict[str, object], list[str]]:
    """Return survivor updates plus a complete audit of differing user state.

    Missing values and earned state are reconciled automatically. When two
    non-empty values are semantically ambiguous, the survivor value is kept
    and the conflict is printed before apply instead of being discarded
    silently.
    """
    rows = group.rows
    survivor = group.survivor
    updates: dict[str, object] = {}
    notes: list[str] = []
    handled = set(_NON_STATE_FIELDS)

    for column, detail in credential_rescues(group):
        donor = next(
            (
                row.get(column)
                for row in group.losers
                if (has_bcrypt_hash(row) if column == "password_hash" else row.get(column))
            ),
            None,
        )
        if donor:
            updates[column] = donor
            notes.append(f"{column}: rescue {detail}")
    handled.update({"password_hash", "google_id"})

    roles = [str(row.get("role") or "collector") for row in rows]
    selected_role = max(roles, key=lambda role: _ROLE_ORDER.get(role, -1))
    if selected_role != survivor.get("role"):
        updates["role"] = selected_role
        notes.append(f"role: preserve highest authority {selected_role!r}")
    handled.add("role")

    for column in _SUM_FIELDS:
        total = sum(int(row.get(column) or 0) for row in rows)
        if total != int(survivor.get(column) or 0):
            updates[column] = total
            notes.append(f"{column}: sum all duplicate balances -> {total}")
    handled.update(_SUM_FIELDS)

    for column in _OR_FIELDS:
        merged = any(bool(row.get(column)) for row in rows)
        if merged != bool(survivor.get(column)):
            updates[column] = merged
            notes.append(f"{column}: preserve enabled state -> {merged}")
    handled.update(_OR_FIELDS)

    for column in _CONSERVATIVE_AND_FIELDS:
        merged = all(bool(row.get(column)) for row in rows)
        if merged != bool(survivor.get(column)):
            updates[column] = merged
            notes.append(f"{column}: conservative consent merge -> {merged}")
    handled.update(_CONSERVATIVE_AND_FIELDS)

    for column in _MAX_FIELDS:
        merged = max(int(row.get(column) or 0) for row in rows)
        if merged != int(survivor.get(column) or 0):
            updates[column] = merged
            notes.append(f"{column}: preserve highest configured value -> {merged}")
    handled.update(_MAX_FIELDS)

    for column in _ARRAY_UNION_FIELDS:
        merged: list = []
        for row in rows:
            for value in row.get(column) or []:
                if value not in merged:
                    merged.append(value)
        if merged and merged != (survivor.get(column) or []):
            updates[column] = merged
            notes.append(f"{column}: union {len(merged)} distinct values")
    handled.update(_ARRAY_UNION_FIELDS)

    for column in _JSON_MERGE_FIELDS:
        merged: dict = {}
        for row in [*group.losers, survivor]:
            value = row.get(column)
            if isinstance(value, dict):
                merged.update(value)
        if merged and merged != (survivor.get(column) or {}):
            updates[column] = merged
            notes.append(f"{column}: merge {len(merged)} keys with survivor precedence")
    handled.update(_JSON_MERGE_FIELDS)

    # A usable login on any duplicate means the merged account is activated.
    reset_required = all(bool(row.get("must_reset_password")) for row in rows)
    if reset_required != bool(survivor.get("must_reset_password")):
        updates["must_reset_password"] = reset_required
        notes.append(f"must_reset_password: merged -> {reset_required}")
    handled.add("must_reset_password")

    # Honor the most recent explicit SMS action. A later STOP always beats an
    # earlier opt-in, and an opt-in without a timestamp is not invented.
    opt_in_at = max((row.get("sms_opt_in_at") for row in rows if row.get("sms_opt_in_at")), default=None)
    opt_out_at = max((row.get("sms_opt_out_at") for row in rows if row.get("sms_opt_out_at")), default=None)
    sms_opt_in = bool(opt_in_at and (not opt_out_at or opt_in_at > opt_out_at))
    for column, value in (
        ("sms_opt_in_at", opt_in_at),
        ("sms_opt_out_at", opt_out_at),
        ("sms_opt_in", sms_opt_in),
    ):
        if value != survivor.get(column):
            updates[column] = value
            notes.append(f"{column}: reconcile latest explicit SMS action")
    handled.update({"sms_opt_in", "sms_opt_in_at", "sms_opt_out_at"})

    created_values = [row.get("created_at") for row in rows if row.get("created_at")]
    updated_values = [row.get("updated_at") for row in rows if row.get("updated_at")]
    if created_values and min(created_values) != survivor.get("created_at"):
        updates["created_at"] = min(created_values)
        notes.append("created_at: preserve earliest account creation")
    if updated_values and max(updated_values) != survivor.get("updated_at"):
        updates["updated_at"] = max(updated_values)
        notes.append("updated_at: preserve latest account update")
    handled.update({"created_at", "updated_at"})

    # Every remaining live column is audited. Missing survivor values are
    # filled automatically; differing non-empty values are explicitly reported.
    for column in sorted(set().union(*(row.keys() for row in rows)) - handled):
        values = [row.get(column) for row in rows]
        donor = next((value for value in values[1:] if _present(value)), None)
        if not _present(survivor.get(column)) and donor is not None:
            updates[column] = donor
            notes.append(f"{column}: fill missing survivor value")
            continue
        distinct = {_value_key(value) for value in values}
        if len(distinct) > 1:
            notes.append(f"{column}: conflict reported, keep survivor value")

    return updates, notes


async def apply_account_state(conn: asyncpg.Connection, group: Group) -> None:
    updates, _notes = account_state_plan(group)
    for column, value in updates.items():
        if column in _JSON_MERGE_FIELDS:
            await conn.execute(
                f'UPDATE "{USERS_TABLE}" SET "{column}" = $1::jsonb WHERE id = $2',
                json.dumps(value),
                group.survivor["id"],
            )
        else:
            await conn.execute(
                f'UPDATE "{USERS_TABLE}" SET "{column}" = $1 WHERE id = $2',
                value,
                group.survivor["id"],
            )


def _far_future():
    from datetime import UTC, datetime

    return datetime.max.replace(tzinfo=UTC)


def normalize(email: str | None) -> str:
    return (email or "").strip().lower()


# ── Reference discovery ────────────────────────────────────────────────────


async def discover_fk_references(conn: asyncpg.Connection) -> list[Reference]:
    """Every FOREIGN KEY column pointing at ryozan_users.id, from the live DB."""
    rows = await conn.fetch(
        """
        SELECT src.relname AS table_name, src_col.attname AS column_name
        FROM pg_constraint c
        JOIN pg_class src ON src.oid = c.conrelid
        JOIN pg_class tgt ON tgt.oid = c.confrelid
        JOIN pg_attribute src_col
          ON src_col.attrelid = c.conrelid AND src_col.attnum = c.conkey[1]
        JOIN pg_attribute tgt_col
          ON tgt_col.attrelid = c.confrelid AND tgt_col.attnum = c.confkey[1]
        WHERE c.contype = 'f'
          AND tgt.relname = $1
          AND tgt_col.attname = 'id'
          AND array_length(c.conkey, 1) = 1
        ORDER BY src.relname, src_col.attname
        """,
        USERS_TABLE,
    )
    return [Reference(r["table_name"], r["column_name"], "fk") for r in rows]


def orm_soft_reference_candidates() -> list[Reference]:
    """UUID columns named like a user reference that carry no FK constraint."""
    found: list[Reference] = []
    for table in Base.metadata.sorted_tables:
        for column in table.columns:
            user_fk = any(fk.target_fullname == "ryozan_users.id" for fk in column.foreign_keys)
            if column.name not in _SOFT_REFERENCE_COLUMN_NAMES and not user_fk:
                continue
            # Production boot may have created the column without the ORM's
            # FK constraint. Only the live constraint sweep can exclude it.
            if "UUID" not in str(column.type).upper():
                continue
            found.append(Reference(table.name, column.name, "soft"))
    return sorted(found, key=lambda r: (r.table, r.column))


async def existing_columns(conn: asyncpg.Connection, refs: list[Reference]) -> list[Reference]:
    """Drop anything the live database does not actually have."""
    live = await conn.fetch(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = current_schema()
        """
    )
    present = {(r["table_name"], r["column_name"]) for r in live}
    return [r for r in refs if (r.table, r.column) in present]


async def discover_references(conn: asyncpg.Connection) -> tuple[list[Reference], list[Reference]]:
    fk_refs = await discover_fk_references(conn)
    fk_pairs = {(r.table, r.column) for r in fk_refs}
    soft_refs = [r for r in orm_soft_reference_candidates() if (r.table, r.column) not in fk_pairs]
    soft_refs = await existing_columns(conn, soft_refs)
    return fk_refs, soft_refs


async def discover_unique_keys(conn: asyncpg.Connection) -> list[UniqueKey]:
    """Return non-partial, column-backed unique keys from the live schema."""
    rows = await conn.fetch(
        """
        SELECT tbl.relname AS table_name,
               idx.relname AS index_name,
               array_agg(att.attname ORDER BY key.ordinality) AS columns
        FROM pg_index i
        JOIN pg_class tbl ON tbl.oid = i.indrelid
        JOIN pg_class idx ON idx.oid = i.indexrelid
        CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS key(attnum, ordinality)
        JOIN pg_attribute att ON att.attrelid = tbl.oid AND att.attnum = key.attnum
        WHERE i.indisunique
          AND i.indpred IS NULL
          AND key.attnum > 0
        GROUP BY tbl.relname, idx.relname
        ORDER BY tbl.relname, idx.relname
        """
    )
    return [UniqueKey(row["table_name"], row["index_name"], tuple(row["columns"])) for row in rows]


async def unique_collision_count(
    conn: asyncpg.Connection,
    group: Group,
    ref: Reference,
    key: UniqueKey,
) -> int:
    """Count key values that would collide after loser ids are repointed."""
    other_columns = [column for column in key.columns if column != ref.column]
    if not other_columns:
        group_by = "1"
        projection = "1 AS singleton"
    else:
        projection = ", ".join(f'"{column}"' for column in other_columns)
        group_by = projection
    return int(
        await conn.fetchval(
            f"""
            SELECT count(*)
            FROM (
                SELECT {projection}
                FROM "{ref.table}"
                WHERE "{ref.column}" = $1 OR "{ref.column}" = ANY($2::uuid[])
                GROUP BY {group_by}
                HAVING bool_or("{ref.column}" = $1)
                   AND bool_or("{ref.column}" = ANY($2::uuid[]))
            ) collisions
            """,
            group.survivor["id"],
            [row["id"] for row in group.losers],
        )
        or 0
    )


async def lock_write_set(conn: asyncpg.Connection, references: list[Reference]) -> None:
    """Block application writes to every table participating in the merge."""
    tables = sorted({USERS_TABLE, *(ref.table for ref in references)})
    quoted = ", ".join(f'"{table}"' for table in tables)
    await conn.execute(f"LOCK TABLE {quoted} IN SHARE ROW EXCLUSIVE MODE")


async def merge_known_unique_rows(
    conn: asyncpg.Connection,
    group: Group,
    ref: Reference,
) -> None:
    """Resolve overlapping per-user rows without losing progress or settings."""
    survivor_id = group.survivor["id"]
    loser_ids = [row["id"] for row in group.losers]

    if ref.table == "ryozan_notification_preferences" and ref.column == "user_id":
        await conn.execute(
            """
            WITH merged AS (
                SELECT bool_and(email_bookings) AS email_bookings,
                       bool_and(email_messages) AS email_messages,
                       bool_and(email_quests) AS email_quests,
                       bool_and(email_marketing) AS email_marketing,
                       bool_and(push_bookings) AS push_bookings,
                       bool_and(push_messages) AS push_messages,
                       bool_and(push_quests) AS push_quests,
                       max(updated_at) AS updated_at
                FROM ryozan_notification_preferences
                WHERE user_id = $1 OR user_id = ANY($2::uuid[])
            )
            UPDATE ryozan_notification_preferences target
            SET email_bookings = merged.email_bookings,
                email_messages = merged.email_messages,
                email_quests = merged.email_quests,
                email_marketing = merged.email_marketing,
                push_bookings = merged.push_bookings,
                push_messages = merged.push_messages,
                push_quests = merged.push_quests,
                updated_at = merged.updated_at
            FROM merged
            WHERE target.user_id = $1
            """,
            survivor_id,
            loser_ids,
        )
        await conn.execute("DELETE FROM ryozan_notification_preferences WHERE user_id = ANY($1::uuid[])", loser_ids)
        return

    if ref.table == "ryozan_user_quests" and ref.column == "user_id":
        await conn.execute(
            """
            WITH merged_rows AS (
                SELECT quest_id,
                       max(progress) AS progress,
                       bool_or(completed) AS completed,
                       bool_or(claimed) AS claimed,
                       min(completed_at) FILTER (WHERE completed_at IS NOT NULL) AS completed_at,
                       min(claimed_at) FILTER (WHERE claimed_at IS NOT NULL) AS claimed_at,
                       min(created_at) AS created_at
                FROM ryozan_user_quests
                WHERE user_id = $1 OR user_id = ANY($2::uuid[])
                GROUP BY quest_id
                HAVING bool_or(user_id = $1) AND bool_or(user_id = ANY($2::uuid[]))
            )
            UPDATE ryozan_user_quests target
            SET progress = merged_rows.progress,
                completed = merged_rows.completed,
                claimed = merged_rows.claimed,
                completed_at = merged_rows.completed_at,
                claimed_at = merged_rows.claimed_at,
                created_at = merged_rows.created_at
            FROM merged_rows
            WHERE target.user_id = $1 AND target.quest_id = merged_rows.quest_id
            """,
            survivor_id,
            loser_ids,
        )
        await conn.execute(
            """
            DELETE FROM ryozan_user_quests loser
            USING ryozan_user_quests survivor
            WHERE loser.user_id = ANY($1::uuid[])
              AND survivor.user_id = $2
              AND survivor.quest_id = loser.quest_id
            """,
            loser_ids,
            survivor_id,
        )
        return

    if ref.table == "ryozan_push_subscriptions" and ref.column == "user_id":
        await conn.execute(
            """
            WITH latest AS (
                SELECT DISTINCT ON (endpoint) endpoint, p256dh, auth
                FROM ryozan_push_subscriptions
                WHERE user_id = $1 OR user_id = ANY($2::uuid[])
                ORDER BY endpoint, created_at DESC
            )
            UPDATE ryozan_push_subscriptions target
            SET p256dh = latest.p256dh, auth = latest.auth
            FROM latest
            WHERE target.user_id = $1 AND target.endpoint = latest.endpoint
            """,
            survivor_id,
            loser_ids,
        )
        await conn.execute(
            """
            DELETE FROM ryozan_push_subscriptions loser
            USING ryozan_push_subscriptions survivor
            WHERE loser.user_id = ANY($1::uuid[])
              AND survivor.user_id = $2
              AND survivor.endpoint = loser.endpoint
            """,
            loser_ids,
            survivor_id,
        )
        return

    raise RuntimeError(f"No merge policy for {ref.table}.{ref.column}")


# ── Group discovery ────────────────────────────────────────────────────────


async def find_duplicate_groups(
    conn: asyncpg.Connection,
    only_email: str | None,
    *,
    for_update: bool = False,
) -> list[Group]:
    params: list = []
    where = ""
    if only_email:
        where = "WHERE lower(email) = $1"
        params.append(normalize(only_email))

    lock_clause = " FOR UPDATE" if for_update else ""
    rows = await conn.fetch(
        f"""
        SELECT *
        FROM {USERS_TABLE}
        {where}
        {lock_clause}
        """,
        *params,
    )

    by_email: dict[str, list[dict]] = {}
    for row in rows:
        by_email.setdefault(normalize(row["email"]), []).append(dict(row))

    return [Group(email, rs) for email, rs in sorted(by_email.items()) if len(rs) > 1]


# ── Planning + execution ───────────────────────────────────────────────────


async def plan_group(
    conn: asyncpg.Connection,
    group: Group,
    references: list[Reference],
) -> list[tuple[Reference, str, int]]:
    """Count what would move, per reference column, per losing row."""
    moves: list[tuple[Reference, str, int]] = []
    loser_ids = [r["id"] for r in group.losers]
    for ref in references:
        count = await conn.fetchval(
            f'SELECT count(*) FROM "{ref.table}" WHERE "{ref.column}" = ANY($1::uuid[])',
            loser_ids,
        )
        if count:
            moves.append((ref, str(group.survivor["id"]), count))
    return moves


async def apply_group(
    conn: asyncpg.Connection,
    group: Group,
    references: list[Reference],
    unique_keys: list[UniqueKey],
) -> None:
    survivor_id = group.survivor["id"]
    loser_ids = [r["id"] for r in group.losers]

    if group.canonical_id is not None:
        await preserve_artist_identities(conn, group, references)

    for ref in references:
        if ref.table == USERS_TABLE and ref.column == "id":
            continue
        keys = [key for key in unique_keys if key.table == ref.table and ref.column in key.columns]
        collisions = [key for key in keys if await unique_collision_count(conn, group, ref, key)]
        if collisions:
            if ref.table not in _KNOWN_UNIQUE_REFERENCE_TABLES:
                names = ", ".join(key.name for key in collisions)
                raise RuntimeError(
                    f"Ambiguous unique-key collision in {ref.table}.{ref.column} ({names}); "
                    "no rows were changed. Reconcile this state before rerunning apply."
                )
            await merge_known_unique_rows(conn, group, ref)
        await conn.execute(
            f'UPDATE "{ref.table}" SET "{ref.column}" = $1 WHERE "{ref.column}" = ANY($2::uuid[])',
            survivor_id,
            loser_ids,
        )

    # Release unique provider subjects only after their recovery snapshots and
    # aliases exist. The transaction rolls everything back on any later error.
    await conn.execute(f"UPDATE {USERS_TABLE} SET google_id = NULL WHERE id = ANY($1::uuid[])", loser_ids)
    await apply_account_state(conn, group)

    if group.canonical_id is not None:
        # The legacy table has no unique participant constraint. Collapse only
        # this artist's repeated memberships, conservatively preserving unread
        # state; original rows are retained in the private recovery snapshot.
        await conn.execute(
            """WITH state AS (
                 SELECT conversation_id, min(joined_at) AS joined_at,
                   CASE WHEN bool_or(last_read_at IS NULL) THEN NULL ELSE min(last_read_at) END AS last_read_at
                 FROM ryozan_conversation_participants WHERE user_id=$1 GROUP BY conversation_id
               ) UPDATE ryozan_conversation_participants p
                 SET joined_at=s.joined_at,last_read_at=s.last_read_at
                 FROM state s WHERE p.user_id=$1 AND p.conversation_id=s.conversation_id""",
            survivor_id,
        )
        await conn.execute(
            """DELETE FROM ryozan_conversation_participants WHERE id IN (
                 SELECT id FROM (
                   SELECT id,row_number() OVER(PARTITION BY conversation_id ORDER BY joined_at,id) AS n
                   FROM ryozan_conversation_participants WHERE user_id=$1
                 ) repeated WHERE n>1
               )""",
            survivor_id,
        )

    await conn.execute(f"DELETE FROM {USERS_TABLE} WHERE id = ANY($1::uuid[])", loser_ids)

    # Leave the surviving row canonical, so it matches what the ORM now writes.
    await conn.execute(
        f"UPDATE {USERS_TABLE} SET email = $1 WHERE id = $2 AND email <> $1",
        group.normalized_email,
        survivor_id,
    )


async def preserve_artist_identities(conn, group, references) -> None:
    updates, _notes = account_state_plan(group)
    password = updates.get("password_hash", group.survivor["password_hash"])
    binding = password_hash_fingerprint(password)
    snapshots = {}
    ids = [row["id"] for row in group.rows]
    for ref in references:
        rows = await conn.fetch(
            f'SELECT to_jsonb(t) AS snapshot FROM "{ref.table}" t WHERE "{ref.column}" = ANY($1::uuid[])',
            ids,
        )
        if rows:
            snapshots[str(ref)] = [json.loads(row["snapshot"]) for row in rows]
    for row in group.losers:
        if not row.get("google_id") and not row.get("email_verified_at"):
            raise RuntimeError("Collector identity has no mailbox/provider proof; explicit identity review is required")
        await conn.execute(
            """INSERT INTO ryozan_user_identity_aliases
               (id,user_id,email,google_id,password_hash,password_binding,source_snapshot,reference_snapshot,created_at)
               VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,now())""",
            row["id"],
            group.survivor["id"],
            row["email"].strip().lower(),
            row.get("google_id"),
            row.get("password_hash") if row.get("email_verified_at") else None,
            binding,
            json.dumps({"artist": group.survivor, "merged": row}, default=str),
            json.dumps(snapshots, default=str),
        )


async def find_artist_group(conn, args, *, for_update=False):
    canonical_id = UUID(args.artist_id)
    loser_ids = [UUID(value) for value in args.merge_user_id]
    if canonical_id in loser_ids or len(set(loser_ids)) != len(loser_ids):
        raise ValueError("Artist and collector IDs must be distinct")
    ids = [canonical_id, *loser_ids]
    rows = [
        dict(row)
        for row in await conn.fetch(
            f"SELECT * FROM {USERS_TABLE} WHERE id = ANY($1::uuid[])" + (" FOR UPDATE" if for_update else ""),
            ids,
        )
    ]
    if len(rows) != len(ids):
        aliases = await conn.fetch(
            "SELECT id,user_id FROM ryozan_user_identity_aliases WHERE id = ANY($1::uuid[])",
            loser_ids,
        )
        if (
            len(rows) == 1
            and rows[0]["id"] == canonical_id
            and rows[0]["role"] == "artist"
            and rows[0]["email"] == args.canonical_email.strip().lower()
            and len(aliases) == len(loser_ids)
            and all(row["user_id"] == canonical_id for row in aliases)
        ):
            return []
        raise ValueError("Missing user or partially reconciled merge; no changes made")
    artist = next(row for row in rows if row["id"] == canonical_id)
    if (
        artist["role"] != "artist"
        or not artist.get("slug")
        or any(row["role"] != "collector" or row.get("slug") for row in rows if row["id"] != canonical_id)
    ):
        raise ValueError("Explicit merge requires one original artist and collector-only duplicates")
    email = args.canonical_email.strip().lower()
    if email not in {row["email"].strip().lower() for row in rows}:
        raise ValueError("Canonical email must belong to a reviewed source account")
    conflict = await conn.fetchval(
        "SELECT EXISTS(SELECT 1 FROM ryozan_users WHERE lower(email)=$1 AND NOT(id=ANY($2::uuid[]))) "
        "OR EXISTS(SELECT 1 FROM ryozan_user_identity_aliases WHERE email=$1 AND NOT(user_id=ANY($2::uuid[])))",
        email,
        ids,
    )
    if conflict:
        raise ValueError("Canonical email belongs to another account; no changes made")
    return [Group(email, rows, canonical_id)]


def describe_row(row: dict, label: str) -> str:
    return (
        f"    {label:<9} {row['id']}  email={row['email']!r}  name={row.get('name')!r}  "
        f"role={row.get('role')}  google_id={'yes' if row.get('google_id') else 'no'}  "
        f"bcrypt={'yes' if (row.get('password_hash') or '').startswith('$2') else 'NO'}  "
        f"must_reset={bool(row.get('must_reset_password'))}  created={row.get('created_at')}"
    )


# ── Entrypoint ─────────────────────────────────────────────────────────────


def _sync_dsn() -> str:
    url = get_settings().DATABASE_URL
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


async def run(args: argparse.Namespace) -> int:
    dsn = _sync_dsn()
    redacted = dsn.split("@")[-1] if "@" in dsn else dsn
    mode = "APPLY (writes)" if args.apply else "DRY RUN (no writes)"

    print("=" * 78)
    print(f"ryozan_users email dedupe — {mode}")
    print(f"database: {redacted}")
    print("=" * 78)

    conn = await asyncpg.connect(dsn)
    try:
        fk_refs, soft_refs = await discover_references(conn)
        references = [r for r in fk_refs + soft_refs if not (r.table == USERS_TABLE and r.column == "id")]
        unique_keys = await discover_unique_keys(conn)

        print(f"\nreferencing columns discovered: {len(references)}")
        print(f"  foreign keys ({len(fk_refs)}):")
        for ref in fk_refs:
            print(f"    {ref}")
        print(f"  soft references, no FK constraint ({len(soft_refs)}):")
        for ref in soft_refs:
            print(f"    {ref}")

        groups = (
            await find_artist_group(conn, args) if args.artist_id else await find_duplicate_groups(conn, args.email)
        )
        if not groups:
            print("\nNo duplicate email groups found. Nothing to do.")
            print("The ux_ryozan_users_email_lower unique index can be created.")
            return 0

        print(f"\nduplicate groups: {len(groups)}")
        total_moves = 0
        for group in groups:
            print(f"\n── {group.normalized_email} — {len(group.rows)} rows")
            print(describe_row(group.survivor, "SURVIVOR"))
            for loser in group.losers:
                print(describe_row(loser, "delete"))

            rescues = credential_rescues(group)
            if rescues:
                print("    credentials rescued onto the survivor:")
                for column, detail in rescues:
                    print(f"      {column} <- {detail}")

            _updates, state_notes = account_state_plan(group)
            if state_notes:
                print("    account state reconciliation:")
                for note in state_notes:
                    print(f"      {note}")

            moves = await plan_group(conn, group, references)
            if moves:
                print("    references to repoint:")
                for ref, target, count in moves:
                    print(f"      {count:>4} row(s) in {ref.table}.{ref.column} -> {target}")
                    total_moves += count
            else:
                print("    references to repoint: none")

            for ref in references:
                keys = [key for key in unique_keys if key.table == ref.table and ref.column in key.columns]
                for key in keys:
                    collision_count = await unique_collision_count(conn, group, ref, key)
                    if not collision_count:
                        continue
                    policy = "merge policy" if ref.table in _KNOWN_UNIQUE_REFERENCE_TABLES else "APPLY BLOCKED"
                    print(f"    unique collision: {ref.table}.{key.name} ({collision_count} key value(s)) -> {policy}")

        print(f"\ntotal references to repoint: {total_moves}")
        print(f"total user rows to delete:   {sum(len(g.losers) for g in groups)}")

        if not args.apply:
            print("\nDRY RUN — nothing was written.")
            print("Re-run with --apply --i-understand-this-writes to perform the merge.")
            return 0

        async with conn.transaction():
            await lock_write_set(conn, references)
            locked_groups = (
                await find_artist_group(conn, args, for_update=True)
                if args.artist_id
                else await find_duplicate_groups(conn, args.email, for_update=True)
            )
            if not locked_groups:
                print("\nNo duplicate groups remained after acquiring write locks. Nothing was changed.")
                return 0
            for group in locked_groups:
                await apply_group(conn, group, references, unique_keys)
        print("\nAPPLIED. All groups merged in one transaction.")

        remaining = (
            await find_artist_group(conn, args) if args.artist_id else await find_duplicate_groups(conn, args.email)
        )
        print(f"verification: duplicate groups remaining = {len(remaining)}")
        return 0 if not remaining else 1
    finally:
        await conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--email", help="Restrict to one address (case-insensitive).")
    parser.add_argument("--artist-id", help="Original artist ID for an operator-confirmed cross-email consolidation.")
    parser.add_argument(
        "--merge-user-id", action="append", default=[], help="Confirmed collector ID; repeat as needed."
    )
    parser.add_argument("--canonical-email", help="Reviewed source email to retain as the artist's primary address.")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Perform the merge. Without this the script is a dry run.",
    )
    parser.add_argument(
        "--i-understand-this-writes",
        dest="confirmed",
        action="store_true",
        help="Required alongside --apply. Deletes user rows; take a backup first.",
    )
    parser.add_argument(
        "--fill-gaps",
        action="store_true",
        help="Deprecated compatibility flag. Missing survivor state is now always preserved.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Explicit no-op flag. Dry run is already the default.",
    )
    args = parser.parse_args()

    if (args.artist_id or args.merge_user_id or args.canonical_email) and (
        not (args.artist_id and args.merge_user_id and args.canonical_email) or args.email
    ):
        parser.error(
            "Artist consolidation requires --artist-id, --merge-user-id and --canonical-email, without --email"
        )

    if args.apply and not args.confirmed:
        parser.error("--apply also requires --i-understand-this-writes")
    if args.dry_run:
        args.apply = False

    return asyncio.run(run(args))


if __name__ == "__main__":
    raise SystemExit(main())

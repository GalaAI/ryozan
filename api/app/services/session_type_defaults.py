"""Canonical idempotent installer for the four tattoo session defaults.

Net-new artifact justification:
The same defaults already exist in booking/profile UI fallbacks, but no backend
owner installs them. This module extends the existing ArtistSessionType owner
and is shared by startup and Alembic so neither path invents duplicate rows.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

_DEFAULT_SESSION_NAMESPACE = uuid.UUID("44bc657f-711a-4cff-8df0-b09c31f5973c")

DEFAULT_SESSION_TYPE_SPECS = (
    {
        "label": "45 Minute Mini Tattoo",
        "size": "small",
        "duration_minutes": 45,
        "buffer_minutes": 0,
        "deposit_amount": 50,
        "hourly_rate": 150,
        "price_min": 100,
        "price_max": 150,
        "sort_order": 10,
    },
    {
        "label": "3 Hour Flash, Palm Sized",
        "size": "small",
        "duration_minutes": 180,
        "buffer_minutes": 0,
        "deposit_amount": 100,
        "hourly_rate": 175,
        "price_min": 400,
        "price_max": 650,
        "sort_order": 20,
    },
    {
        "label": "Half Day Session",
        "size": "large",
        "duration_minutes": 240,
        "buffer_minutes": 60,
        "deposit_amount": 200,
        "hourly_rate": 175,
        "price_min": 700,
        "price_max": 1000,
        "sort_order": 30,
    },
    {
        "label": "Full Day Session",
        "size": "xlarge",
        "duration_minutes": 480,
        "buffer_minutes": 0,
        "deposit_amount": 300,
        "hourly_rate": 175,
        "price_min": 1200,
        "price_max": 1600,
        "sort_order": 40,
    },
)


def missing_default_session_rows(
    artist_ids: list[uuid.UUID],
    existing_pairs: set[tuple[uuid.UUID, str]],
) -> list[dict]:
    rows: list[dict] = []
    normalized_existing = {(artist_id, label.casefold()) for artist_id, label in existing_pairs}
    for artist_id in artist_ids:
        for spec in DEFAULT_SESSION_TYPE_SPECS:
            if (artist_id, spec["label"].casefold()) in normalized_existing:
                continue
            rows.append(
                {
                    # Deterministic IDs let concurrent seeders contend on the
                    # existing primary key without disturbing legacy random-ID rows.
                    "id": uuid.uuid5(_DEFAULT_SESSION_NAMESPACE, f"{artist_id}:{spec['label'].casefold()}"),
                    "artist_id": artist_id,
                    "description": None,
                    "pricing_mode": "range",
                    "placements": [],
                    "is_active": True,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                    **spec,
                }
            )
    return rows


def seed_default_session_types(connection) -> int:
    """Insert only missing default labels for every artist/founder account."""
    inspector = sa.inspect(connection)
    if not inspector.has_table("bttco_users") or not inspector.has_table("bttco_artist_session_types"):
        return 0

    users = sa.table(
        "bttco_users",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("role", sa.String()),
    )
    session_types = sa.table(
        "bttco_artist_session_types",
        sa.column("id", UUID(as_uuid=True)),
        sa.column("artist_id", UUID(as_uuid=True)),
        sa.column("label", sa.String()),
        sa.column("description", sa.Text()),
        sa.column("pricing_mode", sa.String()),
        sa.column("size", sa.String()),
        sa.column("placements", ARRAY(sa.String())),
        sa.column("duration_minutes", sa.Integer()),
        sa.column("buffer_minutes", sa.Integer()),
        sa.column("deposit_amount", sa.Numeric(10, 2)),
        sa.column("hourly_rate", sa.Numeric(10, 2)),
        sa.column("price_min", sa.Numeric(10, 2)),
        sa.column("price_max", sa.Numeric(10, 2)),
        sa.column("is_active", sa.Boolean()),
        sa.column("sort_order", sa.Integer()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    artist_ids = list(
        connection.execute(sa.select(users.c.id).where(users.c.role.in_(["artist", "founder"]))).scalars()
    )
    if not artist_ids:
        return 0
    existing_pairs = {
        (row.artist_id, row.label)
        for row in connection.execute(
            sa.select(session_types.c.artist_id, session_types.c.label).where(session_types.c.artist_id.in_(artist_ids))
        )
    }
    rows = missing_default_session_rows(artist_ids, existing_pairs)
    if rows:
        if connection.dialect.name == "postgresql":
            statement = postgresql_insert(session_types).on_conflict_do_nothing(index_elements=["id"])
        elif connection.dialect.name == "sqlite":
            statement = sqlite_insert(session_types).on_conflict_do_nothing(index_elements=["id"])
        else:
            statement = sa.insert(session_types)
        result = connection.execute(statement, rows)
        return max(int(result.rowcount or 0), 0)
    return len(rows)

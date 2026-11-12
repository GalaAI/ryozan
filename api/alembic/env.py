"""
Alembic migration environment: the one path that builds and changes Ryozan's schema.

The chain starts at 20261031_baseline (the complete schema the old boot path
built). The container runs `alembic upgrade head` before the API starts; the
API itself issues no DDL. Connects with the sync (psycopg2) form of DATABASE_URL.
"""

from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection

from alembic import context

# Register exactly the models the running API registers, by importing the same
# modules main.py imports. A hand-kept list here drifted from the app: it named
# models the app never loads (guild, since deleted) and missed ones it does
# (booking add-ons, rewards), so autogenerate compared against a schema nobody runs.
from app.api.v1 import router as _api_router  # noqa: F401
from app.core.config import get_settings, psycopg2_url
from app.core.database import Base
from app.mcp import oauth as _mcp_oauth  # noqa: F401
from app.mcp import router as _mcp_router  # noqa: F401

settings = get_settings()
config = context.config

# Override sqlalchemy.url — ensure sync (psycopg2) driver for migrations
config.set_main_option("sqlalchemy.url", psycopg2_url(settings.DATABASE_SYNC_URL))

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

BTTCO_OWNED_TABLE_PREFIXES = ("ryozan_", "viber_")
BTTCO_OWNED_TABLES = set(target_metadata.tables)


def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
    """Keep autogenerate scoped to BTTCO-owned tables in the shared Nexus database."""
    if type_ == "table":
        if name is None:
            return True
        return name in BTTCO_OWNED_TABLES or name.startswith(BTTCO_OWNED_TABLE_PREFIXES)

    table_name = parent_names.get("table_name")
    if table_name:
        return table_name in BTTCO_OWNED_TABLES or table_name.startswith(BTTCO_OWNED_TABLE_PREFIXES)

    return True


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode — generates SQL without connecting."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        include_name=include_name,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, include_name=include_name)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode — connects to the database using sync engine."""
    from sqlalchemy import create_engine

    connectable = create_engine(
        config.get_main_option("sqlalchemy.url"),
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

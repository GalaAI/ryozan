"""Idempotently create all ORM tables on the DATABASE_URL target.

Used by scripts/mirror-prod-db.ps1 so a fresh docker db has schema before the
sync scripts import data. Local-only guard mirrors the other dev scripts.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import os

os.environ.setdefault("ENABLE_API_DOCS", "false")
os.environ.setdefault("ENABLE_CONFIG_HEALTH", "false")

# app/models is a namespace package (no __init__), so a bare import registers
# nothing. The API's own schema path relies on the full import chain pulling in
# every model module; mirror that by importing the v1 router.
from app.api.v1 import router as _router  # noqa: F401
from app.core.config import get_settings
from app.core.database import Base, engine


def _is_local(url: str) -> bool:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def _stamp_head() -> None:
    """Mark a create_all schema as being at the Alembic head.

    create_all builds the current ORM shape directly, so the database is at
    head but has no alembic_version row to say so. Without the stamp, the next
    `alembic upgrade head` replays every migration against tables that already
    exist and fails, which is why migrations were only ever exercised for the
    first time in production. Stamping closes that gap.
    """
    from alembic.config import Config

    from alembic import command

    api_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(api_root / "alembic.ini"))
    # alembic.ini carries `script_location = alembic`, and Alembic resolves that
    # against the *current working directory*, not against the ini it came from.
    # Passing an absolute ini path is therefore not enough: run this from the
    # repo root, as mirror-prod-db.ps1 does, and Alembic reports
    # "Path doesn't exist: alembic". Pin it so the script works from anywhere.
    cfg.set_main_option("script_location", str(api_root / "alembic"))

    # purge clears alembic_version before stamping. Without it, Alembic first
    # resolves whatever revision the row already holds, so a database left
    # stamped at a migration that has since been deleted from the repo fails
    # with "Can't locate revision identified by ...". A local dev database is
    # rebuilt from create_all directly above, so the old row carries no
    # information worth preserving.
    command.stamp(cfg, "head", purge=True)


async def main() -> int:
    url = get_settings().DATABASE_URL
    if not _is_local(url):
        print("Refusing: DATABASE_URL is not local.")
        return 2
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _stamp_head()
    print(
        f"Schema ensured and stamped at head on {urlparse(url.replace('postgresql+asyncpg://', 'postgresql://', 1)).netloc}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

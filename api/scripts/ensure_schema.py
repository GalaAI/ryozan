"""Bring a LOCAL database to the current schema with `alembic upgrade head`.

Used by scripts/mirror-prod-db.ps1 so a fresh docker db has schema before the
sync scripts import data. Local-only guard mirrors the other dev scripts.

There is one schema path, Alembic. This used to run Base.metadata.create_all
and stamp head, a second path that could disagree with the migrations; now it
runs the same migrations the container runs before it starts the API. A local
database built by the old boot path (tables, no alembic_version) is refused by
the baseline revision; adopt it with `python scripts/adopt_schema.py --apply`.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

os.environ.setdefault("ENABLE_API_DOCS", "false")
os.environ.setdefault("ENABLE_CONFIG_HEALTH", "false")

from app.core.config import get_settings  # noqa: E402


def _is_local(url: str) -> bool:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def main() -> int:
    from alembic.config import Config

    from alembic import command

    url = get_settings().DATABASE_URL
    if not _is_local(url):
        print("Refusing: DATABASE_URL is not local.")
        return 2
    api_root = Path(__file__).resolve().parents[1]
    cfg = Config(str(api_root / "alembic.ini"))
    # alembic.ini carries `script_location = alembic`, which Alembic resolves
    # against the current working directory. Pin it so this works from anywhere.
    cfg.set_main_option("script_location", str(api_root / "alembic"))
    command.upgrade(cfg, "head")
    print(f"Schema at Alembic head on {urlparse(url.replace('postgresql+asyncpg://', 'postgresql://', 1)).netloc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

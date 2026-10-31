"""Create or repair a local-only Ryozan login (first-run account for a fresh install).

Usage (from api/):  python scripts/bootstrap_dev_login.py
Prints the email and the generated password, which is also kept in
.dev/state/dev-login-password.txt (gitignored).

This script refuses to run against non-local databases unless explicitly
overridden. It is for local development only and never touches production.
"""

from __future__ import annotations

import argparse
import asyncio
import secrets
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import func, select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.config import get_settings
from app.core.database import async_session
from app.core.security import hash_password
from app.models.user import BTTCOUser


def _is_local_database(url: str) -> bool:
    parsed = urlparse(url.replace("postgresql+asyncpg://", "postgresql://", 1))
    return parsed.hostname in {"localhost", "127.0.0.1", "::1"}


def _password_file() -> Path:
    return Path(__file__).resolve().parents[2] / ".dev" / "state" / "dev-login-password.txt"


def _load_or_create_password(explicit_password: str | None) -> str:
    if explicit_password:
        return explicit_password

    path = _password_file()
    if path.exists():
        return path.read_text(encoding="utf-8").strip()

    path.parent.mkdir(parents=True, exist_ok=True)
    password = f"ryozan-dev-{secrets.token_urlsafe(12)}"
    path.write_text(password, encoding="utf-8")
    return password


async def _free_slug(session, wanted: str) -> str:
    """Return `wanted`, or the first free `wanted-dev-N` if it is taken.

    Users are matched by email, but `slug` carries its own unique constraint.
    After a mirror import the local database can already hold the wanted slug
    under a different address, and inserting then fails on
    ryozan_users_slug_key. Creating a dev login must not depend on which rows a
    mirror happened to bring in, and must never overwrite an unrelated account
    that merely shares a slug, so step aside instead.
    """
    taken = await session.execute(select(BTTCOUser).where(BTTCOUser.slug == wanted))
    if taken.scalars().first() is None:
        return wanted

    for suffix in range(1, 100):
        candidate = f"{wanted}-dev" if suffix == 1 else f"{wanted}-dev-{suffix}"
        clash = await session.execute(select(BTTCOUser).where(BTTCOUser.slug == candidate))
        if clash.scalars().first() is None:
            print(f"Slug '{wanted}' is already taken locally; using '{candidate}'.")
            return candidate

    raise RuntimeError(f"No free slug derived from '{wanted}' after 99 attempts.")


async def bootstrap_dev_login(args: argparse.Namespace) -> int:
    settings = get_settings()
    if not args.allow_nonlocal_db and not _is_local_database(settings.DATABASE_URL):
        print("Refusing to reset login: DATABASE_URL is not local.")
        return 2

    email = args.email.strip().lower()
    password = _load_or_create_password(args.password)

    async with async_session() as session:
        result = await session.execute(select(BTTCOUser).where(func.lower(BTTCOUser.email) == email))
        user = result.scalar_one_or_none()
        if user is None:
            user = BTTCOUser(
                email=email,
                password_hash=hash_password(password),
                name=args.name,
                role=args.role,
                slug=await _free_slug(session, args.slug),
                must_reset_password=False,
            )
            session.add(user)
            action = "created"
        else:
            user.password_hash = hash_password(password)
            user.name = args.name
            user.role = args.role
            user.must_reset_password = False
            user.updated_at = datetime.now(UTC)
            action = "updated"

        await session.flush()
        primary_user = user

        if args.with_admin:
            manager_email = args.manager_email.strip().lower()
            manager_result = await session.execute(
                select(BTTCOUser).where(func.lower(BTTCOUser.email) == manager_email)
            )
            manager = manager_result.scalar_one_or_none()
            if manager is None:
                manager = BTTCOUser(
                    email=manager_email,
                    password_hash=hash_password(password),
                    name=args.manager_name,
                    role="admin",
                    slug=await _free_slug(session, args.manager_slug),
                    proxy_for=primary_user.id,
                    must_reset_password=False,
                )
                session.add(manager)
                manager_action = "created"
            else:
                manager.password_hash = hash_password(password)
                manager.name = args.manager_name
                manager.role = "admin"
                manager.proxy_for = primary_user.id
                manager.must_reset_password = False
                manager.updated_at = datetime.now(UTC)
                manager_action = "updated"

        await session.commit()

    print(f"Local dev login {action}: {email}")
    print(f"Password: {password}")
    print(f"Stored at: {_password_file()}")
    if args.with_admin:
        print(f"Local manager login {manager_action}: {manager_email}")
        print("Manager role: admin")
        print(f"Manager proxy_for: {primary_user.id}")
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--email", default="founder@ryozan.app")
    parser.add_argument("--name", default="Studio Founder")
    parser.add_argument("--role", default="founder")
    parser.add_argument("--slug", default="founder")
    parser.add_argument("--password")
    parser.add_argument("--with-admin", action="store_true", help="also create a studio admin that proxies for the founder")
    parser.add_argument("--manager-email", default="admin@ryozan.app")
    parser.add_argument("--manager-name", default="Studio Admin")
    parser.add_argument("--manager-slug", default="studio-admin")
    parser.add_argument("--allow-nonlocal-db", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(bootstrap_dev_login(parse_args())))

"""
Every artist needs a slug: it is how the public pages, the artist list and the
booking calendar find them. An invite may name one; otherwise it comes from the
person's name.
"""

import re

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import User

ARTIST_ACCOUNT_ROLES = ("artist", "founder")
_MAX_SLUG_LENGTH = 100


async def unique_artist_slug(db: AsyncSession, name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[: _MAX_SLUG_LENGTH - 8].strip("-") or "artist"
    candidate = base
    suffix = 1
    while await db.scalar(select(User.id).where(User.slug == candidate)):
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate

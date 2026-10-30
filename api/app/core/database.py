"""
Database session management.

Uses SQLAlchemy 2.0 async engine connected to the nexus PostgreSQL database.
All BTTCO tables currently live in the 'public' schema with 'bttco_' prefix.
"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    pool_size=10,
    max_overflow=20,
    # pool_pre_ping issues a lightweight `SELECT 1` against each connection before
    # handing it out, transparently reconnecting if the underlying socket is dead.
    # Without this, Railway-Postgres restarts (or any TCP blip) leave stale sockets
    # in the pool, causing asyncpg.ConnectionDoesNotExistError ("connection was
    # closed in the middle of operation") on every request until the service is
    # manually restarted.
    pool_pre_ping=True,
    # Recycle connections every 30 minutes so we proactively rotate before
    # cloud-hosted Postgres kicks idle connections (typical idle timeout is
    # ~60 minutes on managed providers).
    pool_recycle=1800,
)

async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy models."""

    pass


async def get_db() -> AsyncSession:
    """FastAPI dependency that yields an async database session."""
    async with async_session() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

"""
Async SQLAlchemy engine/session for apps/api. DATABASE_URL in .env is
kept in the plain `postgresql://` form -- driver-agnostic, so a future
synchronous Alembic migration runner can use the same value -- this
module derives the asyncpg-flavored URL SQLAlchemy's async engine
actually needs.
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from infrastructure.config import settings


def _asyncpg_url(database_url: str) -> str:
    if database_url.startswith("postgresql://"):
        return database_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    return database_url


engine = create_async_engine(_asyncpg_url(settings.database_url), pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session


async def check_database_connection() -> bool:
    """Real connectivity check used by GET /health -- True only if a
    query genuinely executes against the real database, never asserted."""
    try:
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            return result.scalar() == 1
    except Exception:
        return False

"""
Database Session Management

WHY ASYNC SESSIONS?
  URL shorteners are I/O-bound (network calls to DB, Redis).
  Async SQLAlchemy with asyncpg lets us handle thousands of concurrent
  requests WITHOUT blocking threads. A single process can serve many
  users simultaneously while waiting for DB responses.

WHY A SESSION FACTORY (not a global session)?
  Each request gets its OWN session via dependency injection:
  - Prevents data leaking between requests
  - Enables proper transaction boundaries (commit/rollback per request)
  - Makes testing trivial — just inject a mock session

  This is critical for correctness in concurrent systems.
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

# Ensure the database URL uses the async driver (asyncpg).
# Cloud providers like Supabase give you "postgresql://..." but
# SQLAlchemy's create_async_engine requires "postgresql+asyncpg://...".
# This normalization prevents the "psycopg2 is not async" error on Vercel.
_db_url = settings.database_url
if _db_url.startswith("postgresql://"):
    _db_url = _db_url.replace("postgresql://", "postgresql+asyncpg://", 1)

# Engine: manages the connection pool to PostgreSQL
# pool_size=20: keep 20 connections ready (avoids connection storm)
# max_overflow=10: allow 10 extra under peak load, then reject
# echo=False in production to avoid logging every SQL query
engine = create_async_engine(
    _db_url,
    pool_size=20,
    max_overflow=10,
    echo=settings.debug,
)

# Session factory: creates new sessions on demand
# expire_on_commit=False: allows reading attributes after commit
# without triggering a new SQL query (performance optimization)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Dependency that provides an async database session per request.

    FastAPI calls this via `Depends(get_db)`. The session is:
    - Created at request start
    - Yielded to the route/service
    - Automatically closed at request end (even on errors)

    This pattern guarantees no leaked connections.
    """
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()

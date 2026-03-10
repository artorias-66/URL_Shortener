"""
API Dependencies — FastAPI Dependency Injection

DEPENDENCY INJECTION IN FASTAPI:
  FastAPI's Depends() system is one of its killer features.
  It's similar to Spring's @Autowired or Angular's DI container.

  HOW IT WORKS:
  1. Route declares: def handler(service: URLService = Depends(get_url_service))
  2. FastAPI sees Depends(get_url_service)
  3. FastAPI calls get_url_service() → resolves its OWN dependencies → returns service
  4. Service is passed to your route handler
  5. After request, cleanup runs (session closes, etc.)

  WHY THIS MATTERS:
  - No global state. Each request gets fresh, isolated dependencies.
  - Swap real → mock for testing by overriding dependencies.
  - Dependencies are composable: URL service depends on DB + Cache,
    DB depends on session factory, etc. FastAPI resolves the whole tree.

  DEPENDENCY TREE:
  get_url_service
  ├── get_db (async DB session)
  └── get_cache_service (Redis client)
"""

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import async_session_factory
from app.services.cache_service import CacheService
from app.services.url_service import URLService

# Singleton cache service instance (shared across requests)
# WHY SINGLETON? Redis connections are pooled internally.
# Creating a new CacheService per request would waste connections.
_cache_service: CacheService | None = None


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide an async database session for a single request.

    LIFECYCLE:
    1. Session created (from connection pool — no network overhead)
    2. Yielded to route handler
    3. On success → commit
    4. On error → rollback
    5. Always → close (return connection to pool)

    This guarantees:
    - No leaked connections
    - Transaction isolation between requests
    - Automatic cleanup even on exceptions
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


def get_cache_service() -> CacheService:
    """
    Provide the shared CacheService instance.

    Returns the singleton that was initialized during app startup.
    If Redis failed to connect, this still returns the instance —
    it just operates in degraded mode (all gets return None).
    """
    global _cache_service
    if _cache_service is None:
        _cache_service = CacheService()
    return _cache_service


async def init_cache_service() -> CacheService:
    """
    Initialize the cache service at application startup.

    Called once in the FastAPI lifespan event.
    """
    global _cache_service
    _cache_service = CacheService()
    await _cache_service.connect()
    return _cache_service


async def shutdown_cache_service() -> None:
    """Close the cache service at application shutdown."""
    global _cache_service
    if _cache_service:
        await _cache_service.disconnect()
        _cache_service = None


async def get_url_service(
    db: AsyncSession = None,
    cache: CacheService = None,
) -> URLService:
    """
    Build a URLService with all its dependencies.

    This is the top-level dependency that route handlers use.
    It composes DB session + cache service into the service layer.

    Note: When used with FastAPI's Depends(), the db and cache
    parameters are resolved automatically. This signature also
    allows direct construction in tests.
    """
    if db is None:
        # This path is used when called outside of FastAPI's DI
        # (e.g., in background tasks)
        async for session in get_db():
            db = session
            break

    if cache is None:
        cache = get_cache_service()

    return URLService(db=db, cache=cache)

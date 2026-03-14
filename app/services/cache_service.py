"""
Cache Service — Redis Integration Layer

CACHING STRATEGY: CACHE-ASIDE (Lazy Loading)

  This is the most common caching pattern in production systems:

  READ PATH:
    1. Application checks Redis first → cache HIT → return immediately
    2. Cache MISS → query PostgreSQL → store result in Redis with TTL → return
    3. Next request for same key → cache HIT (fast path)

  WRITE PATH:
    1. Write to PostgreSQL (source of truth)
    2. Invalidate (delete) the Redis cache entry
    3. Next read will repopulate the cache

  WHY CACHE-ASIDE (not Write-Through or Write-Behind)?
    - Simpler to implement and reason about
    - Only caches data that's actually read (no wasted memory)
    - Works well for read-heavy workloads like URL shorteners
    - Cache failures don't break writes

  TTL (Time-To-Live):
    Every cached entry has a TTL (default: 1 hour). This ensures:
    - Stale data is automatically evicted
    - Redis memory doesn't grow unbounded
    - Consistency eventually converges even without explicit invalidation

  GRACEFUL DEGRADATION:
    If Redis is down, the service falls back to PostgreSQL.
    This is CRITICAL in production — a cache failure should NEVER
    cause the entire service to fail. Cache is an optimization, not
    a requirement.

  INTERVIEW TALKING POINT:
    "What happens if Redis goes down?"
    → "The service continues serving requests from PostgreSQL with
       higher latency. We log cache failures for monitoring and
       the service auto-recovers when Redis comes back."
"""


import redis.asyncio as redis

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class CacheService:
    """
    Async Redis cache wrapper with graceful degradation.

    All methods catch Redis exceptions and return fallback values
    instead of crashing the application.
    """

    def __init__(self, redis_client: redis.Redis | None = None) -> None:
        """
        Initialize the cache service.

        Args:
            redis_client: Injected Redis client (for testing).
                          If None, creates one from settings.
        """
        self._client = redis_client
        self._settings = get_settings()

    async def connect(self) -> None:
        """
        Initialize the Redis connection.

        Called during application startup. Uses connection pooling
        internally (redis-py handles this automatically).
        """
        if self._client is None:
            try:
                self._client = redis.from_url(
                    self._settings.redis_url,
                    decode_responses=True,
                    socket_connect_timeout=5,
                    socket_timeout=5,
                )
                # Verify connection
                await self._client.ping()
                logger.info("Redis connection established")
            except redis.RedisError as e:
                logger.error(
                    f"Failed to connect to Redis: {e}",
                    extra={"extra_data": {"error": str(e)}},
                )
                self._client = None

    async def disconnect(self) -> None:
        """Close the Redis connection during application shutdown."""
        if self._client:
            await self._client.close()
            logger.info("Redis connection closed")

    async def get(self, key: str) -> str | None:
        """
        Retrieve a value from cache.

        Args:
            key: Cache key (typically the short_code).

        Returns:
            Cached value if found, None on miss or error.
        """
        if self._client is None:
            return None

        try:
            value = await self._client.get(key)
            if value is not None:
                logger.debug(
                    f"Cache HIT for key: {key}",
                    extra={"extra_data": {"key": key}},
                )
            else:
                logger.debug(
                    f"Cache MISS for key: {key}",
                    extra={"extra_data": {"key": key}},
                )
            return value
        except redis.RedisError as e:
            logger.error(
                f"Redis GET failed: {e}",
                extra={"extra_data": {"key": key, "error": str(e)}},
            )
            return None

    async def set(
        self, key: str, value: str, ttl: int | None = None
    ) -> bool:
        """
        Store a value in cache with optional TTL.

        Args:
            key: Cache key.
            value: Value to store.
            ttl: Time-to-live in seconds. None uses default from config.

        Returns:
            True if stored successfully, False on error.
        """
        if self._client is None:
            return False

        if ttl is None:
            ttl = self._settings.cache_ttl_seconds

        try:
            await self._client.set(key, value, ex=ttl)
            logger.debug(
                f"Cache SET for key: {key} (TTL={ttl}s)",
                extra={"extra_data": {"key": key, "ttl": ttl}},
            )
            return True
        except redis.RedisError as e:
            logger.error(
                f"Redis SET failed: {e}",
                extra={"extra_data": {"key": key, "error": str(e)}},
            )
            return False

    async def delete(self, key: str) -> bool:
        """
        Delete a value from cache (cache invalidation).

        Called when a URL is updated or deactivated to prevent
        serving stale data.

        Args:
            key: Cache key to delete.

        Returns:
            True if deleted, False on error.
        """
        if self._client is None:
            return False

        try:
            await self._client.delete(key)
            logger.debug(
                f"Cache DELETE for key: {key}",
                extra={"extra_data": {"key": key}},
            )
            return True
        except redis.RedisError as e:
            logger.error(
                f"Redis DELETE failed: {e}",
                extra={"extra_data": {"key": key, "error": str(e)}},
            )
            return False

    async def increment(self, key: str) -> int | None:
        """
        Atomically increment a counter in Redis.

        Used for rate limiting — Redis INCR is atomic, meaning
        even with concurrent requests, the count is always accurate.
        No race conditions, no locks needed.

        Args:
            key: Counter key.

        Returns:
            New counter value, or None on error.
        """
        if self._client is None:
            return None

        try:
            return await self._client.incr(key)
        except redis.RedisError as e:
            logger.error(
                f"Redis INCR failed: {e}",
                extra={"extra_data": {"key": key, "error": str(e)}},
            )
            return None

    async def expire(self, key: str, ttl: int) -> bool:
        """
        Set a TTL on an existing key.

        Used with increment() for rate limiting windows:
        1. INCR the counter (atomic)
        2. If counter == 1, set EXPIRE (first request in window)
        3. Redis auto-deletes the key after TTL → window resets

        Args:
            key: Key to set expiration on.
            ttl: Time-to-live in seconds.

        Returns:
            True if TTL was set, False on error.
        """
        if self._client is None:
            return False

        try:
            await self._client.expire(key, ttl)
            return True
        except redis.RedisError as e:
            logger.error(
                f"Redis EXPIRE failed: {e}",
                extra={"extra_data": {"key": key, "error": str(e)}},
            )
            return False

    @property
    def is_connected(self) -> bool:
        """Check if Redis client is available."""
        return self._client is not None

"""
Cache Service Tests — 11 Tests

Tests Redis cache operations, TTL behavior, graceful degradation,
and the cache-aside pattern.

TESTING STRATEGY:
  We mock the Redis client entirely. These tests verify:
  1. The CacheService correctly delegates to Redis methods
  2. Error handling works (graceful degradation on Redis failure)
  3. TTL logic is correct
  4. The service operates safely when Redis is disconnected
"""

import pytest
from unittest.mock import AsyncMock

import redis.asyncio as real_redis

from app.services.cache_service import CacheService


@pytest.fixture
def mock_redis() -> AsyncMock:
    """Create a mock Redis client."""
    client = AsyncMock()
    client.get = AsyncMock(return_value=None)
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock(return_value=1)
    client.incr = AsyncMock(return_value=1)
    client.expire = AsyncMock(return_value=True)
    client.ping = AsyncMock(return_value=True)
    client.close = AsyncMock()
    return client


@pytest.fixture
def cache_service(mock_redis: AsyncMock) -> CacheService:
    """Create a CacheService with mocked Redis client."""
    service = CacheService(redis_client=mock_redis)
    return service


@pytest.fixture
def disconnected_cache() -> CacheService:
    """Create a CacheService without a Redis client (simulating disconnection)."""
    return CacheService(redis_client=None)


class TestCacheGet:
    """Tests for cache GET operations."""

    @pytest.mark.asyncio
    async def test_cache_hit(self, cache_service: CacheService, mock_redis: AsyncMock) -> None:
        """Should return the cached value on a cache hit."""
        mock_redis.get.return_value = "https://www.example.com"

        result = await cache_service.get("url:aBcDeFg")

        assert result == "https://www.example.com"
        mock_redis.get.assert_called_once_with("url:aBcDeFg")

    @pytest.mark.asyncio
    async def test_cache_miss(self, cache_service: CacheService, mock_redis: AsyncMock) -> None:
        """Should return None on a cache miss."""
        mock_redis.get.return_value = None

        result = await cache_service.get("url:nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_cache_get_redis_error(self, cache_service: CacheService, mock_redis: AsyncMock) -> None:
        """Should return None (not crash) when Redis throws an error."""
        mock_redis.get.side_effect = real_redis.RedisError("Connection refused")

        result = await cache_service.get("url:aBcDeFg")

        assert result is None  # Graceful degradation

    @pytest.mark.asyncio
    async def test_cache_get_when_disconnected(self, disconnected_cache: CacheService) -> None:
        """Should return None when Redis is not connected."""
        result = await disconnected_cache.get("url:aBcDeFg")

        assert result is None


class TestCacheSet:
    """Tests for cache SET operations."""

    @pytest.mark.asyncio
    async def test_cache_set_with_default_ttl(self, cache_service: CacheService, mock_redis: AsyncMock) -> None:
        """Should store value with default TTL from config."""
        result = await cache_service.set("url:aBcDeFg", "https://example.com")

        assert result is True
        mock_redis.set.assert_called_once()
        # Verify TTL was passed
        call_kwargs = mock_redis.set.call_args
        assert call_kwargs[1]["ex"] > 0

    @pytest.mark.asyncio
    async def test_cache_set_with_custom_ttl(self, cache_service: CacheService, mock_redis: AsyncMock) -> None:
        """Should store value with custom TTL."""
        result = await cache_service.set("url:aBcDeFg", "https://example.com", ttl=300)

        assert result is True
        mock_redis.set.assert_called_once_with("url:aBcDeFg", "https://example.com", ex=300)

    @pytest.mark.asyncio
    async def test_cache_set_redis_error(self, cache_service: CacheService, mock_redis: AsyncMock) -> None:
        """Should return False (not crash) when Redis SET fails."""
        mock_redis.set.side_effect = real_redis.RedisError("Write error")

        result = await cache_service.set("url:aBcDeFg", "https://example.com")

        assert result is False

    @pytest.mark.asyncio
    async def test_cache_set_when_disconnected(self, disconnected_cache: CacheService) -> None:
        """Should return False when Redis is not connected."""
        result = await disconnected_cache.set("url:aBcDeFg", "https://example.com")

        assert result is False


class TestCacheDelete:
    """Tests for cache DELETE operations (cache invalidation)."""

    @pytest.mark.asyncio
    async def test_cache_delete_success(self, cache_service: CacheService, mock_redis: AsyncMock) -> None:
        """Should delete a cached key."""
        result = await cache_service.delete("url:aBcDeFg")

        assert result is True
        mock_redis.delete.assert_called_once_with("url:aBcDeFg")

    @pytest.mark.asyncio
    async def test_cache_delete_when_disconnected(self, disconnected_cache: CacheService) -> None:
        """Should return False when Redis is not connected."""
        result = await disconnected_cache.delete("url:aBcDeFg")

        assert result is False


class TestCacheIncrement:
    """Tests for atomic increment operations (used by rate limiter)."""

    @pytest.mark.asyncio
    async def test_increment_success(self, cache_service: CacheService, mock_redis: AsyncMock) -> None:
        """Should atomically increment and return new value."""
        mock_redis.incr.return_value = 5

        result = await cache_service.increment("rate_limit:192.168.1.1")

        assert result == 5
        mock_redis.incr.assert_called_once_with("rate_limit:192.168.1.1")

    @pytest.mark.asyncio
    async def test_increment_when_disconnected(self, disconnected_cache: CacheService) -> None:
        """Should return None when Redis is not connected."""
        result = await disconnected_cache.increment("rate_limit:192.168.1.1")

        assert result is None


class TestCacheConnection:
    """Tests for connection lifecycle."""

    def test_is_connected_with_client(self, cache_service: CacheService) -> None:
        """Should report connected when client exists."""
        assert cache_service.is_connected is True

    def test_is_disconnected_without_client(self, disconnected_cache: CacheService) -> None:
        """Should report disconnected when client is None."""
        assert disconnected_cache.is_connected is False

    @pytest.mark.asyncio
    async def test_disconnect(self, cache_service: CacheService, mock_redis: AsyncMock) -> None:
        """Should close the Redis connection on disconnect."""
        await cache_service.disconnect()

        mock_redis.close.assert_called_once()

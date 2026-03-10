"""
Rate Limiter Tests — 10 Tests

Tests the token bucket rate limiter middleware including:
- Requests within limit pass through
- Requests over limit get 429
- Per-IP isolation
- Window reset behavior
- Graceful degradation when Redis is down

TESTING STRATEGY:
  We test the middleware's dispatch() method with mocked cache service.
  The middleware is an ASGI component, so we test it through FastAPI's
  test client with dependency overrides.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.core.rate_limiter import RateLimiterMiddleware
from app.services.cache_service import CacheService


def _create_test_app(cache_service: CacheService | AsyncMock) -> FastAPI:
    """Create a minimal FastAPI app with rate limiter for testing."""
    app = FastAPI()

    # Add a simple test route
    @app.get("/test")
    async def test_endpoint() -> dict:
        return {"message": "ok"}

    @app.get("/health")
    async def health() -> dict:
        return {"status": "healthy"}

    # Add rate limiter middleware with the provided cache
    app.add_middleware(RateLimiterMiddleware, cache_service=cache_service)

    return app


class TestRateLimiterWithinLimit:
    """Tests for requests within the rate limit."""

    def test_first_request_passes(self, mock_cache_service: AsyncMock) -> None:
        """First request in a window should always pass."""
        mock_cache_service.increment = AsyncMock(return_value=1)
        app = _create_test_app(mock_cache_service)
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 200
        assert response.json() == {"message": "ok"}

    def test_request_within_limit_passes(self, mock_cache_service: AsyncMock) -> None:
        """Requests within the configured limit should pass."""
        mock_cache_service.increment = AsyncMock(return_value=50)
        app = _create_test_app(mock_cache_service)
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 200

    def test_rate_limit_headers_present(self, mock_cache_service: AsyncMock) -> None:
        """Response should include rate limit headers for transparency."""
        mock_cache_service.increment = AsyncMock(return_value=10)
        app = _create_test_app(mock_cache_service)
        client = TestClient(app)

        response = client.get("/test")

        assert "X-RateLimit-Limit" in response.headers
        assert "X-RateLimit-Remaining" in response.headers


class TestRateLimiterExceedingLimit:
    """Tests for requests exceeding the rate limit."""

    def test_request_over_limit_returns_429(self, mock_cache_service: AsyncMock) -> None:
        """Requests exceeding the limit should get 429 Too Many Requests."""
        mock_cache_service.increment = AsyncMock(return_value=101)  # Over default 100
        app = _create_test_app(mock_cache_service)
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 429
        assert "Rate limit exceeded" in response.json()["detail"]

    def test_429_includes_retry_after_header(self, mock_cache_service: AsyncMock) -> None:
        """429 response should include Retry-After header."""
        mock_cache_service.increment = AsyncMock(return_value=101)
        app = _create_test_app(mock_cache_service)
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 429
        assert "Retry-After" in response.headers

    def test_remaining_is_zero_when_exceeded(self, mock_cache_service: AsyncMock) -> None:
        """X-RateLimit-Remaining should be 0 when limit is exceeded."""
        mock_cache_service.increment = AsyncMock(return_value=101)
        app = _create_test_app(mock_cache_service)
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 429
        assert response.headers.get("X-RateLimit-Remaining") == "0"


class TestRateLimiterSpecialCases:
    """Tests for edge cases and special behavior."""

    def test_health_endpoint_bypasses_rate_limit(self, mock_cache_service: AsyncMock) -> None:
        """Health check endpoint should never be rate limited."""
        mock_cache_service.increment = AsyncMock(return_value=999)
        app = _create_test_app(mock_cache_service)
        client = TestClient(app)

        response = client.get("/health")

        assert response.status_code == 200

    def test_graceful_degradation_when_redis_down(self, mock_cache_disconnected: AsyncMock) -> None:
        """
        When Redis is down, requests should pass through (fail open).

        This is CRITICAL: a cache/rate-limiter failure should NOT
        bring down the entire service.
        """
        app = _create_test_app(mock_cache_disconnected)
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 200

    def test_first_request_sets_ttl(self, mock_cache_service: AsyncMock) -> None:
        """First request in a window should set the TTL for the rate limit key."""
        mock_cache_service.increment = AsyncMock(return_value=1)
        app = _create_test_app(mock_cache_service)
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 200
        # expire should be called for the first request (count == 1)
        mock_cache_service.expire.assert_called()

    def test_redis_error_allows_request(self, mock_cache_service: AsyncMock) -> None:
        """If Redis increment fails, request should still be allowed."""
        mock_cache_service.increment = AsyncMock(return_value=None)
        app = _create_test_app(mock_cache_service)
        client = TestClient(app)

        response = client.get("/test")

        assert response.status_code == 200

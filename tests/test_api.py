"""
API Endpoint Tests — 12 Tests

Tests all HTTP endpoints with mocked service layer:
- POST /shorten (valid, invalid, with expiry)
- GET /{code} (redirect, not found, expired)
- GET /{code}/stats (analytics)
- GET /health (health check)

TESTING STRATEGY:
  We use FastAPI's dependency override to inject a mock URLService.
  This tests the HTTP layer in isolation:
  - Request parsing and validation
  - Response formatting and status codes
  - Error handling via exception handlers
  - Route matching order

  The service layer is fully mocked — no DB or Redis needed.
"""

from datetime import datetime, timezone

from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.responses import JSONResponse

from app.api.routes import router, _build_url_service
from app.exceptions import (
    URLExpiredException,
    URLNotFoundException,
    URLShortenerException,
)
from app.schemas.url_schema import URLResponse, URLStatsResponse


def _create_test_app(mock_service: AsyncMock) -> TestClient:
    """
    Create a test client with mocked URL service.

    Uses FastAPI's dependency_overrides to replace the real
    service with a mock. This is THE way to test FastAPI routes.
    """
    app = FastAPI()

    # Register the exception handler (same as main.py)
    @app.exception_handler(URLShortenerException)
    async def handler(request, exc):
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "status_code": exc.status_code},
        )

    app.include_router(router)

    # Override the dependency to inject our mock
    app.dependency_overrides[_build_url_service] = lambda: mock_service

    return TestClient(app)


class TestShortenEndpoint:
    """Tests for POST /shorten."""

    def test_shorten_valid_url(self, mock_url_service: AsyncMock) -> None:
        """Should return 201 with short URL for valid input."""
        mock_url_service.create_short_url = AsyncMock(
            return_value=URLResponse(
                short_code="aBcDeFg",
                short_url="http://localhost:8000/aBcDeFg",
                original_url="https://www.example.com/test",
                created_at=datetime.now(timezone.utc),
                expires_at=None,
            )
        )
        client = _create_test_app(mock_url_service)

        response = client.post(
            "/shorten",
            json={"url": "https://www.example.com/test"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["short_code"] == "aBcDeFg"
        assert data["short_url"] == "http://localhost:8000/aBcDeFg"
        assert data["original_url"] == "https://www.example.com/test"

    def test_shorten_with_expiry(self, mock_url_service: AsyncMock) -> None:
        """Should accept optional expires_in_minutes parameter."""
        now = datetime.now(timezone.utc)
        mock_url_service.create_short_url = AsyncMock(
            return_value=URLResponse(
                short_code="ExPiRy1",
                short_url="http://localhost:8000/ExPiRy1",
                original_url="https://www.example.com/expiring",
                created_at=now,
                expires_at=now,
            )
        )
        client = _create_test_app(mock_url_service)

        response = client.post(
            "/shorten",
            json={"url": "https://www.example.com/expiring", "expires_in_minutes": 60},
        )

        assert response.status_code == 201
        assert response.json()["expires_at"] is not None

    def test_shorten_invalid_url_format(self, mock_url_service: AsyncMock) -> None:
        """Should return 422 for invalid URL format (Pydantic validation)."""
        client = _create_test_app(mock_url_service)

        response = client.post(
            "/shorten",
            json={"url": "not-a-valid-url"},
        )

        assert response.status_code == 422  # Pydantic validation error

    def test_shorten_missing_url_field(self, mock_url_service: AsyncMock) -> None:
        """Should return 422 when url field is missing."""
        client = _create_test_app(mock_url_service)

        response = client.post("/shorten", json={})

        assert response.status_code == 422

    def test_shorten_invalid_expiry_too_low(self, mock_url_service: AsyncMock) -> None:
        """Should reject expiry less than 1 minute."""
        client = _create_test_app(mock_url_service)

        response = client.post(
            "/shorten",
            json={"url": "https://example.com", "expires_in_minutes": 0},
        )

        assert response.status_code == 422


class TestRedirectEndpoint:
    """Tests for GET /{short_code}."""

    def test_redirect_valid_code(self, mock_url_service: AsyncMock) -> None:
        """Should return 302 redirect for a valid short code."""
        mock_url_service.resolve_short_code = AsyncMock(
            return_value="https://www.example.com/original"
        )
        client = _create_test_app(mock_url_service)

        response = client.get("/aBcDeFg", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"] == "https://www.example.com/original"

    def test_redirect_not_found(self, mock_url_service: AsyncMock) -> None:
        """Should return 404 when short code doesn't exist."""
        mock_url_service.resolve_short_code = AsyncMock(
            side_effect=URLNotFoundException("nonexist")
        )
        client = _create_test_app(mock_url_service)

        response = client.get("/nonexist")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"]

    def test_redirect_expired_returns_410(self, mock_url_service: AsyncMock) -> None:
        """
        Should return 410 Gone for expired URLs.

        410 is semantically correct: "this resource EXISTED but is gone."
        This tells browsers/crawlers to stop retrying.
        """
        mock_url_service.resolve_short_code = AsyncMock(
            side_effect=URLExpiredException("ExpIrEd")
        )
        client = _create_test_app(mock_url_service)

        response = client.get("/ExpIrEd")

        assert response.status_code == 410
        assert "expired" in response.json()["detail"]


class TestStatsEndpoint:
    """Tests for GET /{short_code}/stats."""

    def test_stats_valid_code(self, mock_url_service: AsyncMock) -> None:
        """Should return analytics data for a valid short code."""
        now = datetime.now(timezone.utc)
        mock_url_service.get_url_stats = AsyncMock(
            return_value=URLStatsResponse(
                short_code="aBcDeFg",
                original_url="https://www.example.com",
                click_count=42,
                created_at=now,
                expires_at=None,
                last_accessed_at=now,
            )
        )
        client = _create_test_app(mock_url_service)

        response = client.get("/aBcDeFg/stats")

        assert response.status_code == 200
        data = response.json()
        assert data["click_count"] == 42
        assert data["short_code"] == "aBcDeFg"

    def test_stats_not_found(self, mock_url_service: AsyncMock) -> None:
        """Should return 404 when short code doesn't exist."""
        mock_url_service.get_url_stats = AsyncMock(
            side_effect=URLNotFoundException("nonexist")
        )
        client = _create_test_app(mock_url_service)

        response = client.get("/nonexist/stats")

        assert response.status_code == 404


class TestHealthEndpoint:
    """Tests for GET /health."""

    def test_health_check(self, mock_url_service: AsyncMock) -> None:
        """Health endpoint should return 200 with service status."""
        client = _create_test_app(mock_url_service)

        response = client.get("/health")

        assert response.status_code == 200
        assert response.json()["status"] == "healthy"

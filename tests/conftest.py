"""
Shared Test Fixtures — conftest.py

WHY CONFTEST.PY?
  pytest automatically discovers conftest.py and makes its fixtures
  available to ALL test files in the same directory. This means:
  - No imports needed — fixtures are injected by name
  - Shared setup logic lives in ONE place
  - Each test file stays focused on what it's testing

TESTING PHILOSOPHY:
  We use UNIT tests with MOCKS, not integration tests.

  WHY MOCKS (not a real database)?
  - Speed: mocked tests run in milliseconds, DB tests take seconds
  - Isolation: no external dependencies needed (no Docker in CI)
  - Determinism: no flaky tests from network/DB issues
  - Parallelism: tests can run concurrently without DB conflicts

  Integration tests (with real DB/Redis) belong in a separate test suite
  that runs against the Docker Compose stack.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from httpx import ASGITransport, AsyncClient

from app.exceptions import URLExpiredException, URLNotFoundException
from app.models.url import URL
from app.schemas.url_schema import URLCreateRequest


# ─── URL Model Fixtures ─────────────────────────────────────────────


@pytest.fixture
def sample_url() -> URL:
    """Create a sample URL record for testing."""
    return URL(
        id=1,
        original_url="https://www.example.com/very/long/path",
        short_code="aBcDeFg",
        click_count=0,
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        last_accessed_at=None,
        is_active=True,
    )


@pytest.fixture
def expired_url() -> URL:
    """Create an expired URL record for testing."""
    return URL(
        id=2,
        original_url="https://www.expired-example.com",
        short_code="ExpIrEd",
        click_count=5,
        created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        expires_at=datetime.now(timezone.utc) - timedelta(hours=1),
        last_accessed_at=None,
        is_active=True,
    )


@pytest.fixture
def inactive_url() -> URL:
    """Create a soft-deleted (inactive) URL record for testing."""
    return URL(
        id=3,
        original_url="https://www.deleted-example.com",
        short_code="DeLeTed",
        click_count=10,
        created_at=datetime.now(timezone.utc),
        expires_at=None,
        last_accessed_at=None,
        is_active=False,
    )


@pytest.fixture
def url_with_expiry() -> URL:
    """Create a URL that expires in the future."""
    return URL(
        id=4,
        original_url="https://www.future-expiry.com",
        short_code="FuTuReX",
        click_count=3,
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        last_accessed_at=None,
        is_active=True,
    )


# ─── Request Fixtures ───────────────────────────────────────────────


@pytest.fixture
def valid_url_request() -> URLCreateRequest:
    """Create a valid URL creation request."""
    return URLCreateRequest(url="https://www.example.com/test")


@pytest.fixture
def url_request_with_expiry() -> URLCreateRequest:
    """Create a URL creation request with expiration."""
    return URLCreateRequest(
        url="https://www.example.com/expiring",
        expires_in_minutes=60,
    )


# ─── Mock Fixtures ──────────────────────────────────────────────────


@pytest.fixture
def mock_db_session() -> AsyncMock:
    """
    Create a mock async database session.

    Mocks all SQLAlchemy session methods:
    - execute() returns a mock result
    - add() is a no-op
    - flush() is a no-op
    - commit() is a no-op
    """
    session = AsyncMock()
    session.execute = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.close = AsyncMock()
    return session


@pytest.fixture
def mock_cache_service() -> AsyncMock:
    """
    Create a mock cache service.

    All methods return sensible defaults:
    - get() returns None (cache miss)
    - set() returns True
    - delete() returns True
    - is_connected returns True
    """
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock(return_value=True)
    cache.delete = AsyncMock(return_value=True)
    cache.increment = AsyncMock(return_value=1)
    cache.expire = AsyncMock(return_value=True)
    cache.is_connected = True
    return cache


@pytest.fixture
def mock_cache_disconnected() -> AsyncMock:
    """Create a mock cache service that simulates Redis being down."""
    cache = AsyncMock()
    cache.get = AsyncMock(return_value=None)
    cache.set = AsyncMock(return_value=False)
    cache.delete = AsyncMock(return_value=False)
    cache.increment = AsyncMock(return_value=None)
    cache.expire = AsyncMock(return_value=False)
    cache.is_connected = False
    return cache


# ─── Test Client Fixtures ───────────────────────────────────────────


@pytest.fixture
def mock_url_service() -> AsyncMock:
    """Create a mock URL service for API tests."""
    service = AsyncMock()
    return service

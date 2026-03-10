"""
URL Service — Core Business Logic Orchestrator

SERVICE LAYER ARCHITECTURE:
  This is the heart of the application. All business logic lives HERE,
  not in route handlers. This follows the "Clean Architecture" principle:

  Routes (thin) → Service (business logic) → Repository (data access)

  WHY THIS MATTERS:
  1. Routes only handle HTTP concerns (request parsing, response formatting)
  2. Services contain ALL business rules (reusable across APIs, CLI, workers)
  3. Services are testable without HTTP framework overhead
  4. Changing the web framework (FastAPI → Django) only affects route layer

  DATA FLOW FOR REDIRECT (the hot path):
  ┌──────────┐    ┌───────┐    ┌───────────┐    ┌────────────┐
  │  Client  │───►│ Route │───►│  Service   │───►│   Redis    │
  │ GET /abc │    │       │    │            │    │ (cache hit) │
  └──────────┘    └───────┘    │            │    └────────────┘
                               │ cache miss │───►┌────────────┐
                               │            │    │ PostgreSQL │
                               │ cache set  │◄───│  (DB hit)  │
                               └────────────┘    └────────────┘

  In production, ~95% of requests hit Redis (sub-millisecond).
  Only ~5% fall through to PostgreSQL (~1-5ms).
"""

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.exceptions import URLExpiredException, URLNotFoundException
from app.models.url import URL
from app.schemas.url_schema import URLCreateRequest, URLResponse, URLStatsResponse
from app.services.cache_service import CacheService
from app.services.slug_generator import generate_short_code

logger = get_logger(__name__)


class URLService:
    """
    Service layer for URL shortening operations.

    Orchestrates interactions between:
    - Database (PostgreSQL via SQLAlchemy)
    - Cache (Redis via CacheService)
    - Slug generation

    Each method represents a single business operation
    with clear input/output contracts.
    """

    def __init__(self, db: AsyncSession, cache: CacheService) -> None:
        """
        Initialize with injected dependencies.

        WHY DEPENDENCY INJECTION?
          - In production: real DB session + real Redis client
          - In testing: mock session + mock Redis client
          - Same code, different behavior. No if/else for test mode.
          - This is the "D" in SOLID (Dependency Inversion Principle).

        Args:
            db: Async database session.
            cache: Cache service instance.
        """
        self._db = db
        self._cache = cache
        self._settings = get_settings()

    async def create_short_url(
        self, request: URLCreateRequest, base_url: str | None = None
    ) -> URLResponse:
        """
        Create a new shortened URL.

        FLOW:
        1. Generate a unique Base62 slug
        2. Calculate expiration if requested
        3. Persist to PostgreSQL
        4. Return formatted response

        WHY WE DON'T CACHE ON CREATE:
          Cache-aside pattern only caches on READ. Caching on write
          would fill Redis with URLs that might never be accessed.
          Memory is expensive — only cache what's actually hot.

        Args:
            request: Validated URL creation request.
            base_url: The base URL derived from the incoming HTTP request.
                      Falls back to settings.base_url if not provided.

        Returns:
            URLResponse with short URL and metadata.
        """
        # Generate unique short code with collision detection
        short_code = await self._generate_unique_code()

        # Calculate expiration time if specified
        expires_at = None
        if request.expires_in_minutes:
            expires_at = datetime.now(timezone.utc) + timedelta(
                minutes=request.expires_in_minutes
            )

        # Create database record
        url_record = URL(
            original_url=str(request.url),
            short_code=short_code,
            expires_at=expires_at,
        )

        self._db.add(url_record)
        await self._db.flush()  # Flush to get the ID without committing

        logger.info(
            "URL created",
            extra={
                "extra_data": {
                    "short_code": short_code,
                    "expires_at": expires_at.isoformat() if expires_at else None,
                }
            },
        )

        # Use the request's base URL (works on any host: localhost, Vercel, etc.)
        effective_base = base_url or self._settings.base_url

        return URLResponse(
            short_code=short_code,
            short_url=f"{effective_base}/{short_code}",
            original_url=str(request.url),
            created_at=url_record.created_at,
            expires_at=url_record.expires_at,
        )

    async def resolve_short_code(self, short_code: str) -> str:
        """
        Resolve a short code to its original URL.

        THIS IS THE HOT PATH — optimized for speed:

        1. Check Redis cache (sub-ms) → return on HIT
        2. Cache MISS → Query PostgreSQL (~1-5ms)
        3. Validate: exists? expired? active?
        4. Cache the result in Redis for future requests
        5. Increment click count (async, non-blocking)
        6. Return original URL for 302 redirect

        WHY CACHE THE RESULT ONLY ON MISS?
          Write-through caching would cache every URL, even cold ones.
          Cache-aside only caches URLs that are actually accessed,
          which naturally implements an LRU-like eviction pattern.

        Args:
            short_code: The Base62 short code to look up.

        Returns:
            The original URL string.

        Raises:
            URLNotFoundException: If short code doesn't exist.
            URLExpiredException: If the URL has expired (410 Gone).
        """
        # Step 1: Check Redis cache first
        cached_url = await self._cache.get(f"url:{short_code}")
        if cached_url:
            # Still need to increment click count in DB
            await self._increment_click_count(short_code)
            return cached_url

        # Step 2: Cache miss — query PostgreSQL
        url_record = await self._get_url_by_code(short_code)

        if url_record is None:
            raise URLNotFoundException(short_code)

        # Step 3: Check if URL is active and not expired
        if not url_record.is_active:
            raise URLNotFoundException(short_code)

        if url_record.is_expired:
            # Invalidate cache if it was somehow cached
            await self._cache.delete(f"url:{short_code}")
            raise URLExpiredException(short_code)

        # Step 4: Cache the result for future requests
        # TTL is min(cache_ttl, time_until_expiry) to prevent serving expired URLs
        ttl = self._calculate_ttl(url_record)
        await self._cache.set(f"url:{short_code}", url_record.original_url, ttl)

        # Step 5: Increment click count and update last_accessed
        await self._increment_click_count(short_code)

        logger.info(
            "URL resolved (cache miss)",
            extra={"extra_data": {"short_code": short_code}},
        )

        return url_record.original_url

    async def get_url_stats(self, short_code: str) -> URLStatsResponse:
        """
        Get analytics for a shortened URL.

        This always reads from PostgreSQL (not cache) because
        analytics data must be accurate, not eventually consistent.

        Args:
            short_code: The short code to get stats for.

        Returns:
            URLStatsResponse with click count and timestamps.

        Raises:
            URLNotFoundException: If short code doesn't exist.
        """
        url_record = await self._get_url_by_code(short_code)

        if url_record is None:
            raise URLNotFoundException(short_code)

        return URLStatsResponse(
            short_code=url_record.short_code,
            original_url=url_record.original_url,
            click_count=url_record.click_count,
            created_at=url_record.created_at,
            expires_at=url_record.expires_at,
            last_accessed_at=url_record.last_accessed_at,
        )

    # ─── Private Helper Methods ──────────────────────────────────────

    async def _generate_unique_code(self) -> str:
        """
        Generate a short code guaranteed to be unique in the database.

        Uses the database unique constraint as the ultimate source of truth.
        The retry loop handles the rare case of collision.
        """
        max_retries = 5

        for attempt in range(max_retries):
            code = generate_short_code(self._settings.short_code_length)

            # Check if code already exists
            existing = await self._get_url_by_code(code)
            if existing is None:
                return code

            logger.warning(
                f"Short code collision on attempt {attempt + 1}",
                extra={"extra_data": {"code": code, "attempt": attempt + 1}},
            )

        # If we exhaust retries, raise (extremely unlikely with 62^7 keyspace)
        from app.exceptions import SlugCollisionException
        raise SlugCollisionException()

    async def _get_url_by_code(self, short_code: str) -> URL | None:
        """
        Query the database for a URL by its short code.

        Uses the ix_urls_short_code index for O(log n) lookup.
        Without this index, every redirect would scan the entire table.

        Args:
            short_code: The short code to look up.

        Returns:
            URL record or None if not found.
        """
        stmt = select(URL).where(URL.short_code == short_code)
        result = await self._db.execute(stmt)
        return result.scalar_one_or_none()

    async def _increment_click_count(self, short_code: str) -> None:
        """
        Increment the click count and update last_accessed timestamp.

        WHY NOT USE Redis INCR FOR CLICK COUNT?
          Redis INCR is faster, but click counts need to be durable.
          If Redis crashes, we lose analytics. For production, you'd
          buffer clicks in Redis and flush to DB periodically (write-behind).
          For this project, we write directly to DB for simplicity & correctness.

        Args:
            short_code: The short code whose counter to increment.
        """
        stmt = select(URL).where(URL.short_code == short_code)
        result = await self._db.execute(stmt)
        url_record = result.scalar_one_or_none()

        if url_record:
            url_record.click_count += 1
            url_record.last_accessed_at = datetime.now(timezone.utc)
            await self._db.flush()

    def _calculate_ttl(self, url_record: URL) -> int:
        """
        Calculate the appropriate cache TTL.

        WHY DYNAMIC TTL?
          If a URL expires in 5 minutes but our default TTL is 1 hour,
          we'd serve the URL from cache for 55 minutes AFTER expiry.
          Dynamic TTL = min(default_ttl, time_until_expiry) prevents this.

        Args:
            url_record: The URL record from the database.

        Returns:
            TTL in seconds.
        """
        default_ttl = self._settings.cache_ttl_seconds

        if url_record.expires_at is None:
            return default_ttl

        time_until_expiry = (
            url_record.expires_at - datetime.now(timezone.utc)
        ).total_seconds()

        # Ensure TTL is at least 1 second
        return max(1, int(min(default_ttl, time_until_expiry)))

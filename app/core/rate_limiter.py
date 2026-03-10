"""
Rate Limiter — Token Bucket Algorithm via Redis

ALGORITHM: TOKEN BUCKET (Sliding Window Counter variant)

  The Token Bucket is one of the most common rate limiting algorithms
  used by companies like Stripe, GitHub, and AWS.

  HOW IT WORKS:
  ┌─────────────────────────────────────────────────────┐
  │  Bucket per IP: "rate_limit:192.168.1.1"            │
  │                                                     │
  │  Request arrives → INCR counter in Redis            │
  │  Counter == 1? → First request in window → SET TTL  │
  │  Counter <= limit? → ALLOW request                  │
  │  Counter > limit? → REJECT with 429                 │
  │  TTL expires → Counter auto-deleted → Window resets │
  └─────────────────────────────────────────────────────┘

  WHY REDIS FOR RATE LIMITING?
  - Atomic INCR: no race conditions even with 1000 concurrent requests
  - Built-in TTL: Redis auto-resets the window, no cron jobs needed
  - Shared state: works across multiple API server instances
    (if you scale to 10 servers, all share the same Redis counter)

  WHY NOT IN-MEMORY RATE LIMITING?
  - Per-process: each server has its own counter → user can bypass
    by hitting different instances
  - Lost on restart: counters reset when server reboots
  - Not horizontally scalable

  ALTERNATIVE ALGORITHMS:
  - Fixed Window: simpler, but allows 2x burst at window boundaries
  - Sliding Log: most accurate, but stores every request timestamp (expensive)
  - Leaky Bucket: smooth output rate, used in network traffic shaping

  INTERVIEW TALKING POINT:
  "I chose Token Bucket via Redis because it's horizontally scalable
   (shared state), atomic (no race conditions), and self-cleaning
   (TTL-based window reset)."
"""

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


class RateLimiterMiddleware(BaseHTTPMiddleware):
    """
    HTTP middleware that enforces per-IP rate limiting.

    WHY MIDDLEWARE (not a dependency)?
      Middleware runs BEFORE route matching. This means:
      - Rate limited requests never hit your business logic
      - Protects ALL endpoints uniformly (no need to add Depends() everywhere)
      - Reduces load on DB and cache for abusive clients

      Dependencies run AFTER route matching — middleware is the right layer.
    """

    def __init__(self, app, cache_service=None) -> None:  # noqa: ANN001
        """
        Initialize the rate limiter.

        Args:
            app: The ASGI application.
            cache_service: Injected CacheService for Redis operations.
        """
        super().__init__(app)
        self._cache = cache_service
        self._settings = get_settings()

    def set_cache_service(self, cache_service) -> None:  # noqa: ANN001
        """Set the cache service after initialization (for startup ordering)."""
        self._cache = cache_service

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        """
        Process each request through the rate limiter.

        FLOW:
        1. Extract client IP
        2. Build Redis key: "rate_limit:{ip}"
        3. INCR the counter (atomic)
        4. If first request, SET TTL for the window
        5. If over limit, return 429 Too Many Requests
        6. Otherwise, pass to next middleware/route

        Args:
            request: The incoming HTTP request.
            call_next: The next handler in the middleware chain.

        Returns:
            Response from the next handler, or 429 error.
        """
        # Skip rate limiting if Redis is not available (graceful degradation)
        if self._cache is None or not self._cache.is_connected:
            return await call_next(request)

        # Skip rate limiting for health checks
        if request.url.path == "/health":
            return await call_next(request)

        # Extract client IP
        # In production behind a load balancer, use X-Forwarded-For header
        client_ip = self._get_client_ip(request)
        rate_limit_key = f"rate_limit:{client_ip}"

        try:
            # Atomic increment — even 1000 concurrent requests get unique counts
            request_count = await self._cache.increment(rate_limit_key)

            if request_count is None:
                # Redis error — allow request (fail open)
                return await call_next(request)

            # First request in this window — set the TTL
            if request_count == 1:
                await self._cache.expire(
                    rate_limit_key,
                    self._settings.rate_limit_window_seconds,
                )

            # Check if over limit
            if request_count > self._settings.rate_limit_requests:
                logger.warning(
                    "Rate limit exceeded",
                    extra={
                        "extra_data": {
                            "client_ip": client_ip,
                            "request_count": request_count,
                            "limit": self._settings.rate_limit_requests,
                        }
                    },
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "detail": "Rate limit exceeded. Please try again later.",
                        "retry_after": self._settings.rate_limit_window_seconds,
                    },
                    headers={
                        "Retry-After": str(self._settings.rate_limit_window_seconds),
                        "X-RateLimit-Limit": str(self._settings.rate_limit_requests),
                        "X-RateLimit-Remaining": "0",
                    },
                )

            # Within limit — proceed with the request
            response = await call_next(request)

            # Add rate limit headers to response (transparency for clients)
            remaining = max(
                0, self._settings.rate_limit_requests - request_count
            )
            response.headers["X-RateLimit-Limit"] = str(
                self._settings.rate_limit_requests
            )
            response.headers["X-RateLimit-Remaining"] = str(remaining)

            return response

        except Exception as e:
            # Fail open — if rate limiter crashes, still serve requests
            logger.error(
                f"Rate limiter error: {e}",
                extra={"extra_data": {"error": str(e)}},
            )
            return await call_next(request)

    @staticmethod
    def _get_client_ip(request: Request) -> str:
        """
        Extract the real client IP address.

        WHY X-FORWARDED-FOR?
          Behind a reverse proxy (Nginx, AWS ALB), request.client.host
          is the proxy's IP, not the user's. X-Forwarded-For contains
          the original client IP.

          Format: "X-Forwarded-For: client, proxy1, proxy2"
          We take the FIRST IP (the original client).

        Args:
            request: The HTTP request.

        Returns:
            Client IP address string.
        """
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            # Take the first IP (original client)
            return forwarded_for.split(",")[0].strip()

        # Direct connection (no proxy)
        if request.client:
            return request.client.host

        return "unknown"

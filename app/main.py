"""
FastAPI Application Factory — Entry Point

APPLICATION LIFECYCLE:
  This module creates and configures the FastAPI application.
  It follows the "Application Factory" pattern:

  1. STARTUP (lifespan context manager):
     - Initialize database tables
     - Connect to Redis
     - Wire up rate limiter middleware
     → App is ready to serve requests

  2. REQUEST HANDLING:
     - Middleware chain: Rate Limiter → CORS → Route Handler
     - Each request gets fresh DB session via DI
     - Exception handlers catch domain errors → HTTP responses

  3. SHUTDOWN:
     - Close Redis connection (return connections to pool)
     - Close DB engine (drain connection pool)
     → Graceful shutdown, no leaked connections

  WHY LIFESPAN (not @app.on_event)?
    @app.on_event("startup") is deprecated in FastAPI.
    The lifespan context manager is the modern replacement:
    - Code before `yield` runs on startup
    - Code after `yield` runs on shutdown
    - Cleaner resource management (like `with` statements)
"""

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.dependencies import init_cache_service, shutdown_cache_service
from app.api.routes import router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.core.rate_limiter import RateLimiterMiddleware
from app.db.base import Base
from app.db.session import engine
from app.exceptions import URLShortenerException

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager — startup and shutdown logic.

    STARTUP:
    1. Configure structured logging
    2. Create database tables (if they don't exist)
    3. Initialize Redis connection
    4. Wire rate limiter to the cache service

    SHUTDOWN:
    1. Disconnect Redis
    2. Dispose DB engine (close all pooled connections)
    """
    settings = get_settings()

    # --- STARTUP ---
    setup_logging(level="DEBUG" if settings.debug else "INFO")
    logger.info(
        "Starting URL Shortener service",
        extra={"extra_data": {"env": settings.app_env}},
    )

    # Create database tables
    # In production, you'd use Alembic migrations instead.
    # This is for development convenience.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    logger.info("Database tables created/verified")

    # Initialize Redis
    cache_service = await init_cache_service()
    logger.info("Cache service initialized")

    # Wire rate limiter middleware to the cache service
    for middleware in app.user_middleware:
        if hasattr(middleware, "cls") and middleware.cls == RateLimiterMiddleware:
            # Middleware is already added but needs the cache service
            pass
    # Set cache on the rate limiter instance after middleware stack is built
    app.state.cache_service = cache_service

    yield

    # --- SHUTDOWN ---
    logger.info("Shutting down URL Shortener service")
    await shutdown_cache_service()
    await engine.dispose()
    logger.info("All connections closed. Goodbye!")


def create_app() -> FastAPI:
    """
    Application factory — creates and configures the FastAPI app.

    WHY A FACTORY FUNCTION?
    - Testing: create a fresh app per test (no global state leaking)
    - Configuration: different settings for dev/staging/prod
    - Modularity: easy to add or remove middleware, routes, etc.

    Returns:
        Configured FastAPI application instance.
    """
    settings = get_settings()

    app = FastAPI(
        title="Distributed URL Shortener",
        description=(
            "A production-grade URL shortener service with Redis caching, "
            "rate limiting, click analytics, and expiration support."
        ),
        version="1.0.0",
        lifespan=lifespan,
        docs_url="/docs" if not settings.is_production else None,
        redoc_url="/redoc" if not settings.is_production else None,
    )

    # --- Middleware Stack ---
    # Middleware executes in REVERSE order of addition:
    # Last added = first to run

    # CORS middleware (for frontend integration)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if not settings.is_production else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Rate limiter middleware (runs before routes)
    app.add_middleware(RateLimiterMiddleware)

    # --- Exception Handlers ---
    # Catch domain exceptions and convert to HTTP responses
    @app.exception_handler(URLShortenerException)
    async def url_shortener_exception_handler(
        request: Request, exc: URLShortenerException
    ) -> JSONResponse:
        """
        Global exception handler for all domain-specific errors.

        WHY A GLOBAL HANDLER?
          Without this, unhandled exceptions become generic 500 errors.
          This handler ensures EVERY domain exception gets a proper
          HTTP status code and structured error response.

          Route handlers don't need try/except — they just call services,
          and exceptions bubble up to this handler automatically.
        """
        headers = {}
        if hasattr(exc, "retry_after"):
            headers["Retry-After"] = str(exc.retry_after)

        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": exc.message, "status_code": exc.status_code},
            headers=headers,
        )

    # --- Routes ---
    app.include_router(router)

    return app


# Create the app instance
# Uvicorn will look for this: uvicorn app.main:app
app = create_app()

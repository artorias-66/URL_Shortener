"""
API Routes — HTTP Endpoint Definitions

ROUTE HANDLER PHILOSOPHY:
  Route handlers should be THIN. They are responsible for:
  1. Parsing the HTTP request (FastAPI does this automatically)
  2. Calling the appropriate service method
  3. Formatting the HTTP response
  4. That's it. NO business logic here.

  WHY THIN ROUTES?
  - Business logic in routes is untestable without HTTP overhead
  - Logic in routes can't be reused (CLI tools, background workers)
  - Routes change when your API version changes
  - Services change when your business rules change
  - Keeping them separate = independent change velocity

  If your route handler has more than ~10 lines, it's doing too much.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_cache_service, get_db
from app.schemas.url_schema import URLCreateRequest, URLResponse, URLStatsResponse
from app.services.cache_service import CacheService
from app.services.url_service import URLService

router = APIRouter()


def _build_url_service(
    db: AsyncSession = Depends(get_db),
    cache: CacheService = Depends(get_cache_service),
) -> URLService:
    """
    FastAPI dependency that constructs a URLService.

    This is the glue between FastAPI's DI and our service layer.
    FastAPI resolves get_db and get_cache_service, then passes
    them here to build the service.
    """
    return URLService(db=db, cache=cache)


@router.post(
    "/shorten",
    response_model=URLResponse,
    status_code=201,
    summary="Shorten a URL",
    description="Convert a long URL into a short Base62-encoded slug.",
    responses={
        201: {"description": "URL shortened successfully"},
        400: {"description": "Invalid URL format"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def shorten_url(
    request: URLCreateRequest,
    http_request: Request,
    service: URLService = Depends(_build_url_service),
) -> URLResponse:
    """
    Create a shortened URL.

    FLOW:
    1. FastAPI validates the request body (Pydantic)
    2. FastAPI resolves dependencies (DB session, cache, service)
    3. Service generates slug, persists to DB, returns response
    4. FastAPI serializes to JSON and returns 201

    Notice: NO business logic here. Just delegation to service.
    """
    # Derive the base URL from the actual incoming request so the
    # short_url works on any host (localhost, Vercel, custom domain).
    base_url = str(http_request.base_url).rstrip("/")
    return await service.create_short_url(request, base_url=base_url)


@router.get(
    "/health",
    summary="Health check",
    description="Returns service health status.",
)
async def health_check() -> dict:
    """
    Health check endpoint for load balancers and monitoring.

    WHY A HEALTH ENDPOINT?
      Load balancers (AWS ALB, Kubernetes) periodically ping this
      to decide if the instance is healthy. If it returns non-200,
      the instance is taken out of rotation and replaced.

      In production, you'd also check DB and Redis connectivity here.
    """
    return {"status": "healthy", "service": "url-shortener"}


@router.get(
    "/{short_code}/stats",
    response_model=URLStatsResponse,
    summary="Get URL analytics",
    description="Retrieve click count and metadata for a short URL.",
    responses={
        200: {"description": "URL stats retrieved"},
        404: {"description": "Short code not found"},
    },
)
async def get_url_stats(
    short_code: str,
    service: URLService = Depends(_build_url_service),
) -> URLStatsResponse:
    """
    Get analytics for a shortened URL.

    WHY IS THIS ROUTE BEFORE /{short_code}?
      FastAPI matches routes TOP-DOWN. If /{short_code} was first,
      "abc123/stats" would match /{short_code} with code="abc123/stats".
      By placing /stats first, it takes priority for exact matches.

      This is a common routing gotcha in any web framework.
    """
    return await service.get_url_stats(short_code)


@router.get(
    "/{short_code}",
    summary="Redirect to original URL",
    description="Resolve a short code and redirect with HTTP 302.",
    responses={
        302: {"description": "Redirect to original URL"},
        404: {"description": "Short code not found"},
        410: {"description": "URL has expired"},
        429: {"description": "Rate limit exceeded"},
    },
)
async def redirect_to_url(
    short_code: str,
    request: Request,
    service: URLService = Depends(_build_url_service),
) -> RedirectResponse:
    """
    Redirect to the original URL.

    WHY HTTP 302 (not 301)?
      - 301 (Permanent Redirect): browser CACHES forever. You can never
        change the destination or track clicks, because the browser
        won't hit your server again.
      - 302 (Temporary Redirect): browser always comes back to your
        server first. This lets you:
        - Track every click
        - Change the destination URL
        - Enforce expiration
        - Apply rate limiting

      bit.ly, tinyurl, and most shorteners use 302 for this reason.
      301 is better for SEO redirects where you OWN both URLs.

    Args:
        short_code: The Base62 short code to resolve.
        request: The HTTP request (for logging client info).

    Returns:
        HTTP 302 redirect to the original URL.
    """
    original_url = await service.resolve_short_code(short_code)
    return RedirectResponse(url=original_url, status_code=302)

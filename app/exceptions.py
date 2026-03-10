"""
Custom Application Exceptions

WHY CUSTOM EXCEPTIONS?
  In production APIs, you need fine-grained error handling:
  - Different HTTP status codes for different error types
  - Structured error responses (not generic 500s)
  - Clean separation between business logic errors and framework errors

  Custom exceptions act as a CONTRACT between service layer and API layer:
  - Services raise domain-specific exceptions
  - Exception handlers translate them to HTTP responses
  - Route handlers stay clean — no try/except spaghetti

  This follows the "fail fast, handle at the boundary" principle.
"""


class URLShortenerException(Exception):
    """Base exception for all application-specific errors."""

    def __init__(self, message: str, status_code: int = 500) -> None:
        self.message = message
        self.status_code = status_code
        super().__init__(self.message)


class URLNotFoundException(URLShortenerException):
    """Raised when a short code does not exist in the system."""

    def __init__(self, short_code: str) -> None:
        super().__init__(
            message=f"URL with short code '{short_code}' not found",
            status_code=404,
        )
        self.short_code = short_code


class URLExpiredException(URLShortenerException):
    """
    Raised when a short code exists but has expired.

    WHY 410 GONE (not 404)?
      HTTP 410 signals "this resource EXISTED but is permanently gone."
      This is semantically correct for expired URLs and helps clients
      (browsers, crawlers) stop retrying, unlike 404 which implies
      "maybe try later."
    """

    def __init__(self, short_code: str) -> None:
        super().__init__(
            message=f"URL with short code '{short_code}' has expired",
            status_code=410,
        )
        self.short_code = short_code


class RateLimitExceededException(URLShortenerException):
    """Raised when a client exceeds the rate limit."""

    def __init__(self, retry_after: int = 60) -> None:
        super().__init__(
            message="Rate limit exceeded. Please try again later.",
            status_code=429,
        )
        self.retry_after = retry_after


class SlugCollisionException(URLShortenerException):
    """
    Raised when slug generation fails after max retries.

    WHY THIS MATTERS:
      As the URL space fills up, collisions become more frequent.
      This exception signals that the system is reaching capacity
      and may need to increase slug length or add a new shard.
    """

    def __init__(self) -> None:
        super().__init__(
            message="Failed to generate a unique short code after maximum retries",
            status_code=500,
        )


class InvalidURLException(URLShortenerException):
    """Raised when the provided URL fails validation."""

    def __init__(self, url: str) -> None:
        super().__init__(
            message=f"Invalid URL provided: '{url}'",
            status_code=400,
        )
        self.url = url

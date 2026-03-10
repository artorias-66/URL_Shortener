"""
Pydantic Schemas (Request/Response DTOs)

WHY SEPARATE SCHEMAS FROM MODELS?
  - Models = database representation (SQLAlchemy, tied to DB schema)
  - Schemas = API contract (Pydantic, tied to what clients see)

  This separation means you can:
  - Change DB columns without breaking the API
  - Hide internal fields (id, is_active) from public responses
  - Validate input independently of the database
  - Version your API without changing your DB

  This is the "DTO pattern" (Data Transfer Object) — a core concept
  in clean architecture that interviewers love to see.
"""

from datetime import datetime

from pydantic import BaseModel, Field, HttpUrl


class URLCreateRequest(BaseModel):
    """
    Schema for URL creation requests.

    WHY HttpUrl VALIDATION?
      Pydantic's HttpUrl type validates that the input is a properly
      formatted URL with http:// or https:// scheme. This prevents:
      - Storing garbage strings
      - XSS via javascript: URLs
      - Open redirect attacks via relative paths
    """

    url: HttpUrl = Field(
        ...,
        description="The original URL to shorten",
        examples=["https://www.example.com/very/long/path?with=params"],
    )
    expires_in_minutes: int | None = Field(
        default=None,
        ge=1,
        le=525600,  # Max 1 year
        description="Optional expiration time in minutes (1 min to 1 year)",
    )


class URLResponse(BaseModel):
    """Schema for URL creation response."""

    short_code: str = Field(
        ..., description="The generated short code"
    )
    short_url: str = Field(
        ..., description="Full shortened URL including base domain"
    )
    original_url: str = Field(
        ..., description="The original long URL"
    )
    created_at: datetime = Field(
        ..., description="Creation timestamp"
    )
    expires_at: datetime | None = Field(
        default=None, description="Expiration timestamp if set"
    )

    model_config = {"from_attributes": True}


class URLStatsResponse(BaseModel):
    """Schema for URL analytics response."""

    short_code: str = Field(
        ..., description="The short code"
    )
    original_url: str = Field(
        ..., description="The original long URL"
    )
    click_count: int = Field(
        ..., description="Total number of redirects"
    )
    created_at: datetime = Field(
        ..., description="Creation timestamp"
    )
    expires_at: datetime | None = Field(
        default=None, description="Expiration timestamp if set"
    )
    last_accessed_at: datetime | None = Field(
        default=None, description="Last redirect timestamp"
    )

    model_config = {"from_attributes": True}


class ErrorResponse(BaseModel):
    """Standard error response schema."""

    detail: str = Field(..., description="Error description")
    status_code: int = Field(..., description="HTTP status code")

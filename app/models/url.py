"""
URL Database Model

DATABASE SCHEMA DESIGN DECISIONS:

1. short_code — UNIQUE + INDEXED
   This is the most queried column (every redirect hits it).
   A B-tree index on short_code gives O(log n) lookups.
   Without it, every redirect would do a full table scan — O(n).

2. click_count — denormalized counter
   WHY NOT a separate analytics table?
   For an intern-level project, a denormalized counter is pragmatic.
   In a real system at scale, you'd use a separate analytics pipeline
   (Kafka → ClickHouse) to avoid write contention on the URL row.

3. expires_at — nullable
   NULL means "never expires." This avoids needing a sentinel date
   like year 9999 and allows clean SQL: WHERE expires_at IS NULL OR expires_at > NOW()

4. is_active — soft delete flag
   Never hard-delete data in production. Soft deletes let you:
   - Audit what was deleted and when
   - Restore accidentally deleted URLs
   - Keep analytics data intact
"""

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class URL(Base):
    """SQLAlchemy model representing a shortened URL."""

    __tablename__ = "urls"

    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True
    )
    original_url: Mapped[str] = mapped_column(
        Text, nullable=False, doc="The original long URL"
    )
    short_code: Mapped[str] = mapped_column(
        String(20), unique=True, nullable=False, doc="Base62-encoded short code"
    )
    click_count: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, doc="Number of times this URL was accessed"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
        doc="Timestamp when the URL was created",
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        doc="Expiration timestamp (NULL = never expires)",
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        default=None,
        doc="Last time this URL was redirected",
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False, doc="Soft delete flag"
    )

    # Database indexes for performance
    __table_args__ = (
        # Primary lookup index — every redirect query uses this
        Index("ix_urls_short_code", "short_code", unique=True),
        # For cleanup jobs: find expired URLs efficiently
        Index("ix_urls_expires_at", "expires_at"),
        # For listing active URLs (admin dashboard)
        Index("ix_urls_is_active", "is_active"),
    )

    def __repr__(self) -> str:
        return f"<URL(short_code='{self.short_code}', clicks={self.click_count})>"

    @property
    def is_expired(self) -> bool:
        """Check if this URL has passed its expiration time."""
        if self.expires_at is None:
            return False
        return datetime.now(timezone.utc) >= self.expires_at

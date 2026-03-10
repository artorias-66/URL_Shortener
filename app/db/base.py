"""
SQLAlchemy Declarative Base

WHY A SEPARATE BASE MODULE?
  All models inherit from a single Base class. Keeping it in its
  own module avoids circular imports — models import Base, but
  Base doesn't import any models. This is a standard SQLAlchemy pattern.
"""

from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass

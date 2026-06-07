"""Stub module for historical Alembic migrations."""

from sqlalchemy.dialects.postgresql import JSONB


class FilterType(JSONB):
    """Stub of the removed FilterType for historical migrations compatibility."""

    pass

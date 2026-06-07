"""Stub module for historical Alembic migrations."""

from sqlalchemy.dialects.postgresql import JSONB


class AggregationType(JSONB):
    """Stub of the removed AggregationType for historical migrations compatibility."""

    pass

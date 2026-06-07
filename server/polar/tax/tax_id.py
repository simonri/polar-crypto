"""Stub module for historical Alembic migrations that import TaxIDType."""

from sqlalchemy.dialects.postgresql import JSONB


class TaxIDType(JSONB):
    """Stub of the removed TaxIDType for historical migrations compatibility."""

    pass

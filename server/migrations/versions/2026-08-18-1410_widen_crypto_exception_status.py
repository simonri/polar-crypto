"""widen_crypto_exception_status

The backoffice accept action stores "accepted_<reason>"; several reasons
(paid_partial, paid_late_short, duplicate_payment) overflow varchar(20).

Revision ID: 9b3d4e5f6a71
Revises: 7c1e2a9f4b10
Create Date: 2026-08-18 14:10:00.000000

"""

import sqlalchemy as sa
from alembic import op

# Polar Custom Imports

# revision identifiers, used by Alembic.
revision = "9b3d4e5f6a71"
down_revision = "7c1e2a9f4b10"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.alter_column(
        "crypto_invoices",
        "exception_status",
        existing_type=sa.String(length=20),
        type_=sa.String(length=40),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "crypto_invoices",
        "exception_status",
        existing_type=sa.String(length=40),
        type_=sa.String(length=20),
        existing_nullable=False,
    )

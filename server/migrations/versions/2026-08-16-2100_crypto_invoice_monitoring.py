"""crypto_invoice_monitoring

Adds late-payment monitoring and partial-payment tracking to crypto invoices.

Revision ID: 7c1e2a9f4b10
Revises: 60d94b9a0729
Create Date: 2026-08-16 21:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

# Polar Custom Imports

# revision identifiers, used by Alembic.
revision = "7c1e2a9f4b10"
down_revision = "60d94b9a0729"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    # Ensures we don't break app by applying a deadlock-inducing migration
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.add_column(
        "crypto_invoices",
        sa.Column("monitoring_expiry", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.add_column(
        "crypto_invoices",
        sa.Column("payment_detected_at", sa.TIMESTAMP(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_crypto_invoices_monitoring_expiry",
        "crypto_invoices",
        ["monitoring_expiry"],
    )
    # Existing invoices: keep watching them for a day from now so anything
    # in flight at deploy time is not dropped.
    op.execute(
        "UPDATE crypto_invoices SET monitoring_expiry = now() + interval '24 hours' "
        "WHERE monitoring_expiry IS NULL"
    )


def downgrade() -> None:
    # Ensures we don't break app by applying a deadlock-inducing migration
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.drop_index("ix_crypto_invoices_monitoring_expiry", table_name="crypto_invoices")
    op.drop_column("crypto_invoices", "payment_detected_at")
    op.drop_column("crypto_invoices", "monitoring_expiry")

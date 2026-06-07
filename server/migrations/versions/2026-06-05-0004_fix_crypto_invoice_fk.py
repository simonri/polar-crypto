"""Fix crypto_invoices.order_id FK to reference checkouts instead of orders

Revision ID: cc4455667788
Revises: bb3344556677
Create Date: 2026-06-05 00:04:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "cc4455667788"
down_revision = "bb3344556677"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint(
        "crypto_invoices_order_id_fkey", "crypto_invoices", type_="foreignkey"
    )
    op.create_foreign_key(
        "crypto_invoices_order_id_fkey",
        "crypto_invoices",
        "checkouts",
        ["order_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    op.drop_constraint(
        "crypto_invoices_order_id_fkey", "crypto_invoices", type_="foreignkey"
    )
    op.create_foreign_key(
        "crypto_invoices_order_id_fkey",
        "crypto_invoices",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="CASCADE",
    )

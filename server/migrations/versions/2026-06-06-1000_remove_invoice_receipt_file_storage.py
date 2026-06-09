"""Remove invoice, receipt, and file storage (MinIO/S3)

Revision ID: cc3344556677
Revises: hh9900112233
Create Date: 2026-06-06 10:00:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "cc3344556677"
down_revision = "hh9900112233"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")

    # Drop triggers and functions from orders
    op.execute("DROP TRIGGER IF EXISTS orders_search_vector_trigger ON orders")
    op.execute("DROP FUNCTION IF EXISTS orders_search_vector_update() CASCADE")

    # Drop indexes on orders
    op.execute("DROP INDEX IF EXISTS ix_orders_search_vector")
    op.execute("DROP INDEX IF EXISTS ix_orders_customer_id_receipt_number")

    # Drop columns from orders
    op.drop_column("orders", "search_vector")
    op.drop_column("orders", "invoice_number")
    op.drop_column("orders", "invoice_path")
    op.drop_column("orders", "invoice_checksum")
    op.drop_column("orders", "receipt_number")
    op.drop_column("orders", "receipt_path")

    # Drop columns from payouts
    op.drop_column("payouts", "invoice_number")
    op.drop_column("payouts", "invoice_path")

    # Drop columns from customers
    op.drop_column("customers", "invoice_next_number")
    op.drop_column("customers", "receipt_next_number")

    # Drop columns from organizations
    op.drop_column("organizations", "customer_invoice_prefix")
    op.drop_column("organizations", "customer_invoice_next_number")
    op.drop_column("organizations", "order_settings")

    # Drop product_medias table (association between products and files)
    op.drop_table("product_medias")

    # Drop files table
    op.drop_table("files")


def downgrade() -> None:
    pass

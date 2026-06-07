"""Remove tax columns from all tables

Revision ID: bb3344556677
Revises: aa1122334455
Create Date: 2026-06-05 00:03:00.000000
"""

import sqlalchemy as sa
from alembic import op

revision = "bb3344556677"
down_revision = "aa1122334455"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # checkouts
    op.drop_column("checkouts", "tax_amount_v2")
    op.drop_column("checkouts", "tax_processor")
    op.drop_column("checkouts", "tax_breakdown")
    op.drop_column("checkouts", "tax_behavior")
    op.drop_column("checkouts", "tax_processor_id")
    op.drop_column("checkouts", "customer_tax_id")

    # orders
    op.drop_index("ix_total_amount", table_name="orders")
    op.drop_column("orders", "tax_amount_v2")
    op.drop_column("orders", "refunded_tax_amount_v2")
    op.drop_column("orders", "tax_behavior")
    op.drop_column("orders", "tax_id")
    op.drop_column("orders", "tax_breakdown")
    op.drop_column("orders", "tax_processor")
    op.drop_column("orders", "tax_calculation_processor_id")
    op.drop_column("orders", "tax_transaction_processor_id")

    # order_items
    op.drop_column("order_items", "tax_amount_v2")

    # transactions
    op.drop_column("transactions", "tax_amount")
    op.drop_column("transactions", "tax_country")
    op.drop_column("transactions", "tax_state")
    op.drop_column("transactions", "presentment_tax_amount")
    op.drop_column("transactions", "tax_processor")
    op.drop_column("transactions", "tax_filing_amount")
    op.drop_column("transactions", "tax_filing_currency")
    op.drop_column("transactions", "tax_processor_id")

    # subscriptions
    op.drop_column("subscriptions", "tax_behavior")
    op.drop_column("subscriptions", "tax_exempted")

    # customers
    op.drop_column("customers", "tax_id")

    # products
    op.drop_column("products", "is_tax_applicable")
    op.drop_column("products", "tax_code")

    # product_prices
    op.drop_column("product_prices", "tax_behavior")

    # refunds
    op.drop_column("refunds", "tax_amount")
    op.drop_column("refunds", "tax_transaction_processor_id")

    # wallet_transactions
    op.drop_column("wallet_transactions", "tax_processor")
    op.drop_column("wallet_transactions", "tax_amount")
    op.drop_column("wallet_transactions", "tax_breakdown")
    op.drop_column("wallet_transactions", "tax_calculation_processor_id")

    # disputes
    op.drop_column("disputes", "tax_amount")

    # organizations
    op.drop_column("organizations", "default_tax_behavior")


def downgrade() -> None:
    pass

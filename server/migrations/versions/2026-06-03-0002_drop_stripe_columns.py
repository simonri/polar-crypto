"""Drop remaining Stripe-specific columns from all tables

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-06-03 00:02:00.000000

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "dd8e9f0a1b2c"
down_revision = "cc7d8e9f0a1b"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    # Drop stripe_customer_id from customers
    op.drop_constraint(
        "uq_customers_stripe_customer_id", "customers", type_="unique", if_exists=True
    )
    op.drop_index(
        "ix_customers_stripe_customer_id", table_name="customers", if_exists=True
    )
    op.drop_column("customers", "stripe_customer_id")

    # Drop stripe_invoice_id from orders
    op.drop_constraint(
        "orders_stripe_invoice_id_key", "orders", type_="unique", if_exists=True
    )
    op.drop_index("ix_orders_stripe_invoice_id", table_name="orders", if_exists=True)
    op.drop_column("orders", "stripe_invoice_id")

    # Drop stripe_id from payout_accounts
    op.drop_index(
        "ix_payout_accounts_stripe_id", table_name="payout_accounts", if_exists=True
    )
    op.drop_column("payout_accounts", "stripe_id")

    # Drop stripe_customer_id from users
    op.drop_constraint(
        "users_stripe_customer_id_key", "users", type_="unique", if_exists=True
    )
    op.drop_column("users", "stripe_customer_id")

    # Update the orders search vector trigger to remove stripe_invoice_id reference
    op.execute(
        """
        CREATE OR REPLACE FUNCTION orders_search_vector_update()
        RETURNS trigger AS $$
        BEGIN
            NEW.search_vector := to_tsvector('simple', coalesce(NEW.invoice_number, ''));
            RETURN NEW;
        END
        $$ LANGUAGE plpgsql;
        """
    )


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
    )
    op.add_column(
        "payout_accounts",
        sa.Column("stripe_id", sa.String(100), nullable=True),
    )
    op.add_column(
        "orders",
        sa.Column("stripe_invoice_id", sa.String(), nullable=True, unique=True),
    )
    op.add_column(
        "customers",
        sa.Column("stripe_customer_id", sa.String(), nullable=True),
    )

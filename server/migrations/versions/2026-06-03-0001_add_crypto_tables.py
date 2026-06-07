"""Add crypto invoice, payment method, and payout wallet tables

Revision ID: a1b2c3d4e5f6
Revises: b7b69a5d5731
Create Date: 2026-06-03 00:01:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# Polar Custom Imports

# revision identifiers, used by Alembic.
revision = "cc7d8e9f0a1b"
down_revision = "d2a49dc19a62"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    # crypto_invoices
    op.create_table(
        "crypto_invoices",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("modified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("price", sa.Numeric(20, 8), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column(
            "exception_status", sa.String(20), nullable=False, server_default="none"
        ),
        sa.Column("buyer_email", sa.String(320), nullable=True),
        sa.Column("expiry", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("paid_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("paid_crypto_amount", sa.Numeric(30, 18), nullable=True),
        sa.Column("paid_crypto_currency", sa.String(10), nullable=True),
        sa.Column(
            "tx_hashes",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default="[]",
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_crypto_invoices_created_at", "crypto_invoices", ["created_at"])
    op.create_index("ix_crypto_invoices_deleted_at", "crypto_invoices", ["deleted_at"])
    op.create_index("ix_crypto_invoices_expiry", "crypto_invoices", ["expiry"])
    op.create_index("ix_crypto_invoices_order_id", "crypto_invoices", ["order_id"])
    op.create_index("ix_crypto_invoices_status", "crypto_invoices", ["status"])

    # crypto_payment_methods
    op.create_table(
        "crypto_payment_methods",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("modified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("invoice_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("amount", sa.Numeric(30, 18), nullable=False),
        sa.Column("rate", sa.Numeric(20, 8), nullable=False),
        sa.Column("payment_address", sa.String(500), nullable=False),
        sa.Column("lookup_field", sa.String(500), nullable=False),
        sa.Column("payment_url", sa.String(1000), nullable=False),
        sa.Column("lightning", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("confirmations", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_used", sa.Boolean(), nullable=False, server_default="false"),
        sa.ForeignKeyConstraint(
            ["invoice_id"], ["crypto_invoices.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_crypto_payment_methods_created_at", "crypto_payment_methods", ["created_at"]
    )
    op.create_index(
        "ix_crypto_payment_methods_deleted_at", "crypto_payment_methods", ["deleted_at"]
    )
    op.create_index(
        "ix_crypto_payment_methods_invoice_id", "crypto_payment_methods", ["invoice_id"]
    )
    op.create_index(
        "ix_crypto_payment_methods_is_used", "crypto_payment_methods", ["is_used"]
    )
    op.create_index(
        "ix_crypto_payment_methods_lookup",
        "crypto_payment_methods",
        ["currency", "lookup_field"],
    )
    op.create_index(
        "ix_crypto_payment_methods_lookup_field",
        "crypto_payment_methods",
        ["lookup_field"],
    )

    # crypto_payout_wallets
    op.create_table(
        "crypto_payout_wallets",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("modified_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("account_id", sa.Uuid(), nullable=False),
        sa.Column("currency", sa.String(10), nullable=False),
        sa.Column("wallet_address", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(
            ["account_id"], ["payout_accounts.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "currency", name="uq_crypto_payout_wallet"),
    )
    op.create_index(
        "ix_crypto_payout_wallets_account_id", "crypto_payout_wallets", ["account_id"]
    )
    op.create_index(
        "ix_crypto_payout_wallets_created_at", "crypto_payout_wallets", ["created_at"]
    )
    op.create_index(
        "ix_crypto_payout_wallets_deleted_at", "crypto_payout_wallets", ["deleted_at"]
    )

    # Add crypto_invoice_id to checkouts and orders
    op.add_column(
        "checkouts",
        sa.Column("crypto_invoice_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_checkouts_crypto_invoice_id",
        "checkouts",
        "crypto_invoices",
        ["crypto_invoice_id"],
        ["id"],
        ondelete="SET NULL",
    )

    op.add_column(
        "orders",
        sa.Column("crypto_invoice_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_orders_crypto_invoice_id",
        "orders",
        "crypto_invoices",
        ["crypto_invoice_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_orders_crypto_invoice_id", "orders", type_="foreignkey")
    op.drop_column("orders", "crypto_invoice_id")
    op.drop_constraint(
        "fk_checkouts_crypto_invoice_id", "checkouts", type_="foreignkey"
    )
    op.drop_column("checkouts", "crypto_invoice_id")

    op.drop_index(
        "ix_crypto_payout_wallets_deleted_at", table_name="crypto_payout_wallets"
    )
    op.drop_index(
        "ix_crypto_payout_wallets_created_at", table_name="crypto_payout_wallets"
    )
    op.drop_index(
        "ix_crypto_payout_wallets_account_id", table_name="crypto_payout_wallets"
    )
    op.drop_table("crypto_payout_wallets")

    op.drop_index(
        "ix_crypto_payment_methods_lookup_field", table_name="crypto_payment_methods"
    )
    op.drop_index(
        "ix_crypto_payment_methods_lookup", table_name="crypto_payment_methods"
    )
    op.drop_index(
        "ix_crypto_payment_methods_is_used", table_name="crypto_payment_methods"
    )
    op.drop_index(
        "ix_crypto_payment_methods_invoice_id", table_name="crypto_payment_methods"
    )
    op.drop_index(
        "ix_crypto_payment_methods_deleted_at", table_name="crypto_payment_methods"
    )
    op.drop_index(
        "ix_crypto_payment_methods_created_at", table_name="crypto_payment_methods"
    )
    op.drop_table("crypto_payment_methods")

    op.drop_index("ix_crypto_invoices_status", table_name="crypto_invoices")
    op.drop_index("ix_crypto_invoices_order_id", table_name="crypto_invoices")
    op.drop_index("ix_crypto_invoices_expiry", table_name="crypto_invoices")
    op.drop_index("ix_crypto_invoices_deleted_at", table_name="crypto_invoices")
    op.drop_index("ix_crypto_invoices_created_at", table_name="crypto_invoices")
    op.drop_table("crypto_invoices")

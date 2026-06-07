"""Remove meters feature

Revision ID: e7b8c9d0e1f2
Revises: dd8e9f0a1b2c
Create Date: 2026-06-04 19:28:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "e7b8c9d0e1f2"
down_revision = "dd8e9f0a1b2c"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        DELETE FROM billing_entry
        WHERE type = 'metered'
           OR product_price_id IN (
               SELECT id FROM product_prices WHERE amount_type = 'metered_unit'
           )
        """
    )
    op.execute(
        """
        DELETE FROM subscription_product_prices
        WHERE product_price_id IN (
            SELECT id FROM product_prices WHERE amount_type = 'metered_unit'
        )
        """
    )
    op.execute("DELETE FROM product_prices WHERE amount_type = 'metered_unit'")
    op.execute("DELETE FROM benefits WHERE type = 'meter_credit'")

    op.execute("DROP TABLE IF EXISTS meter_events CASCADE")
    op.execute("DROP TABLE IF EXISTS subscription_meters CASCADE")
    op.execute("DROP TABLE IF EXISTS customer_meters CASCADE")
    op.execute("DROP TABLE IF EXISTS meters CASCADE")

    op.execute("DROP INDEX IF EXISTS ix_customers_meters_dirtied_at")
    op.execute("DROP INDEX IF EXISTS ix_customers_meters_updated_at")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS meters_dirtied_at")
    op.execute("ALTER TABLE customers DROP COLUMN IF EXISTS meters_updated_at")

    op.execute(
        "ALTER TABLE product_prices DROP CONSTRAINT IF EXISTS product_prices_meter_id_fkey"
    )
    op.execute("DROP INDEX IF EXISTS ix_product_prices_meter_id")
    op.execute("ALTER TABLE product_prices DROP COLUMN IF EXISTS meter_id")
    op.execute("ALTER TABLE product_prices DROP COLUMN IF EXISTS unit_amount")
    op.execute("ALTER TABLE product_prices DROP COLUMN IF EXISTS included_units")
    op.execute("ALTER TABLE product_prices DROP COLUMN IF EXISTS cap_amount")
    op.execute("ALTER TABLE product_prices DROP COLUMN IF EXISTS cap_amount_v2")


def downgrade() -> None:
    # The migration deletes historical meter data and intentionally does not
    # recreate removed feature tables or columns.
    pass

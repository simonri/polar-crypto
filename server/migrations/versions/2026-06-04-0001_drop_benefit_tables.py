"""Drop benefit tables and related columns

Revision ID: aabb1122ccdd
Revises: dd8e9f0a1b2c
Create Date: 2026-06-04 00:01:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "aabb1122ccdd"
down_revision = "dd8e9f0a1b2c"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    # Drop benefit_grants first (FK references benefits and subscriptions/orders)
    op.drop_table("benefit_grants")

    # Drop product_benefits junction table
    op.drop_table("product_benefits")

    # Drop downloadables (benefit strategy data)
    op.drop_table("downloadables")

    # Drop license_key_activations before license_keys
    op.drop_table("license_key_activations")

    # Drop license_keys (benefit strategy data)
    op.drop_table("license_keys")

    # Drop benefits table last (referenced by above tables)
    op.drop_table("benefits")

    # Drop benefit_revocation_grace_period from organization subscription_settings
    # This is a JSONB field entry, not a column — no schema change needed.


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for benefit table removal")

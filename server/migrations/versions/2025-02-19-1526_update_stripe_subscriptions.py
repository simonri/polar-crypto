"""Update Stripe Subscriptions

Revision ID: 21585ed16305
Revises: 69d1834e6285
Create Date: 2025-02-19 15:26:53.346054

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "21585ed16305"
down_revision = "69d1834e6285"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    # Stripe data migration already applied on production — skipped here.
    pass


def downgrade() -> None:
    pass

"""Merge branches and drop user_organization.notification_settings

Revision ID: ee5566778899
Revises: bc2540d6d2c0, dd4455667788
Create Date: 2026-06-07 00:02:00.000000

"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "ee5566778899"
down_revision = ("bc2540d6d2c0", "dd4455667788")
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute(
        "ALTER TABLE user_organizations DROP COLUMN IF EXISTS notification_settings"
    )


def downgrade() -> None:
    op.add_column(
        "user_organizations",
        sa.Column(
            "notification_settings",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

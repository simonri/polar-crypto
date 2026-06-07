"""Remove identity_verification_status and identity_verification_id from users

Revision ID: ff7788990011
Revises: ee6677889900
Create Date: 2026-06-05 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "ff7788990011"
down_revision = "ee6677889900"
branch_labels: tuple[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("users", "identity_verification_status")
    op.drop_column("users", "identity_verification_id")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "identity_verification_id",
            sa.String(),
            nullable=True,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "identity_verification_status",
            sa.String(),
            nullable=False,
            server_default="unverified",
        ),
    )

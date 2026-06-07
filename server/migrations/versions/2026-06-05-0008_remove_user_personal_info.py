"""Remove first_name, last_name, country, date_of_birth from users

Revision ID: gg8899001122
Revises: ff7788990011
Create Date: 2026-06-05 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "gg8899001122"
down_revision = "ff7788990011"
branch_labels: tuple[str] | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_column("users", "first_name")
    op.drop_column("users", "last_name")
    op.drop_column("users", "country")
    op.drop_column("users", "date_of_birth")


def downgrade() -> None:
    op.add_column(
        "users",
        sa.Column("date_of_birth", sa.Date(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("country", sa.String(length=2), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("last_name", sa.String(), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("first_name", sa.String(), nullable=True),
    )

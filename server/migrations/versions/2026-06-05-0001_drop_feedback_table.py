"""Drop feedbacks table

Revision ID: ff2233445566
Revises: e7b8c9d0e1f2
Create Date: 2026-06-05 00:00:00.000000

"""

from alembic import op

revision = "ff2233445566"
down_revision = "e7b8c9d0e1f2"
branch_labels: None = None
depends_on: None = None


def upgrade() -> None:
    op.drop_table("feedbacks")


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for feedback table removal")

"""remove_customer_seats_scopes

Revision ID: hh9900112233
Revises: e4913dfc3774
Create Date: 2026-06-06 02:42:00.000000

"""

from alembic import op

# revision identifiers, used by Alembic.
revision = "hh9900112233"
down_revision = "e4913dfc3774"
branch_labels: tuple[str] | None = None
depends_on: tuple[str] | None = None

STALE_SCOPES = ["customer_seats:read", "customer_seats:write"]


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    for scope in STALE_SCOPES:
        op.execute(
            f"UPDATE user_sessions SET scopes = array_remove(scopes, '{scope}') "
            f"WHERE '{scope}' = ANY(scopes)"
        )


def downgrade() -> None:
    pass

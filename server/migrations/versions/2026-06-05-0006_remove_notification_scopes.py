"""Remove notification scopes from user_sessions

Revision ID: ee6677889900
Revises: dd5566778899
Create Date: 2026-06-05 00:00:00.000000

"""

import sqlalchemy as sa
from alembic import op

revision = "ee6677889900"
down_revision = "dd5566778899"
branch_labels: None = None
depends_on: None = None

NOTIFICATION_SCOPES = {
    "notifications:read",
    "notifications:write",
    "notification_recipients:read",
    "notification_recipients:write",
}


def upgrade() -> None:
    conn = op.get_bind()

    # Strip notification scopes from user_sessions.scopes (TEXT[] column)
    conn.execute(
        sa.text("""
            UPDATE user_sessions
            SET scopes = ARRAY(
                SELECT s FROM unnest(scopes) AS s
                WHERE s NOT IN (
                    'notifications:read',
                    'notifications:write',
                    'notification_recipients:read',
                    'notification_recipients:write'
                )
            )
            WHERE scopes && ARRAY[
                'notifications:read',
                'notifications:write',
                'notification_recipients:read',
                'notification_recipients:write'
            ]::varchar[]
        """)
    )

    # Strip notification scopes from oauth2_grants.scope (space-separated TEXT)
    conn.execute(
        sa.text("""
            UPDATE oauth2_grants
            SET scope = TRIM(REGEXP_REPLACE(
                scope,
                '\\m(notifications:read|notifications:write|notification_recipients:read|notification_recipients:write)\\M\\s*',
                '',
                'g'
            ))
            WHERE scope ~ '\\m(notifications:read|notifications:write|notification_recipients:read|notification_recipients:write)\\M'
        """)
    )

    # Strip notification scopes from personal_access_tokens.scope (space-separated TEXT)
    conn.execute(
        sa.text("""
            UPDATE personal_access_tokens
            SET scope = TRIM(REGEXP_REPLACE(
                scope,
                '\\m(notifications:read|notifications:write|notification_recipients:read|notification_recipients:write)\\M\\s*',
                '',
                'g'
            ))
            WHERE scope ~ '\\m(notifications:read|notifications:write|notification_recipients:read|notification_recipients:write)\\M'
        """)
    )

    # Strip notification scopes from organization_access_tokens.scope (space-separated TEXT)
    conn.execute(
        sa.text("""
            UPDATE organization_access_tokens
            SET scope = TRIM(REGEXP_REPLACE(
                scope,
                '\\m(notifications:read|notifications:write|notification_recipients:read|notification_recipients:write)\\M\\s*',
                '',
                'g'
            ))
            WHERE scope ~ '\\m(notifications:read|notifications:write|notification_recipients:read|notification_recipients:write)\\M'
        """)
    )

    # Drop the notification_settings column from organizations
    op.drop_column("organizations", "notification_settings")


def downgrade() -> None:
    op.add_column(
        "organizations",
        sa.Column(
            "notification_settings",
            sa.dialects.postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )

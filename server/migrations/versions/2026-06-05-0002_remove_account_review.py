"""Remove account review: activate all pending orgs, drop review tables and columns

Revision ID: aa1122334455
Revises: ff2233445566
Create Date: 2026-06-05 00:00:00.000000

"""

import json

import sqlalchemy as sa
from alembic import op

revision = "aa1122334455"
down_revision = "ff2233445566"
branch_labels: None = None
depends_on: None = None

_ACTIVE_CAPABILITIES = json.dumps(
    {
        "checkout_payments": True,
        "subscription_renewals": True,
        "payouts": True,
        "refunds": True,
        "api_access": True,
        "dashboard_access": True,
    }
)


def upgrade() -> None:
    # 1. Promote all non-blocked, non-offboarding orgs to ACTIVE with full capabilities.
    #    Orgs in created/review/snoozed/denied never got to accept payments or receive
    #    payouts — now they do by default.
    op.execute(
        f"""
        UPDATE organizations
        SET
            status = 'active',
            capabilities = '{_ACTIVE_CAPABILITIES}'::jsonb,
            status_updated_at = NOW()
        WHERE status IN ('created', 'review', 'snoozed', 'denied')
        """
    )

    # 2. Drop review-related columns from organizations.
    # Note: dropping next_review_threshold automatically drops its check constraint.
    op.drop_column("organizations", "next_review_threshold")
    op.drop_column("organizations", "initially_reviewed_at")
    op.drop_column("organizations", "snooze_count")
    op.drop_column("organizations", "snoozed_until")
    op.drop_column("organizations", "snooze_type")

    # 3. Drop review tables (foreign keys to organizations exist but cascade is safe
    #    since we're dropping the child tables, not the parent).
    op.drop_table("organization_review_feedback")
    op.drop_table("organization_agent_reviews")
    op.drop_table("organization_reviews")


def downgrade() -> None:
    raise NotImplementedError("Downgrade not supported for account review removal")

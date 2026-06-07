"""
Organizations V2 - Redesigned backoffice interface with improved UX.

This module provides a modern, three-column layout with:
- Enhanced list view with status tabs and smart grouping
- Progressive disclosure in detail views
- Contextual actions based on organization status
- Keyboard shortcuts and accessibility improvements
"""

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import UUID4, BaseModel, Field, ValidationError, field_validator
from pydantic_core import PydanticCustomError, SchemaSerializer, core_schema
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.orm import contains_eager, joinedload
from sse_starlette.sse import EventSourceResponse
from tagflow import tag, text

from polar.account.repository import AccountRepository
from polar.account_credit.repository import AccountCreditRepository
from polar.account_credit.service import account_credit_service
from polar.auth.scope import READ_ONLY_SCOPES
from polar.auth.service import auth as auth_service
from polar.backoffice.organizations.analytics import (
    OrganizationSetupAnalyticsService,
    PaymentAnalyticsService,
)
from polar.backoffice.organizations.forms import (
    AddPaymentMethodDomainForm,
    OrganizationOrdersImportForm,
    UpdateOrganizationBasicForm,
    UpdateOrganizationDetailsForm,
    UpdateOrganizationInternalNotesForm,
    UpdateOrganizationSocialsForm,
    UpdateRateLimitGroupForm,
)
from polar.backoffice.organizations.orders_import import orders_import_sse
from polar.config import settings
from polar.integrations.plain.service import (
    AccountReviewThreadCreationError,
    plain_thread_url,
)
from polar.integrations.plain.service import plain as plain_service
from polar.models import (
    AccountCredit,
    Organization,
    PayoutAccount,
    User,
    UserOrganization,
)
from polar.models.customer import Customer
from polar.models.order import Order, OrderStatus
from polar.models.organization import (
    CAPABILITY_METADATA,
    CAPABILITY_NAMES,
    CapabilityName,
    OrganizationStatus,
)
from polar.models.transaction import TransactionType
from polar.models.user_session import UserSession
from polar.organization.repository import OrganizationRepository
from polar.organization.schemas import OrganizationFeatureSettings
from polar.organization.service import organization as organization_service
from polar.payout_account.service import payout_account as payout_account_service
from polar.postgres import AsyncSession, get_db_session
from polar.transaction.service.transaction import transaction as transaction_service
from polar.worker import enqueue_job

from ..components import button, modal
from ..dependencies import get_admin
from ..layout import layout
from ..responses import HXRedirectResponse
from ..toast import add_toast
from .forms import UpdateAccountSettingsForm
from .views.detail_view import OrganizationDetailView
from .views.list_view import (
    DeletedFilter,
    OrganizationListView,
    apply_deleted_filter,
)
from .views.modals import DeletePayoutAccountModal, SetCapabilityModal
from .views.sections.account_section import AccountSection
from .views.sections.overview_section import OverviewSection
from .views.sections.payout_account_section import PayoutAccountSection
from .views.sections.settings_section import SettingsSection
from .views.sections.team_section import TeamSection

router = APIRouter(prefix="/organizations", tags=["organizations"])

logger = structlog.getLogger(__name__)


class DeletePayoutAccountForm(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Reason is required")
        return v

    @classmethod
    def model_validate_form(cls, data: Any) -> "DeletePayoutAccountForm":
        return cls.model_validate(dict(data))


class SetCapabilityForm(BaseModel):
    reason: str

    @field_validator("reason")
    @classmethod
    def reason_min_length(cls, v: str) -> str:
        if len(v.strip()) < 10:
            raise ValueError("Reason must be at least 10 characters.")
        return v.strip()

    @classmethod
    def model_validate_form(cls, data: Any) -> "SetCapabilityForm":
        return cls.model_validate(dict(data))


class CreateReviewTicketForm(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("Title is required")
        return stripped

    @classmethod
    def model_validate_form(cls, data: Any) -> "CreateReviewTicketForm":
        return cls.model_validate(dict(data))


async def count_test_sales(
    session: AsyncSession, organization_id: UUID4
) -> tuple[int, int]:
    """
    Count test sales (self-purchases by org team members with positive amounts).

    Uses UserOrganization + User to get actual org team member emails,
    NOT the Member model which represents customer usage entities.

    Returns (total_count, unrefunded_count).
    """
    team_member_emails_subquery = (
        select(func.lower(User.email))
        .join(UserOrganization, User.id == UserOrganization.user_id)
        .where(
            UserOrganization.organization_id == organization_id,
            UserOrganization.deleted_at.is_(None),
        )
        .correlate(None)
    )

    test_sales_filter = (
        Customer.organization_id == organization_id,
        func.lower(Customer.email).in_(team_member_emails_subquery),
        Order.net_amount > 0,
    )

    orders_count_result = await session.execute(
        select(func.count(Order.id))
        .join(Customer, Order.customer_id == Customer.id)
        .where(*test_sales_filter)
    )
    orders_count = orders_count_result.scalar() or 0

    unrefunded_orders_result = await session.execute(
        select(func.count(Order.id))
        .join(Customer, Order.customer_id == Customer.id)
        .where(
            *test_sales_filter,
            Order.status.notin_([OrderStatus.refunded, OrderStatus.partially_refunded]),
        )
    )
    unrefunded_orders_count = unrefunded_orders_result.scalar() or 0

    return orders_count, unrefunded_orders_count


def _apply_sql_sort(stmt: Select[Any], sort: str, direction: str) -> Select[Any]:
    is_desc = direction == "desc"
    if sort == "name":
        return stmt.order_by(
            Organization.name.desc() if is_desc else Organization.name.asc()
        )
    if sort == "country":
        return stmt.order_by(
            PayoutAccount.country.desc().nullslast()
            if is_desc
            else PayoutAccount.country.asc().nullslast()
        )
    if sort == "created":
        return stmt.order_by(
            Organization.created_at.desc() if is_desc else Organization.created_at.asc()
        )
    if sort == "updated":
        return stmt.order_by(
            Organization.modified_at.desc()
            if is_desc
            else Organization.modified_at.asc()
        )
    if sort == "status_duration":
        return stmt.order_by(
            Organization.status_updated_at.desc().nullslast()
            if is_desc
            else Organization.status_updated_at.asc().nullsfirst()
        )
    if sort == "total_balance":
        return stmt.order_by(
            Organization.total_balance.desc().nullslast()
            if is_desc
            else Organization.total_balance.asc().nullsfirst()
        )
    if sort == "priority":
        return stmt.order_by(
            Organization.status.desc(),
            Organization.status_updated_at.asc().nullsfirst(),
        )
    return stmt


@router.get("/", name="organizations:list")
async def list_organizations(
    request: Request,
    session: AsyncSession = Depends(get_db_session),
    status: str | None = Query(None),
    q: str | None = Query(None),
    sort: str = Query("created"),
    direction: str | None = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    # Advanced filters
    country: str | None = Query(""),
    days_in_status: str | None = Query(""),
    deleted: DeletedFilter | None = Query(None),
) -> None:
    """
    List organizations with enhanced filtering and smart grouping.
    """
    list_view = OrganizationListView(session)

    # Convert empty strings to None and parse numbers
    country = country if country else None
    days_in_status_int = int(days_in_status) if days_in_status else None
    # When searching, include deleted so matches surface.
    deleted_filter: DeletedFilter = deleted or ("include" if q else "exclude")

    # Parse status filter
    status_filter: OrganizationStatus | None = None
    if status == "active":
        status_filter = OrganizationStatus.ACTIVE
    elif status == "denied":
        status_filter = OrganizationStatus.DENIED
    elif status == "created":
        status_filter = OrganizationStatus.CREATED
    elif status == "offboarding":
        status_filter = OrganizationStatus.OFFBOARDING
    elif status == "review":
        status_filter = OrganizationStatus.REVIEW
    elif status == "snoozed":
        status_filter = OrganizationStatus.SNOOZED
    elif status == "blocked":
        status_filter = OrganizationStatus.BLOCKED

    # Build query
    stmt = (
        select(Organization)
        .join(Organization.payout_account, isouter=True)
        .options(
            contains_eager(Organization.payout_account),
            joinedload(Organization.account),
        )
    )

    # Apply filters
    if status_filter:
        stmt = stmt.where(Organization.status == status_filter)
    elif not q:
        # By default, exclude denied and blocked organizations (but not when searching)
        stmt = stmt.where(
            Organization.status.notin_(
                [
                    OrganizationStatus.DENIED,
                    OrganizationStatus.BLOCKED,
                ]
            )
        )

    if q:
        try:
            stmt = stmt.where(Organization.id == uuid.UUID(q))
        except ValueError:
            search_term = f"%{q}%"
            stmt = stmt.where(
                or_(
                    Organization.name.ilike(search_term),
                    Organization.slug.ilike(search_term),
                    Organization.email.ilike(search_term),
                )
            )

    # Country filter
    if country:
        stmt = stmt.where(PayoutAccount.country == country)

    # Days in status filter
    if days_in_status_int:
        threshold_date = datetime.now(UTC) - timedelta(days=days_in_status_int)
        stmt = stmt.where(
            or_(
                Organization.status_updated_at <= threshold_date,
                and_(
                    Organization.status_updated_at.is_(None),
                    Organization.created_at <= threshold_date,
                ),
            )
        )

    stmt = apply_deleted_filter(stmt, deleted_filter)

    if direction is None:
        direction = "desc"

    stmt = _apply_sql_sort(stmt, sort, direction)
    offset = (page - 1) * limit
    stmt = stmt.offset(offset).limit(limit + 1)
    result = await session.execute(stmt)
    organizations: list[Organization] = list(result.scalars().unique().all())
    has_more = len(organizations) > limit
    if has_more:
        organizations = organizations[:limit]

    is_htmx_table_request = request.headers.get("HX-Target") == "org-list"

    if is_htmx_table_request:
        with list_view.render_table_only(
            request,
            organizations,
            status_filter,
            page,
            has_more,
            sort,
            direction,
        ):
            pass
    else:
        status_counts = await list_view.get_status_counts(deleted_filter)
        countries = await list_view.get_distinct_countries()
        with layout(
            request,
            [("Organizations", str(request.url))],
            "organizations:list",
        ):
            with list_view.render(
                request,
                organizations,
                status_filter,
                status_counts,
                page,
                has_more,
                sort,
                direction,
                countries,
                country,
                selected_q=q,
                selected_days_in_status=days_in_status,
                selected_deleted=deleted_filter,
            ):
                pass


@router.get("/{organization_id}", name="organizations:detail")
async def get_organization_detail(
    request: Request,
    organization_id: UUID4,
    section: str = Query("overview"),
    session: AsyncSession = Depends(get_db_session),
) -> None:
    """
    Organization detail view with three-column layout.

    Features:
    - Left sidebar: Section navigation
    - Main content: Current section details
    - Right sidebar: Contextual actions and metadata
    """
    repository = OrganizationRepository(session)

    # Fetch organization with relationships
    stmt = (
        select(Organization)
        .options(
            joinedload(Organization.account),
            joinedload(Organization.payout_account),
        )
        .where(Organization.id == organization_id)
    )

    result = await session.execute(stmt)
    organization = result.scalars().unique().one_or_none()

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Fetch members separately
    members_stmt = (
        select(UserOrganization)
        .join(User, User.id == UserOrganization.user_id)
        .options(contains_eager(UserOrganization.user))
        .where(
            UserOrganization.organization_id == organization_id,
            UserOrganization.is_deleted.is_(False),
            User.is_deleted.is_(False),
        )
    )
    members_result = await session.execute(members_stmt)
    members = list(members_result.scalars().unique().all())
    organization.members = members  # type: ignore[attr-defined]

    # Fetch owner user email for Plain search
    owner_user = await repository.get_owner_user(organization)
    owner_email = owner_user.email if owner_user else None

    # Determine impersonation target: owner, or first member as fallback
    impersonate_user = owner_user
    if not impersonate_user and members:
        impersonate_user = members[0].user

    # Create views
    detail_view = OrganizationDetailView(
        organization,
        owner_email=owner_email,
        impersonate_user=impersonate_user,
    )

    # Fetch analytics data for overview section
    setup_data = None
    payment_stats = None
    orders_count = 0
    unrefunded_orders_count = 0
    if section == "overview":
        setup_analytics = OrganizationSetupAnalyticsService(session)
        payment_analytics = PaymentAnalyticsService(session)

        # Get setup metrics
        checkout_links_count = await setup_analytics.get_checkout_links_count(
            organization_id
        )
        webhooks_count = await setup_analytics.get_webhooks_count(organization_id)
        api_keys_count = await setup_analytics.get_organization_tokens_count(
            organization_id
        )
        products_count = await setup_analytics.get_products_count(organization_id)
        benefits_count = 0
        enabled_benefits_count = 0

        payouts_enabled = await setup_analytics.check_payout_account_enabled(
            organization
        )
        payment_ready = organization.can_accept_payments

        setup_score = OrganizationSetupAnalyticsService.calculate_setup_score(
            checkout_links_count,
            webhooks_count,
            api_keys_count,
            products_count,
            benefits_count,
            payouts_enabled,
        )

        # Calculate total transfer sum (balance transactions)
        total_transfer_sum = await transaction_service.get_transactions_sum(
            session, organization.account_id, type=TransactionType.balance
        )

        setup_data = {
            "setup_score": setup_score,
            "checkout_links_count": checkout_links_count,
            "webhooks_count": webhooks_count,
            "api_keys_count": api_keys_count,
            "products_count": products_count,
            "benefits_count": benefits_count,
            "enabled_benefits_count": enabled_benefits_count,
            "payouts_enabled": payouts_enabled,
            "payment_ready": payment_ready,
            "total_transfer_sum": total_transfer_sum,
        }

        # Get payment metrics
        (
            payment_count,
            total_amount,
        ) = await payment_analytics.get_succeeded_payments_stats(organization_id)
        account_balance = await transaction_service.get_transactions_sum(
            session, organization.account_id
        )
        refunds_count, refunds_amount = await payment_analytics.get_refund_stats(
            organization_id
        )
        failed_count = await payment_analytics.get_failed_payments_count(
            organization_id
        )
        risk_scores = await payment_analytics.get_risk_scores(organization_id)
        (
            dispute_count,
            dispute_amount,
            chargeback_count,
            chargeback_amount,
        ) = await payment_analytics.get_dispute_stats(organization_id)

        total_attempts = payment_count + failed_count
        auth_rate = (
            (payment_count / total_attempts * 100) if total_attempts > 0 else 100.0
        )
        refund_rate = (refunds_count / payment_count * 100) if payment_count > 0 else 0
        dispute_rate = (dispute_count / payment_count * 100) if payment_count > 0 else 0
        chargeback_rate = (
            (chargeback_count / payment_count * 100) if payment_count > 0 else 0
        )

        p50_risk, p90_risk = payment_analytics.calculate_risk_percentiles(risk_scores)

        payment_stats = {
            "payment_count": payment_count,
            "total_amount": total_amount,
            "total_net_amount": total_transfer_sum,
            "account_balance": account_balance,
            "refunds_count": refunds_count,
            "refunds_amount": refunds_amount,
            "refund_rate": refund_rate,
            "auth_rate": auth_rate,
            "failed_count": failed_count,
            "dispute_count": dispute_count,
            "dispute_amount": dispute_amount,
            "dispute_rate": dispute_rate,
            "chargeback_count": chargeback_count,
            "chargeback_amount": chargeback_amount,
            "chargeback_rate": chargeback_rate,
            "p50_risk": p50_risk,
            "p90_risk": p90_risk,
            "risk_scores_count": len(risk_scores),
        }

        orders_count, unrefunded_orders_count = await count_test_sales(
            session, organization_id
        )

    # Render based on section
    with layout(
        request,
        [
            ("Organizations", str(request.url_for("organizations:list"))),
            (organization.name, str(request.url)),
        ],
        "organizations:detail",
    ):
        with detail_view.render(request, section):
            # Render section content
            if section == "overview":
                overview = OverviewSection(
                    organization,
                    orders_count=orders_count,
                    unrefunded_orders_count=unrefunded_orders_count,
                )
                with overview.render(
                    request, setup_data=setup_data, payment_stats=payment_stats
                ):
                    pass
            elif section == "team":
                team_section = TeamSection(organization)
                with team_section.render(request):
                    pass
            elif section == "account":
                account_credits: Sequence[AccountCredit] = []
                credit_repository = AccountCreditRepository.from_session(session)
                account_credits = await credit_repository.get_all_by_account(
                    organization.account.id
                )
                account_section = AccountSection(
                    organization,
                    credits=account_credits,
                )
                payout_account_section = PayoutAccountSection(organization)
                with tag.div(classes="space-y-6"):
                    with account_section.render(request):
                        pass
                    with payout_account_section.render(request):
                        pass
            elif section == "history":
                # TODO: Implement history section
                with tag.div():
                    text("History section coming soon...")
            elif section == "settings":
                settings_section = SettingsSection(organization)
                with settings_section.render(request):
                    pass
            else:
                with tag.div():
                    text(f"Unknown section: {section}")


@router.post(
    "/{organization_id}/approve-dialog",
    name="organizations:approve_dialog",
    response_model=None,
)
async def approve_dialog(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse:
    """Approve organization — set status to ACTIVE."""
    repository = OrganizationRepository(session)
    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    organization.set_status(OrganizationStatus.ACTIVE)
    session.add(organization)

    return HXRedirectResponse(
        request,
        str(request.url_for("organizations:detail", organization_id=organization_id)),
        303,
    )


@router.api_route(
    "/{organization_id}/deny-dialog",
    name="organizations:deny_dialog",
    methods=["GET", "POST"],
    response_model=None,
)
async def deny_dialog(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Deny organization dialog and action."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    error_message: str | None = None

    if request.method == "POST":
        form_data = await request.form()
        reason = str(form_data.get("reason", "")).strip() or None

        if not reason:
            error_message = "A reason is required when denying an organization."
        else:
            organization.set_status(OrganizationStatus.DENIED)
            note = f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}] Organization denied."
            if reason:
                note += f"\nReason: {reason}"
            organization.internal_notes = (
                f"{organization.internal_notes}\n\n{note}"
                if organization.internal_notes
                else note
            )
            session.add(organization)

            return HXRedirectResponse(
                request,
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                ),
                303,
            )

    with modal("Deny Organization", open=True):
        with tag.form(
            hx_post=str(
                request.url_for(
                    "organizations:deny_dialog",
                    organization_id=organization_id,
                )
            ),
            hx_target="#modal",
            classes="flex flex-col gap-4",
        ):
            if error_message:
                with tag.div(classes="alert alert-error"):
                    text(error_message)

            with tag.p(classes="font-semibold text-error"):
                text("Warning: Payments will be blocked")

            with tag.div(classes="form-control"):
                with tag.label(classes="label"):
                    with tag.span(classes="label-text"):
                        text("Reason for denial (required)")
                with tag.textarea(
                    name="reason",
                    classes="textarea textarea-bordered w-full",
                    placeholder="Why are you denying this organization?",
                    rows="3",
                    required=True,
                ):
                    pass

            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(variant="error", type="submit"):
                    text("Deny Organization")

    return None


@router.api_route(
    "/{organization_id}/approve-denied-dialog",
    name="organizations:approve_denied_dialog",
    methods=["GET", "POST"],
    response_model=None,
)
async def approve_denied_dialog(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
    user_session: UserSession = Depends(get_admin),
) -> HXRedirectResponse | None:
    """Approve a denied organization dialog and action."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    if organization.status not in (
        OrganizationStatus.DENIED,
        OrganizationStatus.BLOCKED,
    ):
        return HXRedirectResponse(
            request,
            str(
                request.url_for("organizations:detail", organization_id=organization_id)
            ),
            303,
        )

    error_message: str | None = None

    if request.method == "POST":
        data = await request.form()
        reason = str(data.get("reason", "")).strip() or None

        if not reason:
            error_message = (
                "A reason is required when reactivating a denied organization."
            )
        else:
            organization.set_status(OrganizationStatus.ACTIVE)
            note = f"[{datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}] Organization approved."
            note += f"\nReason: {reason}"
            organization.internal_notes = (
                f"{organization.internal_notes}\n\n{note}"
                if organization.internal_notes
                else note
            )
            session.add(organization)

            return HXRedirectResponse(
                request,
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                ),
                303,
            )

    with modal("Approve Denied Organization", open=True):
        with tag.form(
            hx_post=str(
                request.url_for(
                    "organizations:approve_denied_dialog",
                    organization_id=organization_id,
                )
            ),
            hx_target="#modal",
            classes="flex flex-col gap-4",
        ):
            if error_message:
                with tag.div(classes="alert alert-error"):
                    text(error_message)

            with tag.p(classes="font-semibold"):
                text("Approve this previously denied organization")

            with tag.div(classes="form-control"):
                with tag.label(classes="label"):
                    with tag.span(classes="label-text"):
                        text("Reason for reactivation (required)")
                with tag.textarea(
                    name="reason",
                    classes="textarea textarea-bordered w-full",
                    placeholder="Why are you reactivating this denied organization?",
                    rows="3",
                    required=True,
                ):
                    pass

            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(variant="primary", type="submit"):
                    text("Approve Organization")

    return None


@router.api_route(
    "/{organization_id}/unblock-approve-dialog",
    name="organizations:unblock_approve_dialog",
    methods=["GET", "POST"],
    response_model=None,
)
async def unblock_approve_dialog(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
    user_session: UserSession = Depends(get_admin),
) -> HXRedirectResponse | None:
    """Unblock and approve organization dialog and action."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    error_message: str | None = None

    if request.method == "POST":
        data = await request.form()
        override_reason = str(data.get("override_reason", "")).strip() or None

        if not override_reason:
            error_message = "A reason is required when unblocking an organization."
        else:
            await organization_service.backoffice_approve(
                session, organization, reason=override_reason
            )

            return HXRedirectResponse(
                request,
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                ),
                303,
            )

    with modal("Unblock & Approve Organization", open=True):
        with tag.form(
            hx_post=str(
                request.url_for(
                    "organizations:unblock_approve_dialog",
                    organization_id=organization_id,
                )
            ),
            hx_target="#modal",
            classes="flex flex-col gap-4",
        ):
            if error_message:
                with tag.div(classes="alert alert-error"):
                    text(error_message)

            with tag.p(classes="font-semibold"):
                text("Unblock and approve this organization")

            with tag.div(classes="bg-base-200 p-4 rounded-lg"):
                with tag.p(classes="mb-3"):
                    text(
                        "This will unblock the organization and set it to ACTIVE status. "
                        "The organization will be able to receive payments again."
                    )

            with tag.div(classes="form-control"):
                with tag.label(classes="label"):
                    with tag.span(classes="label-text"):
                        text("Reason for unblocking (required)")
                with tag.textarea(
                    name="override_reason",
                    classes="textarea textarea-bordered w-full",
                    placeholder="Why are you unblocking this organization?",
                    rows="3",
                    required=True,
                ):
                    pass

            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(variant="primary", type="submit"):
                    text("Unblock & Approve")

    return None


@router.api_route(
    "/{organization_id}/block-dialog",
    name="organizations:block_dialog",
    methods=["GET", "POST"],
    response_model=None,
)
async def block_dialog(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Block organization dialog and action."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    if request.method == "POST":
        await organization_service.block_organization(session, organization)

        return HXRedirectResponse(
            request,
            str(
                request.url_for("organizations:detail", organization_id=organization_id)
            ),
            303,
        )

    with modal("Block Organization", open=True):
        with tag.div(classes="flex flex-col gap-4"):
            with tag.p(classes="font-semibold text-error"):
                text("⚠️ Critical Warning: Complete Organization Block")

            with tag.div(classes="bg-error/10 border border-error/20 p-4 rounded-lg"):
                with tag.p(classes="font-semibold mb-2 text-error"):
                    text("Blocking this organization will:")
                with tag.ul(classes="list-disc list-inside space-y-1 text-sm"):
                    with tag.li():
                        text("Prevent all access to the organization")
                    with tag.li():
                        text("Block all payments and transactions")
                    with tag.li():
                        text("Disable API access")
                    with tag.li():
                        text("Prevent any organization operations")

                with tag.p(classes="mt-3 text-sm font-semibold"):
                    text(
                        "This is a severe action typically used for fraud or ToS violations."
                    )

            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with tag.form(
                    hx_post=str(
                        request.url_for(
                            "organizations:block_dialog",
                            organization_id=organization_id,
                        )
                    ),
                ):
                    with button(variant="error", type="submit"):
                        text("Block Organization")

    return None


@router.api_route(
    "/{organization_id}/review-ticket",
    name="organizations:create_review_ticket",
    methods=["GET", "POST"],
    response_model=None,
)
async def create_review_ticket(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    repository = OrganizationRepository.from_session(session)
    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    owner_user = await repository.get_owner_user(organization)
    if not owner_user:
        await add_toast(request, "No owner user found for this organization.", "error")
        return

    default_title = f"Review: {organization.name}"

    if request.method == "POST":
        form_data = await request.form()
        try:
            form = CreateReviewTicketForm.model_validate_form(form_data)
        except ValidationError as e:
            detail = [{"loc": err["loc"], "msg": err["msg"]} for err in e.errors()]
            raise HTTPException(status_code=400, detail=detail) from e

        title = form.title

        try:
            thread_id = await plain_service.create_manual_organization_thread(
                session, organization, owner_user, title
            )
        except AccountReviewThreadCreationError as e:
            logger.error(
                "Failed to create Plain review ticket",
                organization_id=str(organization_id),
                error=str(e),
            )
            await add_toast(request, f"Failed to create ticket: {e.message}", "error")
            return
        except Exception as e:
            error = repr(e)
            logger.exception(
                "Unexpected error creating Plain review ticket",
                organization_id=str(organization_id),
                error=error,
            )
            await add_toast(
                request,
                f"Failed to create ticket: {error[:200]}",
                "error",
            )
            return

        if not thread_id:
            await add_toast(
                request,
                "Plain integration is disabled; no ticket was created.",
                "error",
            )
            return

        with modal("Plain Ticket Created", open=True):
            with tag.div(classes="flex flex-col gap-4"):
                with tag.p():
                    text(f'Plain ticket "{title}" created successfully.')
                with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                    with tag.form(method="dialog"):
                        with button(ghost=True):
                            text("Close")
                    with tag.a(
                        href=plain_thread_url(thread_id),
                        target="_blank",
                        rel="noopener noreferrer",
                    ):
                        with button(variant="primary"):
                            text("Open Ticket")
        return

    with modal("Create Plain Ticket", open=True):
        with tag.form(
            hx_post=str(
                request.url_for(
                    "organizations:create_review_ticket",
                    organization_id=organization_id,
                )
            ),
            hx_target="#modal",
            classes="flex flex-col gap-4",
        ):
            with tag.div(classes="form-control"):
                with tag.label(classes="label"):
                    with tag.span(classes="label-text font-semibold"):
                        text("Ticket title")
                with tag.input(
                    type="text",
                    name="title",
                    value=default_title,
                    required="required",
                    maxlength="200",
                    classes="input input-bordered w-full",
                ):
                    pass

            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(variant="primary", type="submit"):
                    text("Create")


@router.api_route(
    "/{organization_id}/offboard-dialog",
    name="organizations:offboard_dialog",
    methods=["GET", "POST"],
    response_model=None,
)
async def offboard_dialog(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
    user_session: UserSession = Depends(get_admin),
) -> HXRedirectResponse | None:
    """Set organization to offboarding status dialog and action."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    if request.method == "POST":
        form_data = await request.form()
        reason = str(form_data.get("reason", "")).strip() or None

        await organization_service.set_organization_offboarding(
            session, organization, reason=reason
        )

        return HXRedirectResponse(
            request,
            str(
                request.url_for("organizations:detail", organization_id=organization_id)
            ),
            303,
        )

    with modal("Set Offboarding", open=True):
        with tag.form(
            hx_post=str(
                request.url_for(
                    "organizations:offboard_dialog",
                    organization_id=organization_id,
                )
            ),
            classes="flex flex-col gap-4",
        ):
            with tag.p(classes="font-semibold text-warning"):
                text("Set Organization to Offboarding")

            with tag.div(
                classes="bg-warning/10 border border-warning/20 p-4 rounded-lg"
            ):
                with tag.p(classes="font-semibold mb-2"):
                    text("This action will:")
                with tag.ul(classes="list-disc list-inside space-y-1 text-sm"):
                    with tag.li():
                        text("Change the organization status to Offboarding")
                    with tag.li():
                        text("Block payouts while the organization is offboarding")

            with tag.div(classes="form-control"):
                with tag.label(classes="label"):
                    with tag.span(classes="label-text"):
                        text("Reason for offboarding")
                with tag.textarea(
                    name="reason",
                    classes="textarea textarea-bordered w-full",
                    placeholder="Why is this organization being offboarded?",
                    rows="3",
                ):
                    pass

            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(variant="warning", type="submit"):
                    text("Set Offboarding")

    return None


@router.api_route(
    "/{organization_id}/edit",
    name="organizations:edit",
    methods=["GET", "POST"],
    response_model=None,
)
async def edit_organization(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Edit organization details."""
    repository = OrganizationRepository(session)

    # Fetch organization
    organization = await repository.get_by_id(
        organization_id, include_blocked=True, include_deleted=True
    )
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    validation_error = None

    if request.method == "POST":
        data = await request.form()
        try:
            form = UpdateOrganizationBasicForm.model_validate_form(data)
            if form.slug != organization.slug:
                existing_slug = await repository.get_by_slug(
                    form.slug, include_deleted=True
                )
                if existing_slug is not None and existing_slug.id != organization.id:
                    raise ValidationError.from_exception_data(
                        title="SlugAlreadyExists",
                        line_errors=[
                            {
                                "loc": ("slug",),
                                "type": PydanticCustomError(
                                    "SlugAlreadyExists",
                                    "An organization with this slug already exists.",
                                ),
                                "input": form.slug,
                            }
                        ],
                    )

            # Update organization with basic fields only
            form_dict = form.model_dump(exclude_none=True)
            organization = await repository.update(
                organization,
                update_dict=form_dict,
            )
            redirect_url = (
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                )
                + "?section=settings"
            )
            return HXRedirectResponse(request, redirect_url, 303)

        except ValidationError as e:
            validation_error = e

    # Prepare data for form rendering
    form_data = {
        "name": organization.name,
        "slug": organization.slug,
    }

    with modal("Edit Basic Settings", open=True):
        with tag.p(classes="text-sm text-base-content/60 mb-4"):
            text("Update organization name and slug")

        with UpdateOrganizationBasicForm.render(
            data=form_data,
            validation_error=validation_error,
            hx_post=str(
                request.url_for("organizations:edit", organization_id=organization_id)
            ),
            hx_target="#modal",
            classes="space-y-4",
        ):
            # Action buttons
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Save Changes")

    return None


@router.api_route(
    "/{organization_id}/edit-details",
    name="organizations:edit_details",
    methods=["GET", "POST"],
    response_model=None,
)
async def edit_details(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Edit organization details (about, product description, intended use)."""
    repository = OrganizationRepository(session)

    # Fetch organization
    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    validation_error = None

    if request.method == "POST":
        try:
            data = await request.form()
            form = UpdateOrganizationDetailsForm.model_validate_form(data)

            # Update organization with form data
            form_dict = form.model_dump(exclude_none=True)
            organization = await repository.update(
                organization,
                update_dict=form_dict,
            )
            redirect_url = (
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                )
                + "?section=settings"
            )
            return HXRedirectResponse(request, redirect_url, 303)

        except ValidationError as e:
            validation_error = e

    # Prepare data for form rendering
    form_data = {
        "email": organization.email,
        "website": organization.website,
        "details": organization.details or {},
    }

    with modal("Edit Organization Details", open=True):
        with tag.p(classes="text-sm text-base-content/60 mb-4"):
            text("Update organization email, website, and details")

        with UpdateOrganizationDetailsForm.render(
            data=form_data,
            validation_error=validation_error,
            hx_post=str(
                request.url_for(
                    "organizations:edit_details", organization_id=organization_id
                )
            ),
            hx_target="#modal",
            classes="space-y-4",
        ):
            # Action buttons
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Save Changes")

    return None


@router.api_route(
    "/{organization_id}/edit-rate-limit-group",
    name="organizations:edit_rate_limit_group",
    methods=["GET", "POST"],
    response_model=None,
)
async def edit_rate_limit_group(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Edit organization rate limit group."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    validation_error: ValidationError | None = None

    if request.method == "POST":
        data = await request.form()
        try:
            form = UpdateRateLimitGroupForm.model_validate_form(data)
            await repository.update(
                organization,
                update_dict={"rate_limit_group": form.rate_limit_group},
            )
            return HXRedirectResponse(
                request,
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                )
                + "?section=settings",
                303,
            )
        except ValidationError as e:
            validation_error = e

    with modal("Edit Rate Limit Group", open=True):
        with tag.p(classes="text-sm text-base-content/60 mb-4"):
            text("Configure which rate limit group applies to this organization")

        with UpdateRateLimitGroupForm.render(
            data={"rate_limit_group": organization.rate_limit_group.value},
            validation_error=validation_error,
            hx_post=str(
                request.url_for(
                    "organizations:edit_rate_limit_group",
                    organization_id=organization_id,
                )
            ),
            hx_target="#modal",
            classes="flex flex-col",
        ):
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(type="submit", variant="primary"):
                    text("Save Changes")

    return None


@router.api_route(
    "/{organization_id}/edit-socials",
    name="organizations:edit_socials",
    methods=["GET", "POST"],
    response_model=None,
)
async def edit_socials(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Edit organization social media links."""
    # Platform name constants for consistency
    PLATFORM_YOUTUBE = "youtube"
    PLATFORM_INSTAGRAM = "instagram"
    PLATFORM_LINKEDIN = "linkedin"
    PLATFORM_X = "x"
    PLATFORM_FACEBOOK = "facebook"
    PLATFORM_THREADS = "threads"
    PLATFORM_TIKTOK = "tiktok"
    PLATFORM_GITHUB = "github"
    PLATFORM_OTHER = "other"

    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    validation_error = None

    if request.method == "POST":
        try:
            data = await request.form()
            form = UpdateOrganizationSocialsForm.model_validate_form(data)

            # Build socials list from form data
            socials: list[dict[str, str]] = []
            if form.youtube_url:
                socials.append(
                    {"platform": PLATFORM_YOUTUBE, "url": str(form.youtube_url)}
                )
            if form.instagram_url:
                socials.append(
                    {"platform": PLATFORM_INSTAGRAM, "url": str(form.instagram_url)}
                )
            if form.linkedin_url:
                socials.append(
                    {"platform": PLATFORM_LINKEDIN, "url": str(form.linkedin_url)}
                )
            if form.x_url:
                socials.append({"platform": PLATFORM_X, "url": str(form.x_url)})
            if form.facebook_url:
                socials.append(
                    {"platform": PLATFORM_FACEBOOK, "url": str(form.facebook_url)}
                )
            if form.threads_url:
                socials.append(
                    {"platform": PLATFORM_THREADS, "url": str(form.threads_url)}
                )
            if form.tiktok_url:
                socials.append(
                    {"platform": PLATFORM_TIKTOK, "url": str(form.tiktok_url)}
                )
            if form.github_url:
                socials.append(
                    {"platform": PLATFORM_GITHUB, "url": str(form.github_url)}
                )
            if form.other_url:
                socials.append({"platform": PLATFORM_OTHER, "url": str(form.other_url)})

            # Update organization with new socials
            organization = await repository.update(
                organization,
                update_dict={"socials": socials},
            )
            redirect_url = (
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                )
                + "?section=settings"
            )
            return HXRedirectResponse(request, redirect_url, 303)

        except ValidationError as e:
            validation_error = e

    # Prepare data for form rendering - extract URLs from existing socials
    existing_socials = organization.socials or []
    form_data: dict[str, str | None] = {
        "youtube_url": None,
        "instagram_url": None,
        "linkedin_url": None,
        "x_url": None,
        "facebook_url": None,
        "threads_url": None,
        "tiktok_url": None,
        "github_url": None,
        "other_url": None,
    }
    for social in existing_socials:
        platform = social.get("platform", "").lower()
        url = social.get("url", "")
        if platform == PLATFORM_YOUTUBE:
            form_data["youtube_url"] = url
        elif platform == PLATFORM_INSTAGRAM:
            form_data["instagram_url"] = url
        elif platform == PLATFORM_LINKEDIN:
            form_data["linkedin_url"] = url
        elif platform == PLATFORM_X:
            form_data["x_url"] = url
        elif platform == PLATFORM_FACEBOOK:
            form_data["facebook_url"] = url
        elif platform == PLATFORM_THREADS:
            form_data["threads_url"] = url
        elif platform == PLATFORM_TIKTOK:
            form_data["tiktok_url"] = url
        elif platform == PLATFORM_GITHUB:
            form_data["github_url"] = url
        elif platform == PLATFORM_OTHER:
            form_data["other_url"] = url

    with modal("Edit Social Media Links", open=True):
        with tag.p(classes="text-sm text-base-content/60 mb-4"):
            text("Update organization social media links for creator outreach")

        with UpdateOrganizationSocialsForm.render(
            data=form_data,
            validation_error=validation_error,
            hx_post=str(
                request.url_for(
                    "organizations:edit_socials", organization_id=organization_id
                )
            ),
            hx_target="#modal",
            classes="space-y-4",
        ):
            # Action buttons
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Save Changes")

    return None


@router.api_route(
    "/{organization_id}/edit-features",
    name="organizations:edit_features",
    methods=["GET", "POST"],
    response_model=None,
)
async def edit_features(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Edit organization feature flags."""
    repository = OrganizationRepository(session)

    # Fetch organization
    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    validation_error = None

    if request.method == "POST":
        try:
            data = await request.form()

            # Parse boolean feature flags from form data
            feature_flags: dict[str, bool] = {}
            for (
                field_name,
                field_info,
            ) in OrganizationFeatureSettings.model_fields.items():
                if field_info.annotation is bool:
                    feature_flags[field_name] = field_name in data

            # Merge with existing feature_settings
            old_member_model = organization.feature_settings.get(
                "member_model_enabled", False
            )
            updated_feature_settings = {
                **organization.feature_settings,
                **feature_flags,
            }

            # Update organization
            organization = await repository.update(
                organization,
                update_dict={"feature_settings": updated_feature_settings},
            )

            # Trigger backfill when member_model transitions False → True
            new_member_model = updated_feature_settings.get(
                "member_model_enabled", False
            )
            if not old_member_model and new_member_model:
                enqueue_job(
                    "organization.backfill_members",
                    organization_id=organization.id,
                )
            redirect_url = (
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                )
                + "?section=settings"
            )
            return HXRedirectResponse(request, redirect_url, 303)

        except ValidationError as e:
            validation_error = e

    # Render feature flags form
    with modal("Edit Feature Flags", open=True):
        with tag.p(classes="text-sm text-base-content/60 mb-4"):
            text("Enable or disable feature flags for this organization")

        with tag.form(
            hx_post=str(
                request.url_for(
                    "organizations:edit_features", organization_id=organization_id
                )
            ),
            hx_target="#modal",
            classes="space-y-4",
        ):
            # Feature flags checkboxes (boolean fields only)
            with tag.div(classes="space-y-3"):
                for (
                    field_name,
                    field_info,
                ) in OrganizationFeatureSettings.model_fields.items():
                    if field_info.annotation is not bool:
                        continue
                    enabled = organization.feature_settings.get(field_name, False)
                    label = field_name.replace("_", " ").title()

                    with tag.div(classes="form-control"):
                        with tag.label(
                            classes="label cursor-pointer justify-start gap-3"
                        ):
                            with tag.input(
                                type="checkbox",
                                name=field_name,
                                classes="checkbox checkbox-sm",
                                **{"checked": ""} if enabled else {},
                            ):
                                pass
                            with tag.span(classes="label-text"):
                                text(label)

            # Action buttons
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Save Changes")

    return None


@router.api_route(
    "/{organization_id}/edit-checkout-settings",
    name="organizations:edit_checkout_settings",
    methods=["GET", "POST"],
    response_model=None,
)
async def edit_checkout_settings(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Edit organization checkout settings (e.g., require 3DS)."""
    repository = OrganizationRepository(session)

    # Fetch organization
    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    if request.method == "POST":
        data = await request.form()

        # Parse checkout settings from form data
        checkout_settings = {
            "require_3ds": "require_3ds" in data,
        }

        # Merge with existing checkout_settings
        updated_checkout_settings = {
            **organization.checkout_settings,
            **checkout_settings,
        }

        # Update organization
        await repository.update(
            organization,
            update_dict={"checkout_settings": updated_checkout_settings},
        )

        redirect_url = (
            str(
                request.url_for("organizations:detail", organization_id=organization_id)
            )
            + "?section=settings"
        )
        return HXRedirectResponse(request, redirect_url, 303)

    # Render checkout settings form
    require_3ds = organization.checkout_require_3ds

    with modal("Edit Checkout Settings", open=True):
        with tag.p(classes="text-sm text-base-content/60 mb-4"):
            text("Configure checkout behavior for this organization")

        with tag.form(
            hx_post=str(
                request.url_for(
                    "organizations:edit_checkout_settings",
                    organization_id=organization_id,
                )
            ),
            hx_target="#modal",
            classes="space-y-4",
        ):
            # Checkout settings checkboxes
            with tag.div(classes="space-y-3"):
                with tag.div(classes="form-control"):
                    with tag.label(classes="label cursor-pointer justify-start gap-3"):
                        with tag.input(
                            type="checkbox",
                            name="require_3ds",
                            classes="checkbox checkbox-sm",
                            **{"checked": ""} if require_3ds else {},
                        ):
                            pass
                        with tag.span(classes="label-text"):
                            text("Require 3DS on all checkouts")

            # Action buttons
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Save Changes")

    return None


@router.api_route(
    "/{organization_id}/edit-account-settings",
    name="organizations:edit_account_settings",
    methods=["GET", "POST"],
    response_model=None,
)
async def edit_account_settings(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Edit organization account settings (e.g., payout_transaction_delay)."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(
        organization_id,
        include_blocked=True,
        options=(joinedload(Organization.account),),
    )
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    validation_error: ValidationError | None = None

    if request.method == "POST":
        data = await request.form()
        try:
            form = UpdateAccountSettingsForm.model_validate_form(data)
            account_repository = AccountRepository.from_session(session)
            await account_repository.update(
                organization.account,
                update_dict={"payout_transaction_delay": form.payout_transaction_delay},
            )
            return HXRedirectResponse(
                request,
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                )
                + "?section=settings",
            )
        except ValidationError as e:
            validation_error = e

    timedelta_serializer = SchemaSerializer(core_schema.timedelta_schema())
    prefill_data = {
        "payout_transaction_delay": timedelta_serializer.to_python(
            organization.account.payout_transaction_delay, mode="json"
        )
    }

    with modal("Edit Account Settings", open=True):
        with UpdateAccountSettingsForm.render(
            data=prefill_data,
            hx_post=str(
                request.url_for(
                    "organizations:edit_account_settings",
                    organization_id=organization_id,
                )
            ),
            hx_target="#modal",
            validation_error=validation_error,
            classes="flex flex-col",
        ):
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(type="submit", variant="primary"):
                    text("Save Changes")

    return None


@router.api_route(
    "/{organization_id}/add-note",
    name="organizations:add_note",
    methods=["GET", "POST"],
    response_model=None,
)
async def add_note(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Add internal notes to an organization."""
    repository = OrganizationRepository(session)

    # Fetch organization
    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    validation_error = None

    if request.method == "POST":
        try:
            data = await request.form()
            form = UpdateOrganizationInternalNotesForm.model_validate_form(data)
            organization = await repository.update(
                organization, update_dict=form.model_dump(exclude_none=True)
            )
            return HXRedirectResponse(
                request,
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                ),
                303,
            )

        except ValidationError as e:
            validation_error = e

    with modal("Add Internal Notes", open=True):
        with tag.p(classes="text-sm text-base-content/60 mb-4"):
            text("Add internal notes about this organization (admin only)")

        with UpdateOrganizationInternalNotesForm.render(
            data=organization,
            validation_error=validation_error,
            hx_post=str(
                request.url_for(
                    "organizations:add_note", organization_id=organization_id
                )
            ),
            hx_target="#modal",
            classes="space-y-4",
        ):
            # Action buttons
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Save Notes")

    return None


@router.api_route(
    "/{organization_id}/edit-note",
    name="organizations:edit_note",
    methods=["GET", "POST"],
    response_model=None,
)
async def edit_note(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Edit internal notes for an organization."""
    repository = OrganizationRepository(session)

    # Fetch organization
    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    validation_error = None

    if request.method == "POST":
        try:
            data = await request.form()
            form = UpdateOrganizationInternalNotesForm.model_validate_form(data)
            organization = await repository.update(
                organization, update_dict=form.model_dump(exclude_none=True)
            )
            return HXRedirectResponse(
                request,
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                ),
                303,
            )

        except ValidationError as e:
            validation_error = e

    with modal("Edit Internal Notes", open=True):
        with tag.p(classes="text-sm text-base-content/60 mb-4"):
            text("Update internal notes about this organization (admin only)")

        with UpdateOrganizationInternalNotesForm.render(
            data=organization,
            validation_error=validation_error,
            hx_post=str(
                request.url_for(
                    "organizations:edit_note", organization_id=organization_id
                )
            ),
            hx_target="#modal",
            classes="space-y-4",
        ):
            # Action buttons
            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Save Notes")

    return None


@router.get(
    "/{organization_id}/impersonate/{user_id}",
    name="organizations:impersonate",
)
async def impersonate_user(
    request: Request,
    organization_id: UUID4,
    user_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse:
    """Impersonate a user by creating a read-only session for them."""
    from datetime import timedelta

    from polar.config import settings

    # Fetch the user to impersonate
    stmt = select(User).where(User.id == user_id)
    result = await session.execute(stmt)
    user = result.scalars().one_or_none()

    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Verify user belongs to organization
    membership_stmt = select(UserOrganization).where(
        UserOrganization.user_id == user_id,
        UserOrganization.organization_id == organization_id,
    )
    result = await session.execute(membership_stmt)
    if not result.scalars().one_or_none():
        raise HTTPException(
            status_code=400, detail="User is not a member of this organization"
        )

    # Create read-only impersonation session with time limit
    token, impersonation_session = await auth_service._create_user_session(
        session=session,
        user=user,
        user_agent=request.headers.get("User-Agent", ""),
        scopes=list(READ_ONLY_SCOPES),
        expire_in=timedelta(minutes=60),  # Time-limited
    )

    # Get user's first organization for redirect
    repository = OrganizationRepository(session)
    user_orgs = await repository.get_all_by_user(user.id)
    redirect_url = f"/{user_orgs[0].slug}" if user_orgs else "/"

    response = HXRedirectResponse(request, redirect_url, 303)

    admin_token = request.cookies.get(
        settings.IMPERSONATION_COOKIE_KEY
    ) or request.cookies.get(settings.USER_SESSION_COOKIE_KEY)

    # Preserve admin session in impersonation cookie
    if admin_token:
        response.set_cookie(
            settings.IMPERSONATION_COOKIE_KEY,
            value=admin_token,
            expires=impersonation_session.expires_at,
            path="/",
            domain=settings.USER_SESSION_COOKIE_DOMAIN,
            secure=request.url.hostname not in ["127.0.0.1", "localhost"],
            httponly=True,
            samesite="lax",
        )

    # Set impersonated session cookie
    response = auth_service._set_user_session_cookie(
        request, response, token, impersonation_session.expires_at
    )

    # Set impersonation indicator (JS-readable for UI)
    response.set_cookie(
        settings.IMPERSONATION_INDICATOR_COOKIE_KEY,
        value="true",
        expires=impersonation_session.expires_at,
        path="/",
        domain=settings.USER_SESSION_COOKIE_DOMAIN,
        secure=request.url.hostname not in ["127.0.0.1", "localhost"],
        httponly=False,  # JS-readable for UI banner
        samesite="lax",
    )

    return response


@router.post(
    "/{organization_id}/make-owner/{user_id}",
    name="organizations:make_owner",
)
async def make_owner(
    request: Request,
    organization_id: UUID4,
    user_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse:
    """Make a user the owner of the organization."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    try:
        await organization_service.change_owner(
            session, new_owner_id=user_id, organization_id=organization_id
        )
    except Exception as e:
        logger.error("Failed to make user owner", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    redirect_url = (
        str(request.url_for("organizations:detail", organization_id=organization_id))
        + "?section=team"
    )
    return HXRedirectResponse(request, redirect_url, 303)


@router.delete(
    "/{organization_id}/remove-member/{user_id}",
    name="organizations:remove_member",
)
async def remove_member(
    request: Request,
    organization_id: UUID4,
    user_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse:
    """Remove a member from the organization."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Remove the user from the organization
    try:
        from polar.user_organization.service import (
            user_organization as user_organization_service,
        )

        await user_organization_service.remove_member(
            session,
            user_id=user_id,
            organization_id=organization.id,
        )
    except Exception as e:
        logger.error("Failed to remove member", error=str(e))
        raise HTTPException(status_code=400, detail=str(e))

    redirect_url = (
        str(request.url_for("organizations:detail", organization_id=organization_id))
        + "?section=team"
    )
    return HXRedirectResponse(request, redirect_url, 303)


@router.api_route(
    "/{organization_id}/delete-dialog",
    name="organizations:delete_dialog",
    methods=["GET", "POST"],
    response_model=None,
)
async def delete_dialog(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Delete organization dialog and action."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    if request.method == "POST":
        await organization_service.delete(session, organization)

        return HXRedirectResponse(
            request,
            str(request.url_for("organizations:list")),
            303,
        )

    with modal(f"Delete Organization {organization.name}", open=True):
        with tag.div(classes="flex flex-col gap-4"):
            with tag.p(classes="font-semibold text-error"):
                text("Are you sure you want to delete this organization?")

            with tag.div(classes="bg-base-200 p-4 rounded-lg"):
                with tag.p(classes="font-semibold mb-2"):
                    text("Deleting this organization DOES NOT:")
                with tag.ul(classes="list-disc list-inside space-y-1 text-sm"):
                    with tag.li():
                        text("Delete or anonymize users")
                    with tag.li():
                        text("Delete or anonymize the account")
                    with tag.li():
                        text(
                            "Delete customers, products, discounts, benefits, or checkouts"
                        )
                    with tag.li():
                        text("Revoke granted benefits")
                    with tag.li():
                        text("Remove API tokens")

            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with tag.form(
                    hx_post=str(
                        request.url_for(
                            "organizations:delete_dialog",
                            organization_id=organization_id,
                        )
                    ),
                ):
                    with button(variant="error", type="submit"):
                        text("Delete Organization")

    return None


@router.api_route(
    "/{organization_id}/setup-account",
    name="organizations:setup_account",
    methods=["GET", "POST"],
    response_model=None,
)
async def setup_account(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Show modal to setup a manual payment account."""
    repository = OrganizationRepository(session)

    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    if request.method == "POST":
        data = await request.form()
        country = str(data.get("country", "US")).upper()

        owner_user = await repository.get_owner_user(organization)
        if owner_user is None:
            raise HTTPException(status_code=400, detail="Organization has no owner")

        await payout_account_service.create_manual_account(
            session, organization, owner_user, country=country, currency="usd"
        )

        await add_toast(
            request,
            f"Manual payout account created for {organization.name}",
            "success",
        )

        redirect_url = (
            str(
                request.url_for("organizations:detail", organization_id=organization_id)
            )
            + "?section=account"
        )
        return HXRedirectResponse(request, redirect_url, 303)

    # GET - Show modal
    form_action = str(
        request.url_for("organizations:setup_account", organization_id=organization_id)
    )
    with modal("Setup Manual Account", open=True):
        with tag.form(
            method="post",
            hx_post=form_action,
            hx_target="#modal",
            classes="space-y-4",
        ):
            with tag.p(classes="text-sm text-base-content/60"):
                text("This will create a manual payout account for this organization.")

            with tag.div(classes="alert alert-warning"):
                with tag.span(classes="text-sm"):
                    text(
                        "Manual accounts require manual payout processing and do not integrate with Stripe."
                    )

            with tag.div(classes="form-control w-full"):
                with tag.label(classes="label"):
                    with tag.span(classes="label-text font-semibold"):
                        text("Country")
                with tag.input(
                    type="text",
                    name="country",
                    value="US",
                    placeholder="2-letter country code (e.g. US, FR, GB)",
                    maxlength="2",
                    classes="input input-bordered w-full uppercase",
                    required=True,
                ):
                    pass

            with tag.div(classes="modal-action pt-6 border-t border-base-200"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(variant="primary", type="submit"):
                    text("Create Manual Account")

    return None


@router.api_route(
    "/{organization_id}/delete-payout-account",
    name="organizations:delete_payout_account",
    methods=["GET", "POST"],
    response_model=None,
)
async def delete_payout_account(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Show modal to confirm and process payout account deletion."""
    repository = OrganizationRepository(session)
    organization = await repository.get_by_id_with_payout_account(organization_id)

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    if not organization.payout_account:
        raise HTTPException(
            status_code=400, detail="Organization has no payout account"
        )

    payout_account = organization.payout_account
    validation_error = None

    if request.method == "POST":
        data = await request.form()
        try:
            form = DeletePayoutAccountForm.model_validate_form(data)

            account_type = payout_account.type

            await payout_account_service.delete(session, payout_account)

            timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
            delete_note = (
                f"[{timestamp}] Payout account deleted.\nType: {account_type.value}\n"
            )
            delete_note += f"Reason: {form.reason.strip()}"

            if organization.internal_notes:
                organization.internal_notes = (
                    f"{organization.internal_notes}\n\n{delete_note}"
                )
            else:
                organization.internal_notes = delete_note

            session.add(organization)

            logger.info(
                "Payout account deleted from organization",
                organization_id=str(organization_id),
                account_type=account_type.value,
            )

            redirect_url = (
                str(
                    request.url_for(
                        "organizations:detail", organization_id=organization_id
                    )
                )
                + "?section=account"
            )
            return HXRedirectResponse(request, redirect_url, 303)

        except ValidationError as e:
            validation_error = e

    form_action = str(
        request.url_for(
            "organizations:delete_payout_account",
            organization_id=organization_id,
        )
    )
    modal_view = DeletePayoutAccountModal(payout_account, form_action, validation_error)
    with modal_view.render():
        pass

    return None


# =============================================================================
# Fee Credit Management Endpoints
# =============================================================================


@router.api_route(
    "/{organization_id}/grant-credit",
    name="organizations:grant_credit",
    methods=["GET", "POST"],
    response_model=None,
)
async def grant_credit(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Grant fee credits to an organization's account."""
    from datetime import datetime

    repository = OrganizationRepository(session)
    organization = await repository.get_by_id(
        organization_id, options=(joinedload(Organization.account),)
    )

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    if request.method == "POST":
        form_data = await request.form()

        # Parse title (required)
        title = str(form_data.get("title", "")).strip()
        if not title:
            await add_toast(request, "Title is required", "error")
            return None

        # Parse amount (convert dollars to cents)
        try:
            amount_str = form_data.get("amount", "0")
            amount_dollars = float(str(amount_str))
            amount_cents = int(amount_dollars * 100)
        except (ValueError, TypeError):
            amount_dollars = 0
            amount_cents = 0

        if amount_cents <= 0:
            await add_toast(request, "Amount must be greater than 0", "error")
            return None

        # Parse optional expiration date
        expires_at = None
        expires_str = form_data.get("expires_at")
        if expires_str:
            try:
                expires_at = datetime.fromisoformat(str(expires_str))
            except ValueError:
                pass

        notes = form_data.get("notes") or None

        # Create the credit
        await account_credit_service.grant(
            session,
            account=organization.account,
            amount=amount_cents,
            title=title,
            expires_at=expires_at,
            notes=str(notes) if notes else None,
        )

        await add_toast(
            request,
            f"Granted ${amount_dollars:.2f} in fee credits",
            "success",
        )

        return HXRedirectResponse(
            request,
            str(
                request.url_for("organizations:detail", organization_id=organization_id)
            )
            + "?section=account",
        )

    # GET: Show modal form
    with modal("Grant Fee Credit", open=True):
        with tag.form(
            hx_post=str(
                request.url_for(
                    "organizations:grant_credit", organization_id=organization_id
                )
            ),
        ):
            with tag.div(classes="space-y-4"):
                # Title field
                with tag.div():
                    with tag.label(classes="label"):
                        with tag.span(classes="label-text"):
                            text("Title")
                    with tag.input(
                        type="text",
                        name="title",
                        placeholder="Fee Credit",
                        classes="input input-bordered w-full",
                        required=True,
                    ):
                        pass
                    with tag.div(classes="text-xs text-base-content/60 mt-1"):
                        text("Public title shown to the customer")

                # Amount field
                with tag.div():
                    with tag.label(classes="label"):
                        with tag.span(classes="label-text"):
                            text("Amount (USD)")
                    with tag.input(
                        type="number",
                        name="amount",
                        step="0.01",
                        min="0.01",
                        placeholder="100.00",
                        classes="input input-bordered w-full",
                        required=True,
                    ):
                        pass
                    with tag.div(classes="text-xs text-base-content/60 mt-1"):
                        text("Enter amount in dollars (e.g., 100.00 for $100)")

                # Expiration date field
                with tag.div():
                    with tag.label(classes="label"):
                        with tag.span(classes="label-text"):
                            text("Expires At (optional)")
                    with tag.input(
                        type="datetime-local",
                        name="expires_at",
                        classes="input input-bordered w-full",
                    ):
                        pass

                # Notes field
                with tag.div():
                    with tag.label(classes="label"):
                        with tag.span(classes="label-text"):
                            text("Notes (optional)")
                    with tag.textarea(
                        name="notes",
                        placeholder="Reason for granting credit...",
                        classes="textarea textarea-bordered w-full",
                        rows="2",
                    ):
                        pass

            # Action buttons
            with tag.div(classes="modal-action"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(variant="primary", type="submit"):
                    text("Grant Credit")

    return None


@router.api_route(
    "/{organization_id}/credits/{credit_id}/revoke",
    name="organizations:revoke_credit",
    methods=["GET", "POST"],
    response_model=None,
)
async def revoke_credit(
    request: Request,
    organization_id: UUID4,
    credit_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> HXRedirectResponse | None:
    """Revoke a fee credit."""
    repository = OrganizationRepository(session)
    organization = await repository.get_by_id(
        organization_id, options=(joinedload(Organization.account),)
    )

    if not organization:
        raise HTTPException(status_code=404, detail="Organization not found")

    # Get the credit
    credit_repository = AccountCreditRepository.from_session(session)
    credit = await credit_repository.get_by_id_and_account(
        credit_id, organization.account.id
    )

    if not credit:
        raise HTTPException(status_code=404, detail="Credit not found")

    if request.method == "POST":
        from polar.account_credit.service import CreditAlreadyRevokedError

        try:
            await account_credit_service.revoke(
                session, credit, account=organization.account
            )
            await add_toast(request, "Credit has been revoked", "success")
        except CreditAlreadyRevokedError:
            await add_toast(request, "Credit was already revoked", "warning")

        return HXRedirectResponse(
            request,
            str(
                request.url_for("organizations:detail", organization_id=organization_id)
            )
            + "?section=account",
        )

    # GET: Show confirmation modal
    remaining = credit.remaining
    with modal("Revoke Credit", open=True):
        with tag.div(classes="space-y-4"):
            with tag.p():
                text("Are you sure you want to revoke this credit?")

            with tag.div(classes="bg-base-200 p-4 rounded-lg"):
                with tag.div(classes="grid grid-cols-2 gap-2 text-sm"):
                    with tag.div(classes="text-base-content/60"):
                        text("Original Amount:")
                    with tag.div(classes="font-semibold"):
                        text(f"${credit.amount / 100:.2f}")
                    with tag.div(classes="text-base-content/60"):
                        text("Remaining Balance:")
                    with tag.div(classes="font-semibold text-error"):
                        text(f"${remaining / 100:.2f}")

            if remaining > 0:
                with tag.div(classes="alert alert-warning"):
                    with tag.span():
                        text(
                            f"This credit still has ${remaining / 100:.2f} remaining. "
                            "Revoking it will prevent further use."
                        )

        with tag.div(classes="modal-action"):
            with tag.form(method="dialog"):
                with button(ghost=True):
                    text("Cancel")
            with button(
                variant="error",
                hx_post=str(
                    request.url_for(
                        "organizations:revoke_credit",
                        organization_id=organization_id,
                        credit_id=credit_id,
                    )
                ),
            ):
                text("Revoke Credit")

    return None


@router.api_route(
    "/{organization_id}/import-orders",
    name="organizations:import_orders",
    methods=["GET", "POST"],
    dependencies=[Depends(get_admin)],
)
async def import_orders(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    repository = OrganizationRepository.from_session(session)
    organization = await repository.get_by_id(organization_id, include_deleted=True)

    if organization is None:
        raise HTTPException(status_code=404)

    validation_error: ValidationError | None = None
    if request.method == "POST":
        data = await request.form()
        try:
            form = OrganizationOrdersImportForm.model_validate_form(data)
            return EventSourceResponse(
                orders_import_sse(
                    session,
                    organization,
                    form.file,
                )
            )
        except ValidationError as e:
            validation_error = e

    with modal("Import Orders", open=True):
        with OrganizationOrdersImportForm.render(
            {},
            action=str(request.url),
            method="POST",
            classes="flex flex-col",
            validation_error=validation_error,
            _="on submit halt the event then call formPostSSE(me, '#import-progress')",
        ):
            with tag.div(id="import-progress"):
                pass
            with tag.div(classes="modal-action"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Import")


@router.api_route(
    "/{organization_id}/add-payment-method-domain",
    name="organizations:add_payment_method_domain",
    methods=["GET", "POST"],
    dependencies=[Depends(get_admin)],
)
async def add_payment_method_domain(
    request: Request,
    organization_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    repository = OrganizationRepository.from_session(session)
    organization = await repository.get_by_id(organization_id)

    if organization is None:
        raise HTTPException(status_code=404)

    validation_error: ValidationError | None = None
    if request.method == "POST":
        data = await request.form()
        try:
            form = AddPaymentMethodDomainForm.model_validate_form(data)

            # Payment method domain allowlist (Stripe) removed
            await add_toast(
                request,
                "Payment method domain allowlist is not available (Stripe removed).",
                variant="error",
            )
            return

        except ValidationError as e:
            validation_error = e

    with modal("Add Domain to Allowlist", open=True):
        with tag.p(classes="text-sm text-base-content-secondary mb-4"):
            text(
                "Add a custom domain to the Apple Pay / Google Pay allowlist. "
                "This allows these payment methods to appear in embeds on the specified domain."
            )

        with AddPaymentMethodDomainForm.render(
            {},
            hx_post=str(request.url),
            hx_target="#modal",
            classes="flex flex-col gap-4",
            validation_error=validation_error,
        ):
            with tag.div(classes="modal-action"):
                with tag.form(method="dialog"):
                    with button(ghost=True):
                        text("Cancel")
                with button(
                    type="submit",
                    variant="primary",
                ):
                    text("Add Domain")


@router.api_route(
    "/{organization_id}/capabilities/{capability}",
    name="organizations:set_capability",
    methods=["GET", "POST"],
    response_model=None,
)
async def set_capability(
    request: Request,
    organization_id: UUID4,
    capability: str,
    value: bool,
    session: AsyncSession = Depends(get_db_session),
    user_session: UserSession = Depends(get_admin),
) -> HXRedirectResponse | None:
    """Render the capability override modal (GET) or apply it (POST)."""
    if capability not in CAPABILITY_NAMES:
        raise HTTPException(status_code=404, detail="Unknown capability")
    capability_name = cast(CapabilityName, capability)

    repository = OrganizationRepository(session)
    organization = await repository.get_by_id(organization_id, include_blocked=True)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")

    current_value = organization.capabilities[capability_name]

    settings_url = str(
        request.url_for(
            "organizations:detail", organization_id=organization_id
        ).include_query_params(section="settings")
    )

    if current_value == value:
        state = "enabled" if value else "disabled"
        await add_toast(
            request,
            f"Capability '{capability_name}' is already {state}.",
            "error",
        )
        return HXRedirectResponse(request, settings_url, 303)

    label, _ = CAPABILITY_METADATA[capability_name]
    validation_error: ValidationError | None = None

    if request.method == "POST":
        data = await request.form()
        try:
            form = SetCapabilityForm.model_validate_form(data)
            await organization_service.set_capability(
                session,
                organization,
                capability_name,
                value,
                reason=form.reason,
                admin_email=user_session.user.email,
            )

            action_word = "enabled" if value else "disabled"
            await add_toast(
                request,
                f"Capability '{label}' has been {action_word}.",
                "success",
            )
            return HXRedirectResponse(request, settings_url, 303)
        except ValidationError as e:
            validation_error = e

    form_action = str(
        request.url_for(
            "organizations:set_capability",
            organization_id=organization_id,
            capability=capability_name,
        ).include_query_params(value="true" if value else "false")
    )
    modal_view = SetCapabilityModal(
        organization,
        capability_name,
        label,
        value,
        form_action,
        validation_error,
    )
    with modal_view.render():
        pass

    return None


__all__ = ["router"]

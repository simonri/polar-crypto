import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, cast
from urllib.parse import urlparse
from uuid import UUID

import structlog
from pydantic import BaseModel, Field, TypeAdapter
from pydantic import ValidationError as PydanticValidationError
from sqlalchemy.exc import IntegrityError

from polar.account.service import account as account_service
from polar.auth.models import AuthSubject
from polar.authz.service import get_accessible_org_ids
from polar.config import settings
from polar.customer.repository import CustomerRepository
from polar.exceptions import (
    PolarError,
    PolarRequestValidationError,
)
from polar.integrations.polar.service import polar_self as polar_self_service
from polar.kit.anonymization import anonymize_email_for_deletion, anonymize_for_deletion
from polar.kit.currency import PresentmentCurrency
from polar.kit.pagination import PaginationParams
from polar.kit.repository import Options
from polar.kit.sorting import Sorting
from polar.member.repository import MemberRepository
from polar.member.service import member_service
from polar.models import (
    Customer,
    Organization,
    PayoutAccount,
    User,
    UserOrganization,
)
from polar.models.member import MemberRole
from polar.models.organization import (
    STATUS_CAPABILITIES,
    CapabilityName,
    OrganizationCapabilities,
    OrganizationDetails,
    OrganizationStatus,
)
from polar.models.transaction import TransactionType
from polar.models.user_organization import OrganizationRole
from polar.models.webhook_endpoint import WebhookEventType
from polar.payout_account.repository import PayoutAccountRepository
from polar.payout_account.service import payout_account as payout_account_service
from polar.postgres import AsyncReadSession, AsyncSession, sql
from polar.product.repository import ProductRepository
from polar.transaction.service.transaction import transaction as transaction_service
from polar.user_organization.service import (
    user_organization as user_organization_service,
)
from polar.webhook.service import webhook as webhook_service
from polar.worker import enqueue_job

from .repository import OrganizationRepository
from .schemas import (
    OrganizationCreate,
    OrganizationDeletionBlockedReason,
    OrganizationSlugAvailability,
    OrganizationUpdate,
    SlugInput,
)
from .sorting import OrganizationSortProperty

log = structlog.get_logger()

_slug_input_adapter: TypeAdapter[str] = TypeAdapter(SlugInput)

# Hosting domains where it's unreasonable to expect the organization's support email to
# match the website domain — e.g. a user whose product is hosted at `x.framer.com`
# won't have `@framer.com` email.
_HOSTED_WEBSITE_DOMAINS: frozenset[str] = frozenset(
    {
        "chromewebstore.google.com",
        "figma.com",
        "github.com",
        "framer.com",
    }
)


def _website_domain(website: str | None) -> str | None:
    if not website:
        return None
    host = urlparse(website).hostname
    return host.removeprefix("www.") if host else None


def _is_hosted_website_domain(website_domain: str) -> bool:
    return any(
        website_domain == d or website_domain.endswith(f".{d}")
        for d in _HOSTED_WEBSITE_DOMAINS
    )


def _append_internal_note(
    organization: Organization, message: str, *, reason: str | None = None
) -> None:
    timestamp = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    note = f"[{timestamp}] {message}"
    if reason:
        note += f"\nReason: {reason}"
    if organization.internal_notes:
        organization.internal_notes = f"{organization.internal_notes}\n\n{note}"
    else:
        organization.internal_notes = note


class PaymentStatusResponse(BaseModel):
    """Service-level response for payment status."""

    payment_ready: bool = Field(
        description="Whether the organization is ready to accept payments"
    )
    organization_status: OrganizationStatus = Field(
        description="Current organization status"
    )


class OrganizationDeletionCheckResult(BaseModel):
    """Result of checking if an organization can be deleted."""

    can_delete_immediately: bool = Field(
        description="Whether the organization can be deleted immediately"
    )
    blocked_reasons: list[OrganizationDeletionBlockedReason] = Field(
        default_factory=list,
        description="Reasons why immediate deletion is blocked",
    )


class OrganizationError(PolarError): ...


class InvalidAccount(OrganizationError):
    def __init__(self, account_id: UUID) -> None:
        self.account_id = account_id
        message = (
            f"The account {account_id} does not exist or you don't have access to it."
        )
        super().__init__(message)


class AccountAlreadySet(OrganizationError):
    def __init__(self, organization_slug: str) -> None:
        self.organization_slug = organization_slug
        message = f"The account for organization '{organization_slug}' has already been set up by the owner. Contact support to change the owner of the account."
        super().__init__(message, 403)


class CannotChangeOwnerError(OrganizationError):
    def __init__(self, reason: str) -> None:
        super().__init__(f"Cannot change organization owner: {reason}")


class OrganizationService:
    async def list(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User | Organization],
        *,
        slug: str | None = None,
        pagination: PaginationParams,
        sorting: list[Sorting[OrganizationSortProperty]] = [
            (OrganizationSortProperty.created_at, False)
        ],
    ) -> tuple[Sequence[Organization], int]:
        repository = OrganizationRepository.from_session(session)
        org_ids = await get_accessible_org_ids(session, auth_subject)
        statement = repository.get_statement_by_org_ids(org_ids)

        if slug is not None:
            statement = statement.where(Organization.slug == slug)

        statement = repository.apply_sorting(statement, sorting)

        return await repository.paginate(
            statement, limit=pagination.limit, page=pagination.page
        )

    async def get(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User | Organization],
        id: uuid.UUID,
        *,
        options: Options = (),
    ) -> Organization | None:
        repository = OrganizationRepository.from_session(session)
        org_ids = await get_accessible_org_ids(session, auth_subject)
        statement = (
            repository.get_statement_by_org_ids(org_ids)
            .where(Organization.id == id)
            .options(*options)
        )
        return await repository.get_one_or_none(statement)

    async def get_anonymous(
        self,
        session: AsyncReadSession,
        id: uuid.UUID,
        *,
        options: Options = (),
    ) -> Organization | None:
        """Use it with precaution! Get organization by ID for anonymous users."""
        repository = OrganizationRepository.from_session(session)
        statement = (
            repository.get_base_statement()
            .where(Organization.status != OrganizationStatus.BLOCKED)
            .where(Organization.id == id)
            .options(*options)
        )

        return await repository.get_one_or_none(statement)

    async def check_slug_availability(
        self, session: AsyncReadSession, slug: str
    ) -> OrganizationSlugAvailability:
        """Check whether a slug is valid and available for a new organization.

        Runs the slug through `SlugInput` (the same validator used when
        creating an organization) and reports invalid slugs as unavailable
        rather than as a 422.
        """
        try:
            normalized = _slug_input_adapter.validate_python(slug)
        except PydanticValidationError:
            return OrganizationSlugAvailability(available=False)

        repository = OrganizationRepository.from_session(session)
        if await repository.slug_exists(normalized):
            return OrganizationSlugAvailability(available=False)

        return OrganizationSlugAvailability(available=True)

    async def create(
        self,
        session: AsyncSession,
        create_schema: OrganizationCreate,
        auth_subject: AuthSubject[User],
    ) -> Organization:
        repository = OrganizationRepository.from_session(session)
        if await repository.slug_exists(create_schema.slug):
            raise PolarRequestValidationError(
                [
                    {
                        "loc": ("body", "slug"),
                        "msg": "An organization with this slug already exists.",
                        "type": "value_error",
                        "input": create_schema.slug,
                    }
                ]
            )

        create_data = create_schema.model_dump(exclude_unset=True, exclude_none=True)
        feature_settings = create_data.get("feature_settings", {})
        feature_settings["member_model_enabled"] = True
        create_data["feature_settings"] = feature_settings

        create_data["status"] = OrganizationStatus.ACTIVE
        create_data["capabilities"] = {**STATUS_CAPABILITIES[OrganizationStatus.ACTIVE]}
        create_data["status_updated_at"] = datetime.now(UTC)

        nested = await session.begin_nested()
        try:
            organization = await repository.create(
                Organization(
                    **create_data,
                )
            )
            organization.account = await account_service.create(session)
            await session.flush()
        except IntegrityError as e:
            await nested.rollback()
            raise PolarRequestValidationError(
                [
                    {
                        "loc": ("body", "slug"),
                        "msg": "An organization with this slug already exists.",
                        "type": "value_error",
                        "input": create_schema.slug,
                    }
                ]
            ) from e
        owner = auth_subject.subject
        polar_self_service.enqueue_create_customer(
            organization_id=organization.id,
            name=organization.name,
            slug=organization.slug,
            owner_external_id=str(owner.id),
            owner_email=owner.email,
            owner_name=owner.email.split("@", 1)[0],
        )
        await self.add_user(
            session,
            organization,
            auth_subject.subject,
            role=OrganizationRole.owner,
            enqueue_polar_self_member=False,
        )

        enqueue_job("organization.created", organization_id=organization.id)

        return organization

    async def _validate_currency_change(
        self,
        session: AsyncSession,
        organization: Organization,
        new_currency: PresentmentCurrency,
    ) -> None:
        """Validate that all active products have the target currency."""
        if new_currency == organization.default_presentment_currency:
            return

        product_repo = ProductRepository.from_session(session)
        products_without_currency = await product_repo.get_products_without_currency(
            organization.id, new_currency
        )

        if products_without_currency:
            raise PolarRequestValidationError(
                [
                    {
                        "loc": ("body", "default_presentment_currency"),
                        "msg": (
                            "All active products must have prices in the new currency."
                        ),
                        "type": "value_error",
                        "input": new_currency,
                    }
                ]
            )

    async def update(
        self,
        session: AsyncSession,
        organization: Organization,
        update_schema: OrganizationUpdate,
    ) -> Organization:
        repository = OrganizationRepository.from_session(session)

        if organization.onboarded_at is None:
            organization.onboarded_at = datetime.now(UTC)

        if update_schema.feature_settings is not None:
            old_member_model = organization.feature_settings.get(
                "member_model_enabled", False
            )

            organization.feature_settings = {
                **organization.feature_settings,
                **update_schema.feature_settings.model_dump(
                    mode="json", exclude_unset=True, exclude_none=True
                ),
            }

            new_member_model = organization.feature_settings.get(
                "member_model_enabled", False
            )

            if not old_member_model and new_member_model:
                enqueue_job(
                    "organization.backfill_members",
                    organization_id=organization.id,
                )

        if update_schema.subscription_settings is not None:
            organization.subscription_settings = update_schema.subscription_settings

        if update_schema.default_presentment_currency is not None:
            await self._validate_currency_change(
                session, organization, update_schema.default_presentment_currency
            )

        update_dict = update_schema.model_dump(
            by_alias=True,
            exclude_unset=True,
            exclude={
                "profile_settings",
                "feature_settings",
                "subscription_settings",
                "details",
            },
        )

        if update_schema.details:
            organization.details = cast(
                OrganizationDetails, update_schema.details.model_dump()
            )

        organization = await repository.update(organization, update_dict=update_dict)

        await self._after_update(session, organization)
        return organization

    async def delete(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> Organization:
        """Anonymizes fields on the Organization that can contain PII and then
        soft-deletes the Organization.

        DOES NOT:
        - Delete or anonymize Users related Organization
        - Delete or anonymize Account of the Organization
        - Delete or anonymize Customers, Products, Discounts, Checkouts of the Organization
        - Remove API tokens (organization or personal)
        """
        repository = OrganizationRepository.from_session(session)

        update_dict: dict[str, Any] = {}

        pii_fields = ["name", "slug", "website"]
        github_fields = ["bio", "company", "blog", "location", "twitter_username"]
        for pii_field in pii_fields + github_fields:
            value = getattr(organization, pii_field)
            if value:
                update_dict[pii_field] = anonymize_for_deletion(
                    value, organization.created_at
                )

        if organization.email:
            update_dict["email"] = anonymize_email_for_deletion(
                organization.email, organization.created_at
            )

        if organization.details:
            update_dict["details"] = {}

        if organization.socials:
            update_dict["socials"] = []

        organization = await repository.update(organization, update_dict=update_dict)
        await repository.soft_delete(organization)
        polar_self_service.enqueue_delete_customer(organization_id=organization.id)

        return organization

    async def check_can_delete(
        self,
        session: AsyncReadSession,
        organization: Organization,
    ) -> OrganizationDeletionCheckResult:
        """Check if an organization can be deleted immediately.

        An organization can be deleted immediately if it has:
        - No paid orders (excludes $0 orders from free/discounted products)
        - No paid active subscriptions (excludes inherently free or
          permanently discounted subscriptions)

        If it has an account but no orders/subscriptions, we'll attempt to
        delete the Stripe account first.
        """
        blocked_reasons: list[OrganizationDeletionBlockedReason] = []
        repository = OrganizationRepository.from_session(session)

        # Check for paid orders (excludes $0 orders)
        order_count = await repository.count_paid_orders_by_organization(
            organization.id
        )
        if order_count > 0:
            blocked_reasons.append(OrganizationDeletionBlockedReason.HAS_ORDERS)

        # Check for paid active subscriptions (excludes free subscriptions)
        active_subscription_count = (
            await repository.count_paid_active_subscriptions_by_organization(
                organization.id
            )
        )
        if active_subscription_count > 0:
            blocked_reasons.append(
                OrganizationDeletionBlockedReason.HAS_ACTIVE_SUBSCRIPTIONS
            )

        return OrganizationDeletionCheckResult(
            can_delete_immediately=len(blocked_reasons) == 0,
            blocked_reasons=blocked_reasons,
        )

    async def request_deletion(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        organization: Organization,
    ) -> OrganizationDeletionCheckResult:
        """Request deletion of an organization.

        Authorization is handled by the AuthorizeOrgDelete policy dependency
        at the endpoint level.

        Flow:
        1. Check for orders/subscriptions -> if blocked, create support ticket
        2. If has account -> try to delete Stripe account
        3. If Stripe deletion fails -> create support ticket
        4. Soft delete organization
        """
        check_result = await self.check_can_delete(session, organization)

        if not check_result.can_delete_immediately:
            # Organization has orders or active subscriptions
            enqueue_job(
                "organization.deletion_requested",
                organization_id=organization.id,
                user_id=auth_subject.subject.id,
                blocked_reasons=[r.value for r in check_result.blocked_reasons],
            )
            return check_result

        try:
            await self._delete_payout_account(session, organization)
        except Exception as e:
            log.error(
                "organization.deletion.stripe_account_deletion_failed",
                organization_id=organization.id,
                error=str(e),
            )
            # Stripe deletion failed, create support ticket
            check_result = OrganizationDeletionCheckResult(
                can_delete_immediately=False,
                blocked_reasons=[
                    OrganizationDeletionBlockedReason.STRIPE_ACCOUNT_DELETION_FAILED
                ],
            )
            enqueue_job(
                "organization.deletion_requested",
                organization_id=organization.id,
                user_id=auth_subject.subject.id,
                blocked_reasons=[r.value for r in check_result.blocked_reasons],
            )
            return check_result

        # Soft delete the organization
        await self.soft_delete_organization(session, organization)

        return OrganizationDeletionCheckResult(
            can_delete_immediately=True,
            blocked_reasons=[],
        )

    async def soft_delete_organization(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> Organization:
        """Soft-delete an organization, releasing its slug for reuse.

        Anonymizes PII fields, archives the previous slug to ``slug_history``,
        and rewrites the live slug to a tombstone so a new organization can
        claim the original.
        """
        repository = OrganizationRepository.from_session(session)

        now = datetime.now(UTC)
        update_dict: dict[str, Any] = {
            "slug_history": [
                *organization.slug_history,
                {"slug": organization.slug, "deleted_at": now.isoformat()},
            ],
            "slug": f"__deleted__-{organization.slug}-{organization.id}",
        }

        pii_fields = ["name", "website"]
        github_fields = ["bio", "company", "blog", "location", "twitter_username"]
        for pii_field in pii_fields + github_fields:
            value = getattr(organization, pii_field)
            if value:
                update_dict[pii_field] = anonymize_for_deletion(
                    value, organization.created_at
                )

        if organization.email:
            update_dict["email"] = anonymize_email_for_deletion(
                organization.email, organization.created_at
            )

        if organization.details:
            update_dict["details"] = {}

        if organization.socials:
            update_dict["socials"] = []

        organization = await repository.update(organization, update_dict=update_dict)
        await repository.soft_delete(organization)
        polar_self_service.enqueue_delete_customer(organization_id=organization.id)

        log.info(
            "organization.deleted",
            organization_id=organization.id,
            slug=organization.slug,
        )

        return organization

    async def _delete_payout_account(
        self, session: AsyncSession, organization: Organization
    ) -> None:
        if organization.payout_account_id is None:
            return

        payout_account_repository = PayoutAccountRepository.from_session(session)
        payout_account = await payout_account_repository.get_by_id(
            organization.payout_account_id
        )

        if payout_account is None:
            return

        # Unlink the payout account from the organization before deleting
        organization_repository = OrganizationRepository.from_session(session)
        await organization_repository.delete_payout_account(payout_account.id)

        await payout_account_service.delete(session, payout_account)

    async def set_payout_account(
        self,
        session: AsyncSession,
        organization: Organization,
        payout_account: PayoutAccount,
    ) -> Organization:
        previous_payout_account_id = organization.payout_account_id

        organization_repository = OrganizationRepository.from_session(session)
        await organization_repository.update(
            organization,
            update_dict={"payout_account_id": payout_account.id},
            flush=True,
        )
        return organization

    async def add_user(
        self,
        session: AsyncSession,
        organization: Organization,
        user: User,
        *,
        role: OrganizationRole = OrganizationRole.member,
        polar_self_member_delay: int | None = None,
        enqueue_polar_self_member: bool = True,
    ) -> None:
        nested = await session.begin_nested()
        try:
            relation = UserOrganization(
                user_id=user.id, organization_id=organization.id, role=role
            )
            session.add(relation)
            await session.flush()
            log.info(
                "organization.member.added",
                user_id=user.id,
                organization_id=organization.id,
                role=role,
            )
        except IntegrityError:
            # TODO: Currently, we treat this as success since the connection
            # exists. However, once we use status to distinguish active/inactive
            # installations we need to change this.
            log.info(
                "organization.member.re_added",
                organization_id=organization.id,
                user_id=user.id,
                role=role,
            )
            await nested.rollback()
            # Update
            stmt = (
                sql.Update(UserOrganization)
                .where(
                    UserOrganization.user_id == user.id,
                    UserOrganization.organization_id == organization.id,
                )
                .values(
                    deleted_at=None,  # un-delete user if exists
                    role=role,
                )
            )
            await session.execute(stmt)
            await session.flush()
        finally:
            if enqueue_polar_self_member:
                polar_self_service.enqueue_add_member(
                    external_customer_id=str(organization.id),
                    email=user.email,
                    name=user.email.split("@", 1)[0],
                    external_id=str(user.id),
                    delay=polar_self_member_delay,
                )

    async def change_owner(
        self,
        session: AsyncSession,
        *,
        new_owner_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> None:
        new_owner_user = await user_organization_service.transfer_ownership(
            session,
            new_owner_user_id=new_owner_id,
            organization_id=organization_id,
        )

        await self._sync_polar_self_customer_owner(
            session,
            organization_id=organization_id,
            new_owner_user=new_owner_user,
        )

    async def _sync_polar_self_customer_owner(
        self,
        session: AsyncSession,
        *,
        organization_id: uuid.UUID,
        new_owner_user: User,
    ) -> None:
        if not settings.POLAR_SELF_ENABLED:
            return

        polar_organization_id = uuid.UUID(settings.POLAR_ORGANIZATION_ID)
        if organization_id == polar_organization_id:
            return

        customer_repository = CustomerRepository.from_session(session)
        customer = await customer_repository.get_by_external_id_and_organization(
            str(organization_id), polar_organization_id
        )
        if customer is None:
            raise CannotChangeOwnerError(
                f"Polar self customer not found for organization {organization_id}"
            )

        member_repository = MemberRepository.from_session(session)
        target_member = await member_repository.get_by_customer_id_and_external_id(
            customer.id, str(new_owner_user.id)
        )
        if target_member is None:
            raise CannotChangeOwnerError(
                f"Polar self member not found for user {new_owner_user.id}"
            )

        if target_member.role != MemberRole.owner:
            await member_service.update(
                session,
                target_member,
                role=MemberRole.owner,
                allow_ownership_transfer=True,
            )

    async def _after_update(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        await webhook_service.send(
            session, organization, WebhookEventType.organization_updated, organization
        )

    async def update_total_balance(
        self, session: AsyncSession, organization: Organization
    ) -> Organization:
        transfers_sum = await transaction_service.get_transactions_sum(
            session, organization.account_id, type=TransactionType.balance
        )
        organization.total_balance = transfers_sum
        session.add(organization)
        return organization

    def _enqueue_cancel_pending_payouts(self, organization: Organization) -> None:
        """Cancel in-flight (held/pending) payouts when an org leaves the
        review flow to a terminal state (denied, blocked, offboarding)."""
        if organization.account_id is not None:
            enqueue_job(
                "payout.cancel_account_payouts",
                account_id=organization.account_id,
            )

    async def block_organization(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> Organization:
        """Block an organization by setting status to BLOCKED."""
        organization.set_status(OrganizationStatus.BLOCKED)
        session.add(organization)
        self._enqueue_cancel_pending_payouts(organization)
        return organization

    async def backoffice_approve(
        self,
        session: AsyncSession,
        organization: Organization,
        *,
        reason: str,
    ) -> Organization:
        """Backoffice override to re-activate a BLOCKED organization."""
        if organization.status != OrganizationStatus.BLOCKED:
            raise OrganizationError(
                "backoffice_approve requires BLOCKED status, got "
                f"{organization.status.get_display_name()}.",
                400,
            )
        organization.set_status(OrganizationStatus.ACTIVE)
        _append_internal_note(organization, "Organization unblocked.", reason=reason)
        session.add(organization)
        log.info(
            "organization.backoffice_approve.activated",
            organization_id=str(organization.id),
            slug=organization.slug,
        )
        return organization

    async def set_organization_offboarding(
        self,
        session: AsyncSession,
        organization: Organization,
        *,
        reason: str | None = None,
    ) -> Organization:
        organization.set_status(OrganizationStatus.OFFBOARDING)
        _append_internal_note(
            organization, "Organization set to offboarding.", reason=reason
        )
        session.add(organization)
        self._enqueue_cancel_pending_payouts(organization)
        return organization

    def get_payment_status(self, organization: Organization) -> PaymentStatusResponse:
        """Get payment status and onboarding steps for an organization."""
        return PaymentStatusResponse(
            payment_ready=organization.can_accept_payments,
            organization_status=organization.status,
        )

    async def mark_ai_onboarding_complete(
        self, session: AsyncSession, organization: Organization
    ) -> Organization:
        """Mark the AI onboarding as completed for this organization.

        Only sets the timestamp if it hasn't been set before, to capture the first completion.
        """
        if organization.ai_onboarding_completed_at is not None:
            return organization

        repository = OrganizationRepository.from_session(session)
        organization = await repository.update(
            organization,
            update_dict={
                "onboarded_at": datetime.now(UTC),
                "ai_onboarding_completed_at": datetime.now(UTC),
            },
        )
        return organization

    async def set_capability(
        self,
        session: AsyncSession,
        organization: Organization,
        capability: CapabilityName,
        value: bool,
        *,
        reason: str,
        admin_email: str | None = None,
    ) -> Organization:
        """Override a single capability on an organization.

        The override persists until the next status transition — `set_status`
        resets `capabilities` from `STATUS_CAPABILITIES`.
        """
        current: OrganizationCapabilities = dict(  # type: ignore[assignment]
            organization.capabilities
        )
        if current[capability] == value:
            return organization

        current[capability] = value
        organization.capabilities = current

        action = "enabled" if value else "disabled"
        by = f" by {admin_email}" if admin_email else ""
        _append_internal_note(
            organization,
            f"Capability '{capability}' {action}{by}",
            reason=reason,
        )

        session.add(organization)
        return organization


organization = OrganizationService()

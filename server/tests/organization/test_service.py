from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from pytest_mock import MockerFixture

from polar.auth.models import AuthSubject
from polar.config import settings
from polar.enums import (
    PayoutAccountType,
    SubscriptionRecurringInterval,
)
from polar.exceptions import PolarRequestValidationError
from polar.models import Customer, Organization, Product, User, UserOrganization
from polar.models.account import Account
from polar.models.organization import (
    STATUS_CAPABILITIES,
    InvalidStatusTransitionError,
    OrganizationStatus,
)
from polar.models.user_organization import OrganizationRole
from polar.organization.repository import OrganizationRepository
from polar.organization.schemas import (
    OrganizationCreate,
    OrganizationFeatureSettings,
)
from polar.organization.service import OrganizationError
from polar.organization.service import organization as organization_service
from polar.postgres import AsyncSession
from polar.user_organization.service import (
    user_organization as user_organization_service,
)
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_order,
    create_payout_account,
    create_product,
)


@pytest.mark.asyncio
class TestCreate:
    @pytest.mark.auth
    @pytest.mark.parametrize(
        "slug",
        [
            "",
            "a",
            "ab",
            "Polar Software Inc 🌀",
            "slug/with/slashes",
            *settings.ORGANIZATION_SLUG_RESERVED_KEYWORDS,
        ],
    )
    async def test_slug_validation(
        self, slug: str, auth_subject: AuthSubject[User], session: AsyncSession
    ) -> None:
        with pytest.raises(ValidationError):
            await organization_service.create(
                session,
                OrganizationCreate(name="My New Organization", slug=slug),
                auth_subject,
            )

    @pytest.mark.auth
    async def test_existing_slug(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        with pytest.raises(PolarRequestValidationError):
            await organization_service.create(
                session,
                OrganizationCreate(name=organization.name, slug=organization.slug),
                auth_subject,
            )

    @pytest.mark.auth
    async def test_concurrent_slug_creation(
        self,
        mocker: MockerFixture,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        """
        Concurrent slug creation (TOCTOU race) is handled as a validation error.

        Simulates the race where slug_exists() returns False for both requests
        but the DB unique constraint catches the duplicate on INSERT.
        """
        # Bypass the pre-check to simulate both requests passing the pre-check
        mocker.patch.object(
            OrganizationRepository, "slug_exists", new=AsyncMock(return_value=False)
        )

        with pytest.raises(PolarRequestValidationError) as exc_info:
            await organization_service.create(
                session,
                OrganizationCreate(name=organization.name, slug=organization.slug),
                auth_subject,
            )

    @pytest.mark.auth
    @pytest.mark.parametrize("slug", ["polar-software-inc", "slug-with-dashes"])
    async def test_valid(
        self,
        slug: str,
        mocker: MockerFixture,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
    ) -> None:
        enqueue_job_mock = mocker.patch("polar.organization.service.enqueue_job")

        organization = await organization_service.create(
            session,
            OrganizationCreate(name="My New Organization", slug=slug),
            auth_subject,
        )

        assert organization.name == "My New Organization"
        assert organization.slug == slug
        assert organization.feature_settings == {
            "member_model_enabled": True,
        }

        user_organization = await user_organization_service.get_by_user_and_org(
            session, auth_subject.subject.id, organization.id
        )
        assert user_organization is not None
        assert user_organization.role == OrganizationRole.owner

        enqueue_job_mock.assert_called_once_with(
            "organization.created", organization_id=organization.id
        )

    @pytest.mark.auth
    async def test_enqueues_polar_self_customer_with_owner(
        self,
        mocker: MockerFixture,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
    ) -> None:
        create_customer_mock = mocker.patch(
            "polar.organization.service.polar_self_service.enqueue_create_customer"
        )
        add_member_mock = mocker.patch(
            "polar.organization.service.polar_self_service.enqueue_add_member"
        )

        organization = await organization_service.create(
            session,
            OrganizationCreate(name="My New Organization", slug="signup-race-test"),
            auth_subject,
        )

        owner = auth_subject.subject
        create_customer_mock.assert_called_once_with(
            organization_id=organization.id,
            name=organization.name,
            slug=organization.slug,
            owner_external_id=str(owner.id),
            owner_email=owner.email,
            owner_name=owner.email.split("@", 1)[0],
        )
        add_member_mock.assert_not_called()

    @pytest.mark.auth
    async def test_valid_with_feature_settings(
        self, auth_subject: AuthSubject[User], session: AsyncSession
    ) -> None:
        organization = await organization_service.create(
            session,
            OrganizationCreate(
                name="My New Organization",
                slug="my-new-organization",
                feature_settings=OrganizationFeatureSettings(
                    issue_funding_enabled=False
                ),
            ),
            auth_subject,
        )

        assert organization.name == "My New Organization"

        assert organization.feature_settings == {
            "issue_funding_enabled": False,
            "member_model_enabled": True,
        }

    @pytest.mark.auth
    async def test_valid_with_none_subscription_settings(
        self, auth_subject: AuthSubject[User], session: AsyncSession
    ) -> None:
        organization = await organization_service.create(
            session,
            OrganizationCreate(
                name="My New Organization",
                slug="my-new-organization",
                subscription_settings=None,
            ),
            auth_subject,
        )

        assert organization.subscription_settings is not None

    @pytest.mark.auth
    async def test_creates_active_organization(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
    ) -> None:
        organization = await organization_service.create(
            session,
            OrganizationCreate(name="New Org", slug="new-org"),
            auth_subject,
        )

        assert organization.status == OrganizationStatus.ACTIVE
        assert (
            organization.capabilities == STATUS_CAPABILITIES[OrganizationStatus.ACTIVE]
        )
        assert organization.status_updated_at is not None


@pytest.mark.asyncio
class TestBackofficeApprove:
    async def test_rejects_non_blocked(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.status = OrganizationStatus.REVIEW

        with pytest.raises(OrganizationError, match="BLOCKED"):
            await organization_service.backoffice_approve(
                session, organization, reason="Test"
            )

    async def test_activates_blocked_organization(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.status = OrganizationStatus.BLOCKED

        await organization_service.backoffice_approve(
            session, organization, reason="Support escalation"
        )

        assert organization.status == OrganizationStatus.ACTIVE


class TestGetPaymentStatus:
    def test_active_org_is_payment_ready(
        self,
        organization: Organization,
    ) -> None:
        # Default fixture status is ACTIVE → checkout_payments capability is True.
        payment_status = organization_service.get_payment_status(organization)
        assert payment_status.payment_ready is True

    def test_blocked_org_is_not_payment_ready(
        self,
        organization: Organization,
    ) -> None:
        organization.set_status(OrganizationStatus.BLOCKED)

        payment_status = organization_service.get_payment_status(organization)

        assert payment_status.payment_ready is False


@pytest.mark.asyncio
class TestCheckCanDelete:
    async def test_can_delete_no_activity(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        """Organization with no orders and no subscriptions can be deleted."""
        result = await organization_service.check_can_delete(session, organization)

        assert result.can_delete_immediately is True
        assert result.blocked_reasons == []

    async def test_blocked_with_paid_orders(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        customer: Customer,
    ) -> None:
        """Organization with paid orders cannot be immediately deleted."""
        from tests.fixtures.random_objects import create_order

        await create_order(save_fixture, customer=customer, subtotal_amount=1000)

        result = await organization_service.check_can_delete(session, organization)

        assert result.can_delete_immediately is False
        assert "has_orders" in [r.value for r in result.blocked_reasons]

    async def test_not_blocked_with_zero_amount_orders(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        customer: Customer,
    ) -> None:
        """Organization with only $0 orders can be deleted."""
        from tests.fixtures.random_objects import create_order

        await create_order(
            save_fixture,
            customer=customer,
            subtotal_amount=0,
        )

        result = await organization_service.check_can_delete(session, organization)

        assert result.can_delete_immediately is True
        assert result.blocked_reasons == []

    async def test_not_blocked_with_fully_discounted_orders(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        customer: Customer,
    ) -> None:
        """Organization with only fully discounted $0 orders can be deleted."""
        from tests.fixtures.random_objects import create_order

        await create_order(
            save_fixture,
            customer=customer,
            subtotal_amount=1000,
            discount_amount=1000,
        )

        result = await organization_service.check_can_delete(session, organization)

        assert result.can_delete_immediately is True
        assert result.blocked_reasons == []

    async def test_blocked_with_paid_active_subscriptions(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        product: Product,
        customer: Customer,
    ) -> None:
        """Organization with paid active subscriptions cannot be immediately deleted."""
        from polar.models.subscription import SubscriptionStatus
        from tests.fixtures.random_objects import create_subscription

        await create_subscription(
            save_fixture,
            product=product,
            customer=customer,
            status=SubscriptionStatus.active,
        )

        result = await organization_service.check_can_delete(session, organization)

        assert result.can_delete_immediately is False
        assert "has_active_subscriptions" in [r.value for r in result.blocked_reasons]

    async def test_not_blocked_with_free_active_subscriptions(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        customer: Customer,
    ) -> None:
        """Organization with only free active subscriptions can be deleted."""
        from polar.models.subscription import SubscriptionStatus
        from tests.fixtures.random_objects import create_subscription

        free_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
            prices=[(None, "usd")],
        )
        await create_subscription(
            save_fixture,
            product=free_product,
            customer=customer,
            status=SubscriptionStatus.active,
        )

        result = await organization_service.check_can_delete(session, organization)

        assert result.can_delete_immediately is True
        assert result.blocked_reasons == []

    async def test_not_blocked_with_forever_discounted_free_subscriptions(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        product: Product,
        customer: Customer,
    ) -> None:
        """Organization with subscriptions made free by a forever discount can be deleted."""
        from polar.models.discount import DiscountDuration, DiscountType
        from polar.models.subscription import SubscriptionStatus
        from tests.fixtures.random_objects import create_discount, create_subscription

        discount = await create_discount(
            save_fixture,
            type=DiscountType.percentage,
            basis_points=10000,
            duration=DiscountDuration.forever,
            organization=organization,
        )
        await create_subscription(
            save_fixture,
            product=product,
            customer=customer,
            status=SubscriptionStatus.active,
            discount=discount,
        )

        result = await organization_service.check_can_delete(session, organization)

        assert result.can_delete_immediately is True
        assert result.blocked_reasons == []

    async def test_blocked_with_non_forever_discounted_free_subscriptions(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        product: Product,
        customer: Customer,
    ) -> None:
        """Subscription with a 100% off once discount still blocks deletion."""
        from polar.models.discount import DiscountDuration, DiscountType
        from polar.models.subscription import SubscriptionStatus
        from tests.fixtures.random_objects import create_discount, create_subscription

        discount = await create_discount(
            save_fixture,
            type=DiscountType.percentage,
            basis_points=10000,
            duration=DiscountDuration.once,
            organization=organization,
        )
        await create_subscription(
            save_fixture,
            product=product,
            customer=customer,
            status=SubscriptionStatus.active,
            discount=discount,
        )

        result = await organization_service.check_can_delete(session, organization)

        assert result.can_delete_immediately is False
        assert "has_active_subscriptions" in [r.value for r in result.blocked_reasons]

    async def test_not_blocked_with_canceled_subscriptions(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        product: Product,
        customer: Customer,
    ) -> None:
        """Organization with canceled subscriptions can be deleted."""
        from polar.models.subscription import SubscriptionStatus
        from tests.fixtures.random_objects import create_subscription

        await create_subscription(
            save_fixture,
            product=product,
            customer=customer,
            status=SubscriptionStatus.canceled,
        )

        result = await organization_service.check_can_delete(session, organization)

        assert result.can_delete_immediately is True
        assert result.blocked_reasons == []


@pytest.mark.asyncio
class TestRequestDeletion:
    @pytest.mark.auth
    async def test_immediate_deletion_no_activity(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        organization: Organization,
    ) -> None:
        """Organization with no activity is immediately deleted."""
        enqueue_job_mock = mocker.patch("polar.organization.service.enqueue_job")

        result = await organization_service.request_deletion(
            session, auth_subject, organization
        )

        assert result.can_delete_immediately is True
        assert organization.deleted_at is not None
        # No job should be enqueued for immediate deletion
        enqueue_job_mock.assert_not_called()

    @pytest.mark.auth
    async def test_blocked_creates_support_ticket(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        save_fixture: SaveFixture,
        auth_subject: AuthSubject[User],
        organization: Organization,
        customer: Customer,
    ) -> None:
        """Organization with orders creates support ticket."""
        await create_order(save_fixture, customer=customer)

        enqueue_job_mock = mocker.patch("polar.organization.service.enqueue_job")

        result = await organization_service.request_deletion(
            session, auth_subject, organization
        )

        assert result.can_delete_immediately is False
        assert organization.deleted_at is None
        enqueue_job_mock.assert_called_once_with(
            "organization.deletion_requested",
            organization_id=organization.id,
            user_id=auth_subject.subject.id,
            blocked_reasons=["has_orders"],
        )

    @pytest.mark.auth
    async def test_with_account_deletes_payout_account(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        save_fixture: SaveFixture,
        auth_subject: AuthSubject[User],
        organization: Organization,
        account: Account,
        user: User,
    ) -> None:
        """Organization with account deletes payout account first."""
        await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )
        payout_account_delete_mock = mocker.patch(
            "polar.organization.service.payout_account_service.delete",
            return_value=None,
        )

        result = await organization_service.request_deletion(
            session, auth_subject, organization
        )

        assert result.can_delete_immediately is True
        assert organization.deleted_at is not None
        payout_account_delete_mock.assert_called_once()

    @pytest.mark.auth
    async def test_payout_account_deletion_failure_creates_ticket(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        save_fixture: SaveFixture,
        auth_subject: AuthSubject[User],
        organization: Organization,
        user: User,
    ) -> None:
        """Payout account deletion failure creates support ticket."""
        await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )
        mocker.patch(
            "polar.organization.service.payout_account_service.delete",
            side_effect=Exception("Stripe API error"),
        )

        enqueue_job_mock = mocker.patch("polar.organization.service.enqueue_job")

        result = await organization_service.request_deletion(
            session, auth_subject, organization
        )

        assert result.can_delete_immediately is False
        assert "stripe_account_deletion_failed" in [
            r.value for r in result.blocked_reasons
        ]
        assert organization.deleted_at is None
        enqueue_job_mock.assert_called_once()


@pytest.mark.asyncio
class TestSoftDeleteOrganization:
    async def test_enqueues_polar_self_customer_deletion(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        enqueue_delete_customer_mock = mocker.patch(
            "polar.organization.service.polar_self_service.enqueue_delete_customer"
        )

        await organization_service.soft_delete_organization(session, organization)

        enqueue_delete_customer_mock.assert_called_once_with(
            organization_id=organization.id
        )

    async def test_anonymizes_pii_and_releases_slug(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
    ) -> None:
        original_slug = organization.slug
        organization.name = "Test Organization"
        organization.email = "test@example.com"
        organization.website = "https://test.com"
        organization.bio = "Test bio"
        await save_fixture(organization)

        result = await organization_service.soft_delete_organization(
            session, organization
        )

        # The live slug should no longer be the original, freeing it for reuse.
        assert result.slug != original_slug

        # The original slug is archived in slug_history.
        assert len(result.slug_history) == 1
        assert result.slug_history[0]["slug"] == original_slug
        assert "deleted_at" in result.slug_history[0]

        # PII should be anonymized
        assert result.name != "Test Organization"
        assert result.email != "test@example.com"
        assert result.website != "https://test.com"
        assert result.bio != "Test bio"

        # Should be soft deleted
        assert result.deleted_at is not None

    async def test_releases_slug_for_reuse(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
    ) -> None:
        original_slug = organization.slug
        await organization_service.soft_delete_organization(session, organization)
        await session.flush()

        repository = OrganizationRepository.from_session(session)
        # Original slug is now free — slug_exists (which still inspects
        # soft-deleted rows defensively) reports it as available.
        assert await repository.slug_exists(original_slug) is False

    async def test_appends_to_existing_slug_history(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
    ) -> None:
        previous_entry = {
            "slug": "previous-slug",
            "deleted_at": "2026-01-01T00:00:00+00:00",
        }
        organization.slug_history = [previous_entry]
        await save_fixture(organization)

        result = await organization_service.soft_delete_organization(
            session, organization
        )

        assert len(result.slug_history) == 2
        assert result.slug_history[0] == previous_entry
        assert result.slug_history[1]["slug"] != previous_entry["slug"]

    async def test_clears_details_and_socials(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
    ) -> None:
        """Soft delete clears details and socials."""
        organization.details = {"about": "Test company"}
        organization.socials = [
            {"platform": "twitter", "url": "https://twitter.com/test"}
        ]
        await save_fixture(organization)

        result = await organization_service.soft_delete_organization(
            session, organization
        )

        assert result.details == {}
        assert result.socials == []


@pytest.mark.asyncio
class TestDelete:
    async def test_enqueues_polar_self_customer_deletion(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        enqueue_delete_customer_mock = mocker.patch(
            "polar.organization.service.polar_self_service.enqueue_delete_customer"
        )

        await organization_service.delete(session, organization)

        enqueue_delete_customer_mock.assert_called_once_with(
            organization_id=organization.id
        )


@pytest.mark.asyncio
class TestSetOrganizationOffboarding:
    async def test_from_active(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.status = OrganizationStatus.ACTIVE

        result = await organization_service.set_organization_offboarding(
            session, organization
        )

        assert result.status == OrganizationStatus.OFFBOARDING
        assert result.status_updated_at is not None

    @pytest.mark.parametrize(
        "status",
        [
            OrganizationStatus.SNOOZED,
            OrganizationStatus.REVIEW,
            OrganizationStatus.DENIED,
            OrganizationStatus.CREATED,
        ],
    )
    async def test_from_non_active_raises(
        self,
        status: OrganizationStatus,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.status = status

        with pytest.raises(Exception):
            await organization_service.set_organization_offboarding(
                session, organization
            )

    async def test_with_reason_appends_internal_notes(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.status = OrganizationStatus.ACTIVE
        organization.internal_notes = None

        result = await organization_service.set_organization_offboarding(
            session, organization, reason="Requested by merchant"
        )

        assert result.status == OrganizationStatus.OFFBOARDING
        assert result.internal_notes is not None
        assert "Requested by merchant" in result.internal_notes

    async def test_enqueues_cancel_pending_payouts(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.status = OrganizationStatus.ACTIVE
        enqueue_job_mock = mocker.patch("polar.organization.service.enqueue_job")

        await organization_service.set_organization_offboarding(session, organization)

        enqueue_job_mock.assert_any_call(
            "payout.cancel_account_payouts",
            account_id=organization.account_id,
        )


@pytest.mark.asyncio
class TestSetPayoutAccount:
    @pytest.mark.auth
    async def test_set_payout_account_on_organization(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        user_organization: UserOrganization,
        user: User,
    ) -> None:
        """Successfully sets the payout account on an organization."""
        payout_account = await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )
        # Unlink from org first
        organization.payout_account = None
        await save_fixture(organization)

        updated_org = await organization_service.set_payout_account(
            session, organization, payout_account
        )

        assert updated_org.payout_account_id == payout_account.id

    @pytest.mark.auth
    async def test_swap_updates_payout_account(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        user_organization: UserOrganization,
        user: User,
    ) -> None:
        old_payout_account = await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )
        new_payout_account = await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )
        organization.payout_account = old_payout_account
        await save_fixture(organization)

        result = await organization_service.set_payout_account(
            session, organization, new_payout_account
        )

        assert result.payout_account_id == new_payout_account.id


@pytest.mark.asyncio
class TestStatusTransitions:
    """Tests for the organization status transition rules enforced in
    Organization.set_status()."""

    @pytest.mark.parametrize(
        "current",
        [
            OrganizationStatus.CREATED,
            OrganizationStatus.REVIEW,
            OrganizationStatus.SNOOZED,
            OrganizationStatus.ACTIVE,
            OrganizationStatus.DENIED,
            OrganizationStatus.OFFBOARDING,
        ],
    )
    async def test_every_status_can_go_to_blocked(
        self,
        current: OrganizationStatus,
        organization: Organization,
    ) -> None:
        organization.status = current
        organization.set_status(OrganizationStatus.BLOCKED)
        assert organization.status == OrganizationStatus.BLOCKED

    async def test_active_can_go_to_offboarding(
        self,
        organization: Organization,
    ) -> None:
        organization.status = OrganizationStatus.ACTIVE
        organization.set_status(OrganizationStatus.OFFBOARDING)
        assert organization.status == OrganizationStatus.OFFBOARDING

    @pytest.mark.parametrize(
        "current",
        [
            OrganizationStatus.CREATED,
            OrganizationStatus.SNOOZED,
            OrganizationStatus.REVIEW,
            OrganizationStatus.DENIED,
            OrganizationStatus.BLOCKED,
        ],
    )
    async def test_only_active_can_go_to_offboarding(
        self,
        current: OrganizationStatus,
        organization: Organization,
    ) -> None:
        organization.status = current
        with pytest.raises(InvalidStatusTransitionError):
            organization.set_status(OrganizationStatus.OFFBOARDING)

    async def test_self_transition_is_noop(
        self,
        organization: Organization,
    ) -> None:
        organization.status = OrganizationStatus.BLOCKED
        organization.set_status(OrganizationStatus.BLOCKED)
        assert organization.status == OrganizationStatus.BLOCKED

    @pytest.mark.parametrize(
        "target",
        [
            OrganizationStatus.REVIEW,
            OrganizationStatus.SNOOZED,
            OrganizationStatus.DENIED,
            OrganizationStatus.OFFBOARDING,
            OrganizationStatus.CREATED,
        ],
    )
    async def test_blocked_only_transitions_to_active(
        self,
        target: OrganizationStatus,
        organization: Organization,
    ) -> None:
        organization.status = OrganizationStatus.BLOCKED
        with pytest.raises(InvalidStatusTransitionError):
            organization.set_status(target)


@pytest.mark.asyncio
class TestCapabilityOverrides:
    """Capability overrides flip enforcement gates without changing status."""

    async def test_checkout_payments_override_blocks_active_org(
        self,
        organization: Organization,
    ) -> None:
        organization.set_status(OrganizationStatus.ACTIVE)
        assert organization.capabilities is not None
        organization.capabilities = {
            **organization.capabilities,
            "checkout_payments": False,
        }

        assert organization.can_accept_payments is False

    async def test_can_authenticate_follows_api_access_capability(
        self,
        organization: Organization,
    ) -> None:
        organization.set_status(OrganizationStatus.ACTIVE)
        assert organization.can_authenticate is True
        assert organization.capabilities is not None

        organization.capabilities = {
            **organization.capabilities,
            "api_access": False,
        }
        assert organization.can_authenticate is False

    async def test_can_access_dashboard_follows_dashboard_access_capability(
        self,
        organization: Organization,
    ) -> None:
        organization.set_status(OrganizationStatus.ACTIVE)
        assert organization.can_access_dashboard is True
        assert organization.capabilities is not None

        organization.capabilities = {
            **organization.capabilities,
            "dashboard_access": False,
        }
        assert organization.can_access_dashboard is False


@pytest.mark.asyncio
class TestSetStatusCapabilities:
    @pytest.mark.parametrize("status", list(OrganizationStatus))
    async def test_set_status_writes_capabilities(
        self,
        status: OrganizationStatus,
        organization: Organization,
    ) -> None:
        # Bypass set_status's transition validation: this test verifies the
        # capability mapping for each status, not the transition rules.
        organization.status = status
        organization.set_status(status)

        assert organization.status == status
        assert organization.capabilities == STATUS_CAPABILITIES[status]

    async def test_set_status_overwrites_prior_overrides(
        self,
        organization: Organization,
    ) -> None:
        organization.set_status(OrganizationStatus.ACTIVE)
        assert organization.capabilities is not None
        organization.capabilities = {**organization.capabilities, "payouts": False}

        organization.set_status(OrganizationStatus.BLOCKED)

        assert (
            organization.capabilities == STATUS_CAPABILITIES[OrganizationStatus.BLOCKED]
        )


@pytest.mark.asyncio
class TestSetCapability:
    async def test_flips_value(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.set_status(OrganizationStatus.ACTIVE)
        assert organization.capabilities is not None
        assert organization.capabilities["payouts"] is True

        result = await organization_service.set_capability(
            session,
            organization,
            "payouts",
            False,
            reason="Investigating suspicious withdrawal pattern",
        )

        assert result.capabilities is not None
        assert result.capabilities["payouts"] is False
        assert result.capabilities["checkout_payments"] is True

    async def test_appends_internal_note_with_reason(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.set_status(OrganizationStatus.ACTIVE)
        organization.internal_notes = None

        await organization_service.set_capability(
            session,
            organization,
            "payouts",
            False,
            reason="Manual ops hold",
            admin_email="ops@polar.sh",
        )

        assert organization.internal_notes is not None
        assert (
            "Capability 'payouts' disabled by ops@polar.sh"
            in organization.internal_notes
        )
        assert "Reason: Manual ops hold" in organization.internal_notes

    async def test_noop_when_unchanged(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.set_status(OrganizationStatus.ACTIVE)
        organization.internal_notes = None
        initial = dict(organization.capabilities or {})

        await organization_service.set_capability(
            session,
            organization,
            "payouts",
            True,
            reason="Already enabled, should not change",
        )

        assert organization.capabilities == initial
        assert organization.internal_notes is None

    async def test_status_transition_resets_override(
        self,
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        organization.set_status(OrganizationStatus.ACTIVE)
        await organization_service.set_capability(
            session,
            organization,
            "payouts",
            False,
            reason="Temporary hold for KYC recheck",
        )
        assert organization.capabilities is not None
        assert organization.capabilities["payouts"] is False

        organization.set_status(OrganizationStatus.BLOCKED)
        assert organization.capabilities is not None
        # BLOCKED defaults reset all capabilities; payouts is False, checkout_payments also False.
        assert organization.capabilities["payouts"] is False
        assert organization.capabilities["checkout_payments"] is False


@pytest.mark.asyncio
class TestChangeOwnerRoleSwap:
    """
    `change_owner` swaps `UserOrganization.role`: the previous `owner` is
    demoted to `admin`, and the new owner is promoted to `owner`. The flow
    no longer touches `Account.admin_id`; ownership is driven entirely
    by `UserOrganization.role`.
    """

    async def test_swaps_owner_role_on_owner_change(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        organization: Organization,
        user: User,
        user_second: User,
    ) -> None:
        previous_owner_uo = UserOrganization(
            user_id=user.id,
            organization_id=organization.id,
            role=OrganizationRole.owner,
        )
        new_owner_uo = UserOrganization(
            user_id=user_second.id,
            organization_id=organization.id,
            role=OrganizationRole.member,
        )
        await save_fixture(previous_owner_uo)
        await save_fixture(new_owner_uo)

        await organization_service.change_owner(
            session,
            new_owner_id=user_second.id,
            organization_id=organization.id,
        )

        previous = await user_organization_service.get_by_user_and_org(
            session, user.id, organization.id
        )
        new = await user_organization_service.get_by_user_and_org(
            session, user_second.id, organization.id
        )
        assert previous is not None
        assert new is not None
        assert previous.role == OrganizationRole.admin
        assert new.role == OrganizationRole.owner

    async def test_no_previous_owner_promotes_new_owner(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        organization: Organization,
        user: User,
        user_second: User,
    ) -> None:
        # Edge case: org has no current `owner` (shouldn't happen in
        # production post-backfill, but the swap should still promote
        # the new owner rather than blow up).
        previous_member_uo = UserOrganization(
            user_id=user.id,
            organization_id=organization.id,
            role=OrganizationRole.member,
        )
        new_owner_uo = UserOrganization(
            user_id=user_second.id,
            organization_id=organization.id,
            role=OrganizationRole.member,
        )
        await save_fixture(previous_member_uo)
        await save_fixture(new_owner_uo)

        await organization_service.change_owner(
            session,
            new_owner_id=user_second.id,
            organization_id=organization.id,
        )

        previous = await user_organization_service.get_by_user_and_org(
            session, user.id, organization.id
        )
        new = await user_organization_service.get_by_user_and_org(
            session, user_second.id, organization.id
        )
        assert previous is not None
        assert new is not None
        assert previous.role == OrganizationRole.member
        assert new.role == OrganizationRole.owner

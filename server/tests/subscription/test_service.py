import uuid
from collections import namedtuple
from collections.abc import Generator
from datetime import UTC, datetime, timedelta
from unittest.mock import ANY, AsyncMock, MagicMock

import freezegun
import pytest
from freezegun import freeze_time
from pytest_mock import MockerFixture

from polar.auth.models import AuthSubject
from polar.billing_entry.repository import BillingEntryRepository
from polar.checkout.eventstream import CheckoutEvent
from polar.email.schemas import SubscriptionRevokedEmail
from polar.enums import (
    PaymentProcessor,
    SubscriptionProrationBehavior,
    SubscriptionRecurringInterval,
)
from polar.event.repository import EventRepository
from polar.event.system import SystemEvent
from polar.exceptions import (
    BadRequest,
    PolarRequestValidationError,
    ResourceUnavailable,
)
from polar.kit.pagination import PaginationParams
from polar.kit.trial import TrialInterval
from polar.kit.utils import utc_now
from polar.locker import Locker
from polar.models import (
    Customer,
    Discount,
    Organization,
    PaymentMethod,
    Product,
    Subscription,
    User,
    UserOrganization,
)
from polar.models.billing_entry import BillingEntryDirection, BillingEntryType
from polar.models.checkout import CheckoutStatus
from polar.models.discount import DiscountDuration, DiscountType
from polar.models.order import OrderBillingReasonInternal
from polar.models.product_price import ProductPriceAmountType
from polar.models.subscription import SubscriptionStatus
from polar.models.webhook_endpoint import WebhookEventType
from polar.order.repository import OrderRepository
from polar.postgres import AsyncSession
from polar.product.guard import (
    is_fixed_price,
    is_free_price,
)
from polar.product.price_set import PriceSet
from polar.subscription.repository import SubscriptionUpdateRepository
from polar.subscription.schemas import (
    SubscriptionCreateCustomer,
    SubscriptionCreateExternalCustomer,
    SubscriptionUpdateBillingPeriod,
    SubscriptionUpdateDiscount,
    SubscriptionUpdateProduct,
    SubscriptionUpdateTrial,
)
from polar.subscription.service import (
    AlreadyCanceledSubscription,
    InactiveSubscription,
    MissingCheckoutCustomer,
    NotARecurringProduct,
    SubscriptionUpdateContext,
)
from polar.subscription.service import subscription as subscription_service
from polar.subscription.update import generate_subscription_update
from tests.fixtures.auth import AuthSubjectFixture
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_active_subscription,
    create_canceled_subscription,
    create_checkout,
    create_discount,
    create_legacy_recurring_product_price,
    create_product,
    create_subscription,
    create_trialing_subscription,
)

Hooks = namedtuple("Hooks", "updated activated canceled uncanceled revoked")
HookNames = frozenset(Hooks._fields)


def assert_webhook_sent_once(
    mock: AsyncMock,
    event_type: WebhookEventType,
    organization: Organization,
    subscription: Subscription,
) -> None:
    mock.assert_any_call(ANY, organization, event_type, subscription)


async def assert_order_exists(
    session: AsyncSession, subscription: Subscription
) -> None:
    repo = OrderRepository.from_session(session)
    orders = await repo.get_all_by_subscription(subscription.id)
    assert len(orders) > 0, (
        f"Expected order to exist for subscription {subscription.id}"
    )


def assert_hooks_called_once(subscription_hooks: Hooks, called: set[str]) -> None:
    for hook in called:
        getattr(subscription_hooks, hook).assert_called_once()

    not_called = HookNames - called
    for hook in not_called:
        getattr(subscription_hooks, hook).assert_not_called()


def reset_hooks(subscription_hooks: Hooks) -> None:
    for hook in HookNames:
        getattr(subscription_hooks, hook).reset_mock()


@pytest.fixture
def subscription_hooks(mocker: MockerFixture) -> Hooks:
    updated = mocker.patch.object(subscription_service, "_on_subscription_updated")
    activated = mocker.patch.object(subscription_service, "_on_subscription_activated")
    canceled = mocker.patch.object(subscription_service, "_on_subscription_canceled")
    uncanceled = mocker.patch.object(
        subscription_service, "_on_subscription_uncanceled"
    )
    revoked = mocker.patch.object(subscription_service, "_on_subscription_revoked")
    return Hooks(
        updated=updated,
        activated=activated,
        canceled=canceled,
        uncanceled=uncanceled,
        revoked=revoked,
    )


@pytest.fixture
def publish_checkout_event_mock(mocker: MockerFixture) -> AsyncMock:
    return mocker.patch("polar.subscription.service.publish_checkout_event")


@pytest.fixture
def enqueue_job_mock(mocker: MockerFixture) -> MagicMock:
    return mocker.patch("polar.subscription.service.enqueue_job")


@pytest.fixture
def enqueue_email_mock(mocker: MockerFixture) -> MagicMock:
    return mocker.patch("polar.subscription.service.enqueue_email_template")


@pytest.fixture
def webhook_service_send_mock(mocker: MockerFixture) -> AsyncMock:
    return mocker.patch("polar.subscription.service.webhook_service.send")


@pytest.fixture
def frozen_time() -> Generator[datetime, None]:
    frozen_time = utc_now()
    with freezegun.freeze_time(frozen_time):
        yield frozen_time


@pytest.mark.asyncio
class TestCreate:
    @pytest.mark.auth
    async def test_product_does_not_exist(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
    ) -> None:
        subscription_create = SubscriptionCreateCustomer(
            product_id=uuid.uuid4(),
            customer_id=uuid.uuid4(),
        )

        with pytest.raises(PolarRequestValidationError) as exc_info:
            await subscription_service.create(
                session, subscription_create, auth_subject
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("body", "product_id")
        assert errors[0]["msg"] == "Product does not exist."

    @pytest.mark.auth
    async def test_product_not_recurring(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        product_one_time: Product,
        customer: Customer,
        user_organization: UserOrganization,
    ) -> None:
        subscription_create = SubscriptionCreateCustomer(
            product_id=product_one_time.id,
            customer_id=customer.id,
        )

        with pytest.raises(PolarRequestValidationError) as exc_info:
            await subscription_service.create(
                session, subscription_create, auth_subject
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("body", "product_id")
        assert errors[0]["msg"] == "Product is not a recurring product."

    @pytest.mark.auth
    async def test_product_not_free(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        product: Product,
        customer: Customer,
        user_organization: UserOrganization,
    ) -> None:
        subscription_create = SubscriptionCreateCustomer(
            product_id=product.id,
            customer_id=customer.id,
        )

        with pytest.raises(PolarRequestValidationError) as exc_info:
            await subscription_service.create(
                session, subscription_create, auth_subject
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("body", "product_id")
        assert (
            errors[0]["msg"]
            == "Product is not free. The customer should go through a checkout to create a paid subscription."
        )

    @pytest.mark.auth
    async def test_customer_does_not_exist_by_id(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        product_recurring_free_price: Product,
        user_organization: UserOrganization,
    ) -> None:
        subscription_create = SubscriptionCreateCustomer(
            product_id=product_recurring_free_price.id,
            customer_id=uuid.uuid4(),
        )

        with pytest.raises(PolarRequestValidationError) as exc_info:
            await subscription_service.create(
                session, subscription_create, auth_subject
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("body", "customer_id")
        assert errors[0]["msg"] == "Customer does not exist."

    @pytest.mark.auth
    async def test_customer_does_not_exist_by_external_id(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        product_recurring_free_price: Product,
        user_organization: UserOrganization,
    ) -> None:
        subscription_create = SubscriptionCreateExternalCustomer(
            product_id=product_recurring_free_price.id,
            external_customer_id="nonexistent",
        )

        with pytest.raises(PolarRequestValidationError) as exc_info:
            await subscription_service.create(
                session, subscription_create, auth_subject
            )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["loc"] == ("body", "external_customer_id")
        assert errors[0]["msg"] == "Customer does not exist."

    @pytest.mark.auth
    async def test_valid_with_customer_id(
        self,
        subscription_hooks: Hooks,
        save_fixture: SaveFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        product_recurring_free_price: Product,
        customer: Customer,
        user_organization: UserOrganization,
    ) -> None:
        subscription_create = SubscriptionCreateCustomer(
            product_id=product_recurring_free_price.id,
            customer_id=customer.id,
            metadata={"key": "value"},
        )

        subscription = await subscription_service.create(
            session, subscription_create, auth_subject
        )

        assert subscription.status == SubscriptionStatus.active
        assert subscription.product_id == product_recurring_free_price.id
        assert subscription.customer_id == customer.id
        assert subscription.prices == product_recurring_free_price.prices
        assert subscription.amount == 0
        assert subscription.currency == "usd"
        assert subscription.recurring_interval == SubscriptionRecurringInterval.month
        assert subscription.recurring_interval_count == 1
        assert subscription.user_metadata == {"key": "value"}

        assert subscription.started_at is not None
        assert subscription.current_period_start is not None
        assert subscription.current_period_end is not None
        assert subscription.started_at == subscription.current_period_start
        assert subscription.current_period_end > subscription.current_period_start
        assert subscription.anchor_day == subscription.current_period_start.day

        assert_hooks_called_once(subscription_hooks, {"activated", "updated"})

    @pytest.mark.auth
    async def test_valid_with_external_customer_id(
        self,
        subscription_hooks: Hooks,
        save_fixture: SaveFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        product_recurring_free_price: Product,
        customer_external_id: Customer,
        user_organization: UserOrganization,
    ) -> None:
        assert customer_external_id.external_id is not None

        subscription_create = SubscriptionCreateExternalCustomer(
            product_id=product_recurring_free_price.id,
            external_customer_id=customer_external_id.external_id,
        )

        subscription = await subscription_service.create(
            session, subscription_create, auth_subject
        )

        assert subscription.status == SubscriptionStatus.active
        assert subscription.product_id == product_recurring_free_price.id
        assert subscription.customer_id == customer_external_id.id
        assert subscription.prices == product_recurring_free_price.prices

        assert_hooks_called_once(subscription_hooks, {"activated", "updated"})


@pytest.mark.asyncio
class TestCreateOrUpdateFromCheckout:
    async def test_not_recurring_product(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(
            save_fixture,
            products=[product_one_time],
            status=CheckoutStatus.confirmed,
        )
        with pytest.raises(NotARecurringProduct):
            await subscription_service.create_or_update_from_checkout(
                session, checkout, None
            )

    async def test_missing_customer(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product: Product,
    ) -> None:
        checkout = await create_checkout(
            save_fixture,
            products=[product],
            status=CheckoutStatus.confirmed,
        )
        with pytest.raises(MissingCheckoutCustomer):
            await subscription_service.create_or_update_from_checkout(
                session, checkout, None
            )

    async def test_new_fixed(
        self,
        publish_checkout_event_mock: AsyncMock,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product: Product,
        customer: Customer,
        payment_method: PaymentMethod,
    ) -> None:
        checkout = await create_checkout(
            save_fixture,
            products=[product],
            status=CheckoutStatus.confirmed,
            customer=customer,
        )

        (
            subscription,
            created,
        ) = await subscription_service.create_or_update_from_checkout(
            session, checkout, payment_method
        )

        assert created is True

        assert subscription.status == SubscriptionStatus.active
        assert subscription.prices == product.prices
        assert subscription.amount == checkout.total_amount
        assert subscription.payment_method == payment_method

        assert subscription.started_at is not None
        assert subscription.current_period_start is not None
        assert subscription.current_period_end is not None
        assert subscription.started_at == subscription.current_period_start
        assert subscription.current_period_end > subscription.current_period_start
        assert subscription.anchor_day == subscription.current_period_start.day

        publish_checkout_event_mock.assert_called_once_with(
            checkout.client_secret, CheckoutEvent.subscription_created
        )

    async def test_new_custom(
        self,
        publish_checkout_event_mock: AsyncMock,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_recurring_custom_price: Product,
        customer: Customer,
        payment_method: PaymentMethod,
    ) -> None:
        checkout = await create_checkout(
            save_fixture,
            products=[product_recurring_custom_price],
            status=CheckoutStatus.confirmed,
            customer=customer,
            amount=4242,
            currency="usd",
        )

        (
            subscription,
            created,
        ) = await subscription_service.create_or_update_from_checkout(
            session, checkout, payment_method
        )

        assert created is True

        assert subscription.status == SubscriptionStatus.active
        assert subscription.prices == product_recurring_custom_price.prices
        assert subscription.amount == checkout.total_amount
        assert subscription.currency == checkout.currency
        assert subscription.payment_method == payment_method

        publish_checkout_event_mock.assert_called_once_with(
            checkout.client_secret, CheckoutEvent.subscription_created
        )

    async def test_new_free(
        self,
        publish_checkout_event_mock: AsyncMock,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_recurring_free_price: Product,
        customer: Customer,
    ) -> None:
        checkout = await create_checkout(
            save_fixture,
            products=[product_recurring_free_price],
            status=CheckoutStatus.confirmed,
            customer=customer,
        )

        (
            subscription,
            created,
        ) = await subscription_service.create_or_update_from_checkout(
            session, checkout, None
        )

        assert created is True

        assert subscription.status == SubscriptionStatus.active
        assert subscription.prices == product_recurring_free_price.prices
        assert subscription.amount == 0
        assert subscription.currency == "usd"
        assert subscription.payment_method is None

        publish_checkout_event_mock.assert_called_once_with(
            checkout.client_secret, CheckoutEvent.subscription_created
        )

    async def test_new_custom_discount_percentage_100(
        self,
        publish_checkout_event_mock: AsyncMock,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_recurring_custom_price: Product,
        customer: Customer,
        discount_percentage_100: Discount,
        payment_method: PaymentMethod,
    ) -> None:
        checkout = await create_checkout(
            save_fixture,
            products=[product_recurring_custom_price],
            status=CheckoutStatus.confirmed,
            customer=customer,
            amount=4242,
            currency="usd",
            discount=discount_percentage_100,
        )

        (
            subscription,
            created,
        ) = await subscription_service.create_or_update_from_checkout(
            session, checkout, payment_method
        )

        assert created is True

        assert subscription.status == SubscriptionStatus.active
        assert subscription.prices == product_recurring_custom_price.prices
        assert subscription.amount == 0
        assert subscription.currency == checkout.currency
        assert subscription.payment_method == payment_method

        publish_checkout_event_mock.assert_called_once_with(
            checkout.client_secret, CheckoutEvent.subscription_created
        )

    async def test_upgrade_fixed(
        self,
        publish_checkout_event_mock: AsyncMock,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_recurring_free_price: Product,
        product: Product,
        customer: Customer,
        payment_method: PaymentMethod,
    ) -> None:
        subscription = await create_subscription(
            save_fixture,
            product=product_recurring_free_price,
            customer=customer,
            status=SubscriptionStatus.active,
        )
        checkout = await create_checkout(
            save_fixture,
            products=[product],
            status=CheckoutStatus.confirmed,
            customer=customer,
            subscription=subscription,
        )
        previous_current_period_start = subscription.current_period_start
        previous_current_period_end = subscription.current_period_end
        previous_started_at = subscription.started_at

        (
            updated_subscription,
            created,
        ) = await subscription_service.create_or_update_from_checkout(
            session, checkout, payment_method
        )

        assert created is False

        assert updated_subscription.status == SubscriptionStatus.active
        assert updated_subscription.prices == product.prices
        assert updated_subscription.amount == checkout.total_amount
        assert updated_subscription.currency == checkout.currency
        assert updated_subscription.payment_method == payment_method

        # Started at doesn't change, but current period does
        assert updated_subscription.started_at == previous_started_at
        assert updated_subscription.current_period_start > previous_current_period_start
        assert updated_subscription.current_period_end is not None
        assert previous_current_period_end is not None
        assert updated_subscription.current_period_end > previous_current_period_end
        assert (
            updated_subscription.anchor_day
            == updated_subscription.current_period_start.day
        )

        publish_checkout_event_mock.assert_called_once_with(
            checkout.client_secret, CheckoutEvent.subscription_created
        )

    async def test_trial(
        self,
        publish_checkout_event_mock: AsyncMock,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product: Product,
        customer: Customer,
        payment_method: PaymentMethod,
    ) -> None:
        checkout = await create_checkout(
            save_fixture,
            products=[product],
            status=CheckoutStatus.confirmed,
            customer=customer,
            trial_interval=TrialInterval.month,
            trial_interval_count=1,
        )

        (
            subscription,
            created,
        ) = await subscription_service.create_or_update_from_checkout(
            session, checkout, payment_method
        )

        assert created is True

        assert subscription.status == SubscriptionStatus.trialing
        assert subscription.prices == product.prices
        assert subscription.amount == checkout.total_amount
        assert subscription.payment_method == payment_method

        assert subscription.started_at is not None
        assert subscription.current_period_start is not None
        assert subscription.current_period_end is not None
        assert subscription.started_at == subscription.current_period_start
        assert subscription.current_period_end > subscription.current_period_start
        assert subscription.current_period_end == checkout.trial_end
        assert subscription.trial_start == subscription.current_period_start
        assert subscription.anchor_day == subscription.current_period_start.day

        publish_checkout_event_mock.assert_called_once_with(
            checkout.client_secret, CheckoutEvent.subscription_created
        )

    async def test_multi_currencies(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_recurring_multiple_currencies: Product,
        customer: Customer,
        payment_method: PaymentMethod,
    ) -> None:
        checkout = await create_checkout(
            save_fixture,
            products=[product_recurring_multiple_currencies],
            status=CheckoutStatus.confirmed,
            customer=customer,
            currency="eur",
        )

        (
            subscription,
            created,
        ) = await subscription_service.create_or_update_from_checkout(
            session, checkout, payment_method
        )

        assert created is True

        assert subscription.status == SubscriptionStatus.active

        currency_prices = PriceSet.from_prices(
            product_recurring_multiple_currencies.prices, "eur"
        )

        assert len(subscription.prices) == len(currency_prices)
        assert subscription.amount == checkout.total_amount
        assert subscription.payment_method == payment_method
        assert subscription.currency == "eur"


@pytest.mark.asyncio
class TestCycle:
    async def test_inactive(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_subscription(
            save_fixture, product=product, customer=customer
        )

        with pytest.raises(InactiveSubscription):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.cycle(session, ctx, subscription)

    async def test_fixed_price(
        self,
        session: AsyncSession,
        enqueue_job_mock: MagicMock,
        enqueue_email_mock: MagicMock,
        webhook_service_send_mock: AsyncMock,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            scheduler_locked_at=utc_now(),
        )

        previous_current_period_end = subscription.current_period_end

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )

        assert updated_subscription.ended_at is None
        assert updated_subscription.current_period_start == previous_current_period_end
        assert updated_subscription.current_period_end is not None
        assert previous_current_period_end is not None
        assert updated_subscription.current_period_end > previous_current_period_end
        assert updated_subscription.scheduler_locked_at is None

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(SystemEvent.subscription_cycled)
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["amount"] == subscription.amount
        assert event.user_metadata["currency"] == subscription.currency
        assert (
            event.user_metadata["recurring_interval"]
            == subscription.recurring_interval.value
        )
        assert (
            event.user_metadata["recurring_interval_count"]
            == subscription.recurring_interval_count
        )
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

        price = product.prices[0]
        assert is_fixed_price(price)
        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        assert len(billing_entries) == 1
        billing_entry = billing_entries[0]
        assert (
            billing_entry.start_timestamp == updated_subscription.current_period_start
        )
        assert billing_entry.end_timestamp == updated_subscription.current_period_end
        assert billing_entry.direction == BillingEntryDirection.debit
        assert billing_entry.customer_id == customer.id
        assert billing_entry.product_price_id == price.id
        assert billing_entry.event_id == event.id
        assert billing_entry.amount == price.price_amount
        assert billing_entry.currency == price.price_currency

        enqueue_job_mock.assert_any_call(
            "order.create_subscription_order",
            subscription.id,
            OrderBillingReasonInternal.subscription_cycle,
        )

        assert_webhook_sent_once(
            webhook_service_send_mock,
            WebhookEventType.subscription_updated,
            organization,
            updated_subscription,
        )

        enqueue_email_mock.assert_not_called()

    async def test_free_price(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_recurring_free_price: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product_recurring_free_price,
            customer=customer,
            scheduler_locked_at=utc_now(),
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            await subscription_service.cycle(session, ctx, subscription)

        price = product_recurring_free_price.prices[0]
        assert is_free_price(price)
        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        assert len(billing_entries) == 1
        billing_entry = billing_entries[0]
        assert billing_entry.amount == 0
        assert billing_entry.currency == subscription.currency

    @freeze_time("2024-01-15")
    async def test_discount_repetition(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        discount = await create_discount(
            save_fixture,
            type=DiscountType.fixed,
            amounts={"usd": 1000},
            duration=DiscountDuration.repeating,
            duration_in_months=3,
            organization=organization,
        )
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            discount=discount,
            scheduler_locked_at=utc_now(),
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            second_month_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )
        assert second_month_subscription.discount == discount

        async with SubscriptionUpdateContext(
            session, second_month_subscription, subscription_service
        ) as ctx:
            third_month_subscription = await subscription_service.cycle(
                session, ctx, second_month_subscription
            )
        assert third_month_subscription.discount == discount

        async with SubscriptionUpdateContext(
            session, third_month_subscription, subscription_service
        ) as ctx:
            fourth_month_subscription = await subscription_service.cycle(
                session, ctx, third_month_subscription
            )
        assert fourth_month_subscription.discount is None

        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        assert len(billing_entries) == 3

        (
            second_month_billing_entry,
            third_month_billing_entry,
            fourth_month_billing_entry,
        ) = billing_entries
        assert second_month_billing_entry.discount == discount
        assert third_month_billing_entry.discount == discount
        assert fourth_month_billing_entry.discount is None

    @freeze_time("2024-01-15")
    async def test_nth_month_cycle(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_recurring_every_second_month: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product_recurring_every_second_month,
            customer=customer,
            scheduler_locked_at=utc_now(),
        )

        first_period_start = subscription.current_period_start
        first_period_end = subscription.current_period_end
        assert first_period_start is not None
        assert first_period_end is not None
        assert first_period_end == first_period_start.replace(month=3)

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            second_cycle_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )
        second_period_start = second_cycle_subscription.current_period_start
        second_period_end = second_cycle_subscription.current_period_end
        assert second_period_start == first_period_end
        assert second_period_end is not None
        assert second_period_end == first_period_end.replace(month=5)

        async with SubscriptionUpdateContext(
            session, second_cycle_subscription, subscription_service
        ) as ctx:
            third_cycle_subscription = await subscription_service.cycle(
                session, ctx, second_cycle_subscription
            )
        assert third_cycle_subscription.current_period_start == second_period_end
        assert third_cycle_subscription.current_period_end is not None
        assert third_cycle_subscription.current_period_end == second_period_end.replace(
            month=7
        )

        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        assert len(billing_entries) == 2

        second_cycle_billing_entry, third_cycle_billing_entry = billing_entries
        assert second_cycle_billing_entry.start_timestamp == second_period_start
        assert second_cycle_billing_entry.end_timestamp == second_period_end
        assert (
            third_cycle_billing_entry.start_timestamp
            == third_cycle_subscription.current_period_start
        )
        assert (
            third_cycle_billing_entry.end_timestamp
            == third_cycle_subscription.current_period_end
        )

    async def test_anchor_monthly_cycle(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            scheduler_locked_at=utc_now(),
            current_period_start=datetime(2024, 1, 31, tzinfo=UTC),
        )
        assert subscription.anchor_day == 31
        assert subscription.recurring_interval_count == 1
        assert subscription.current_period_start.month == 1
        assert subscription.current_period_end.month == 2

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            second_cycle_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )
        second_period_start = second_cycle_subscription.current_period_start
        assert second_period_start.month == 2
        assert second_period_start.day == 29
        second_period_end = second_cycle_subscription.current_period_end
        assert second_period_end.month == 3
        assert second_period_end.day == 31

        async with SubscriptionUpdateContext(
            session, second_cycle_subscription, subscription_service
        ) as ctx:
            third_cycle_subscription = await subscription_service.cycle(
                session, ctx, second_cycle_subscription
            )
        third_period_start = third_cycle_subscription.current_period_start
        assert third_period_start.month == 3
        assert third_period_start.day == 31
        third_period_end = third_cycle_subscription.current_period_end
        assert third_period_end.month == 4
        assert third_period_end.day == 30

    async def test_cancel_at_period_end(
        self,
        session: AsyncSession,
        enqueue_job_mock: MagicMock,
        enqueue_email_mock: MagicMock,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            cancel_at_period_end=True,
            scheduler_locked_at=utc_now(),
        )

        previous_current_period_start = subscription.current_period_start
        previous_current_period_end = subscription.current_period_end

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )

        assert updated_subscription.status == SubscriptionStatus.canceled
        # ended_at should be set to the current time, not to ends_at
        assert updated_subscription.ended_at is not None
        assert updated_subscription.ended_at <= utc_now()
        # ends_at is the scheduled end time (future), ended_at is when it actually ended (now)
        assert updated_subscription.ends_at is not None
        assert updated_subscription.ended_at < updated_subscription.ends_at
        assert (
            updated_subscription.current_period_start == previous_current_period_start
        )
        assert updated_subscription.current_period_end == previous_current_period_end
        assert updated_subscription.scheduler_locked_at is None

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_revoked
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["amount"] == subscription.amount
        assert event.user_metadata["currency"] == subscription.currency
        assert (
            event.user_metadata["recurring_interval"]
            == subscription.recurring_interval.value
        )
        assert (
            event.user_metadata["recurring_interval_count"]
            == subscription.recurring_interval_count
        )
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        assert len(billing_entries) == 0

        enqueue_job_mock.assert_any_call(
            "order.create_subscription_order",
            subscription.id,
            OrderBillingReasonInternal.subscription_cancel,
        )

        enqueue_email_mock.assert_called_once()
        assert isinstance(enqueue_email_mock.call_args[0][0], SubscriptionRevokedEmail)
        subject = enqueue_email_mock.call_args.kwargs["subject"]
        assert "ended" in subject.lower()

    @freeze_time("2024-01-15 10:00:00")
    async def test_cancel_at_period_end_sets_ended_at_to_current_time(
        self,
        session: AsyncSession,
        enqueue_job_mock: MagicMock,
        enqueue_email_mock: MagicMock,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        """Test that ended_at is set to the current time when subscription ends, not to ends_at."""
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            cancel_at_period_end=True,
            scheduler_locked_at=utc_now(),
        )

        # Record when the cycle runs - this should be the ended_at timestamp
        cycle_time = utc_now()

        # ends_at is set to current_period_end (in the future)
        assert subscription.ends_at is not None
        assert subscription.ends_at == subscription.current_period_end
        assert subscription.ends_at > cycle_time

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )

        # ended_at should be set to the current time when the cycle ran, not to ends_at
        assert updated_subscription.status == SubscriptionStatus.canceled
        assert updated_subscription.ended_at == cycle_time

    async def test_trial_end(
        self,
        session: AsyncSession,
        enqueue_job_mock: MagicMock,
        enqueue_email_mock: MagicMock,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            scheduler_locked_at=utc_now(),
        )
        previous_trial_start = subscription.trial_start
        previous_trial_end = subscription.trial_end
        previous_current_period_end = subscription.current_period_end

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )

        assert updated_subscription.ended_at is None
        assert updated_subscription.current_period_start == previous_current_period_end
        assert updated_subscription.current_period_end is not None
        assert previous_current_period_end is not None
        assert updated_subscription.current_period_end > previous_current_period_end
        assert updated_subscription.scheduler_locked_at is None
        assert updated_subscription.status == SubscriptionStatus.active
        assert updated_subscription.trial_start == previous_trial_start
        assert updated_subscription.trial_end == previous_trial_end

        enqueue_job_mock.assert_any_call(
            "order.create_subscription_order",
            subscription.id,
            OrderBillingReasonInternal.subscription_cycle_after_trial,
        )

        enqueue_email_mock.assert_not_called()

    @freeze_time("2024-04-28 12:00:00")
    async def test_trial_end_rebases_anchor_to_trial_end_day(
        self,
        session: AsyncSession,
        enqueue_job_mock: MagicMock,
        enqueue_email_mock: MagicMock,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            trial_interval=TrialInterval.day,
            trial_interval_count=7,
            scheduler_locked_at=utc_now(),
        )
        assert subscription.anchor_day == 28
        assert subscription.trial_start == datetime(2024, 4, 28, 12, 0, 0, tzinfo=UTC)
        assert subscription.trial_end == datetime(2024, 5, 5, 12, 0, 0, tzinfo=UTC)
        assert subscription.current_period_end == subscription.trial_end

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )

        assert updated_subscription.status == SubscriptionStatus.active
        assert updated_subscription.anchor_day == 5
        assert updated_subscription.current_period_start == subscription.trial_end
        assert updated_subscription.current_period_end == datetime(
            2024, 6, 5, 12, 0, 0, tzinfo=UTC
        )

    async def test_trial_end_with_once_discount(
        self,
        session: AsyncSession,
        enqueue_job_mock: MagicMock,
        enqueue_email_mock: MagicMock,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        # Create a "once" discount (e.g., 100% off)
        discount = await create_discount(
            save_fixture,
            type=DiscountType.percentage,
            basis_points=10_000,  # 100%
            duration=DiscountDuration.once,
            organization=organization,
            code="TRIAL100",
        )

        # Create trialing subscription with the discount
        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            discount=discount,
            scheduler_locked_at=utc_now(),
        )

        # Verify discount is applied
        assert subscription.discount == discount
        assert subscription.status == SubscriptionStatus.trialing

        # Cycle the subscription (trial ends, first billing cycle)
        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )

        # Verify discount is STILL applied after trial ends
        # This is the first actual billing cycle, so "once" discount should apply
        assert updated_subscription.discount == discount
        assert updated_subscription.status == SubscriptionStatus.active

        # Verify billing entry was created with discount
        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        assert len(billing_entries) > 0
        cycle_entries = [
            entry for entry in billing_entries if entry.type == BillingEntryType.cycle
        ]
        assert len(cycle_entries) == 1
        assert cycle_entries[0].discount == discount
        assert cycle_entries[0].discount_amount is not None
        assert cycle_entries[0].discount_amount > 0

        # Now cycle again (second billing period)
        async with SubscriptionUpdateContext(
            session, updated_subscription, subscription_service
        ) as ctx:
            second_cycle_subscription = await subscription_service.cycle(
                session, ctx, updated_subscription
            )

        # Verify discount is NOW removed (used up after first billing cycle)
        assert second_cycle_subscription.discount is None

    async def test_trial_end_with_repeating_discount(
        self,
        session: AsyncSession,
        enqueue_job_mock: MagicMock,
        enqueue_email_mock: MagicMock,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        """Test that repeating discounts applied during checkout with trial
        are properly tracked from the first billing cycle after trial ends."""
        # Create a 3-month repeating discount
        discount = await create_discount(
            save_fixture,
            type=DiscountType.fixed,
            amounts={"usd": 1000},
            duration=DiscountDuration.repeating,
            duration_in_months=3,
            organization=organization,
        )

        # Create trialing subscription with the discount
        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            discount=discount,
            scheduler_locked_at=utc_now(),
        )

        # Verify initial state: discount is set but discount_applied_at is None
        # (discount hasn't been applied to a billing cycle yet)
        assert subscription.discount == discount
        assert subscription.discount_applied_at is None
        assert subscription.status == SubscriptionStatus.trialing

        # Cycle 1: Trial ends, first billing cycle
        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            first_billing_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )

        # Verify discount_applied_at is now set to the first billing period start
        assert first_billing_subscription.discount == discount
        assert first_billing_subscription.discount_applied_at is not None
        assert (
            first_billing_subscription.discount_applied_at
            == first_billing_subscription.current_period_start
        )
        assert first_billing_subscription.status == SubscriptionStatus.active

        # Cycle 2: Second billing cycle (2nd month of discount)
        async with SubscriptionUpdateContext(
            session, first_billing_subscription, subscription_service
        ) as ctx:
            second_billing_subscription = await subscription_service.cycle(
                session, ctx, first_billing_subscription
            )
        assert second_billing_subscription.discount == discount

        # Cycle 3: Third billing cycle (3rd month of discount)
        async with SubscriptionUpdateContext(
            session, second_billing_subscription, subscription_service
        ) as ctx:
            third_billing_subscription = await subscription_service.cycle(
                session, ctx, second_billing_subscription
            )
        assert third_billing_subscription.discount == discount

        # Cycle 4: Fourth billing cycle - discount should now be expired
        async with SubscriptionUpdateContext(
            session, third_billing_subscription, subscription_service
        ) as ctx:
            fourth_billing_subscription = await subscription_service.cycle(
                session, ctx, third_billing_subscription
            )
        assert fourth_billing_subscription.discount is None

        # Verify billing entries - 3 should have discount, 1 should not
        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        cycle_entries = [
            entry for entry in billing_entries if entry.type == BillingEntryType.cycle
        ]
        assert len(cycle_entries) == 4

        # First 3 entries should have discount applied
        assert cycle_entries[0].discount == discount
        assert cycle_entries[1].discount == discount
        assert cycle_entries[2].discount == discount
        # Fourth entry should have no discount
        assert cycle_entries[3].discount is None

    async def test_pending_update_product(
        self,
        session: AsyncSession,
        enqueue_job_mock: MagicMock,
        enqueue_email_mock: MagicMock,
        save_fixture: SaveFixture,
        product: Product,
        product_second: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            scheduler_locked_at=utc_now(),
        )
        subscription_update, _ = generate_subscription_update(
            subscription,
            SubscriptionProrationBehavior.prorate,
            product=product_second,
        )
        await save_fixture(subscription_update)
        subscription.pending_update = subscription_update
        await save_fixture(subscription)

        previous_current_period_end = subscription.current_period_end

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )

        assert updated_subscription.ended_at is None
        assert updated_subscription.current_period_start == previous_current_period_end
        assert updated_subscription.current_period_end is not None
        assert previous_current_period_end is not None
        assert updated_subscription.current_period_end > previous_current_period_end
        assert updated_subscription.scheduler_locked_at is None

        assert updated_subscription.product == product_second

        price = product_second.prices[0]
        assert is_fixed_price(price)
        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        assert len(billing_entries) == 1
        billing_entry = billing_entries[0]
        assert (
            billing_entry.start_timestamp == updated_subscription.current_period_start
        )
        assert billing_entry.end_timestamp == updated_subscription.current_period_end
        assert billing_entry.direction == BillingEntryDirection.debit
        assert billing_entry.product_price_id == price.id

        enqueue_job_mock.assert_any_call(
            "order.create_subscription_order",
            subscription.id,
            OrderBillingReasonInternal.subscription_cycle,
        )

        assert updated_subscription.pending_update is None

        subscription_update_repository = SubscriptionUpdateRepository.from_session(
            session
        )
        updated_subscription_update = await subscription_update_repository.get_by_id(
            subscription_update.id
        )
        assert updated_subscription_update is not None
        assert updated_subscription_update.applied_at is not None

    async def test_pending_update_product_interval_change(
        self,
        session: AsyncSession,
        enqueue_job_mock: MagicMock,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        annual_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.year,
            prices=[(10000, "usd")],
        )
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            scheduler_locked_at=utc_now(),
        )
        subscription_update, _ = generate_subscription_update(
            subscription,
            SubscriptionProrationBehavior.next_period,
            product=annual_product,
        )
        await save_fixture(subscription_update)
        subscription.pending_update = subscription_update
        await save_fixture(subscription)

        old_period_end = subscription.current_period_end
        assert old_period_end is not None

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.cycle(
                session, ctx, subscription
            )

        # The new period should start exactly at the old period end
        assert updated_subscription.current_period_start == old_period_end
        # The new period should end one year after the old period end (not two years)
        assert updated_subscription.current_period_end == (
            SubscriptionRecurringInterval.year.get_next_period(
                old_period_end, updated_subscription.current_period_start.day, 1
            )
        )
        assert updated_subscription.anchor_day == subscription.anchor_day
        assert updated_subscription.product == annual_product
        assert updated_subscription.pending_update is None

        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        assert len(billing_entries) == 1
        billing_entry = billing_entries[0]
        assert (
            billing_entry.start_timestamp == updated_subscription.current_period_start
        )
        assert billing_entry.end_timestamp == updated_subscription.current_period_end


@pytest.mark.asyncio
class TestRevoke:
    async def test_already_canceled(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_subscription(
            save_fixture,
            product=product,
            customer=customer,
            status=SubscriptionStatus.canceled,
        )

        with pytest.raises(AlreadyCanceledSubscription):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.revoke(session, ctx, subscription)

    async def test_valid(
        self,
        frozen_time: datetime,
        session: AsyncSession,
        save_fixture: SaveFixture,
        enqueue_job_mock: MagicMock,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.revoke(
                session, ctx, subscription
            )

        assert updated_subscription.status == SubscriptionStatus.canceled
        assert updated_subscription.canceled_at == frozen_time
        assert updated_subscription.ends_at == frozen_time
        assert updated_subscription.ended_at == frozen_time

        # Verify that the void pending orders task is enqueued
        enqueue_job_mock.assert_any_call(
            "order.void_pending_orders_for_subscription", subscription.id
        )

    async def test_revoke_scheduled_cancellation_sends_canceled_hook(
        self,
        frozen_time: datetime,
        session: AsyncSession,
        save_fixture: SaveFixture,
        subscription_hooks: Hooks,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_canceled_subscription(
            save_fixture,
            product=product,
            customer=customer,
            cancel_at_period_end=True,
        )
        assert subscription.canceled_at is not None
        assert subscription.cancel_at_period_end is True
        reset_hooks(subscription_hooks)

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            await subscription_service.revoke(session, ctx, subscription)

        subscription_hooks.canceled.assert_called_once()
        subscription_hooks.revoked.assert_called_once()


@pytest.mark.asyncio
class TestCancel:
    async def test_repeat_cancel_raises(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        save_fixture: SaveFixture,
        subscription_hooks: Hooks,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_canceled_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )
        assert subscription.cancel_at_period_end is True

        with pytest.raises(AlreadyCanceledSubscription):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.cancel(session, ctx, subscription)


@pytest.mark.asyncio
class TestUncancel:
    async def test_not_canceled(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        with pytest.raises(BadRequest):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.uncancel(session, ctx, subscription)

    async def test_valid(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        subscription_hooks: Hooks,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            cancel_at_period_end=True,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.uncancel(
                session, ctx, subscription
            )

        assert updated_subscription.status == SubscriptionStatus.active
        assert updated_subscription.cancel_at_period_end is False
        assert updated_subscription.ends_at is None
        assert updated_subscription.canceled_at is None

    async def test_uncancel_active(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        save_fixture: SaveFixture,
        subscription_hooks: Hooks,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )
        assert subscription.cancel_at_period_end is False

        with pytest.raises(BadRequest):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.uncancel(session, ctx, subscription)

    async def test_uncancel_already_revoked(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        save_fixture: SaveFixture,
        subscription_hooks: Hooks,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_canceled_subscription(
            save_fixture,
            product=product,
            customer=customer,
            cancel_at_period_end=False,
            revoke=True,
        )
        assert subscription.cancel_at_period_end is False
        assert subscription.ended_at
        assert subscription.canceled_at

        with pytest.raises(ResourceUnavailable):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.uncancel(session, ctx, subscription)

    async def test_uncancel_past_due(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        subscription_hooks: Hooks,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_subscription(
            save_fixture,
            product=product,
            customer=customer,
            status=SubscriptionStatus.past_due,
            cancel_at_period_end=True,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.uncancel(
                session, ctx, subscription
            )

        assert updated_subscription.status == SubscriptionStatus.past_due
        assert updated_subscription.cancel_at_period_end is False
        assert updated_subscription.ends_at is None
        assert updated_subscription.canceled_at is None


@pytest.mark.asyncio
class TestList:
    @pytest.mark.auth
    async def test_user_not_organization_member(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            started_at=datetime(2023, 1, 1),
            ended_at=datetime(2023, 6, 15),
        )

        # then
        session.expunge_all()

        results, count = await subscription_service.list(
            session, auth_subject, pagination=PaginationParams(1, 10)
        )

        assert len(results) == 0
        assert count == 0

    @pytest.mark.auth
    async def test_user_organization_member(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        save_fixture: SaveFixture,
        user_organization: UserOrganization,
        product: Product,
        customer: Customer,
    ) -> None:
        await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            started_at=datetime(2023, 1, 1),
            ended_at=datetime(2023, 6, 15),
        )

        # then
        session.expunge_all()

        results, count = await subscription_service.list(
            session, auth_subject, pagination=PaginationParams(1, 10)
        )

        assert len(results) == 1
        assert count == 1

    @pytest.mark.auth(AuthSubjectFixture(subject="organization"))
    async def test_organization(
        self,
        auth_subject: AuthSubject[Organization],
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        product: Product,
        customer: Customer,
    ) -> None:
        await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            started_at=datetime(2023, 1, 1),
            ended_at=datetime(2023, 6, 15),
        )

        # then
        session.expunge_all()

        results, count = await subscription_service.list(
            session, auth_subject, pagination=PaginationParams(1, 10)
        )

        assert len(results) == 1
        assert count == 1

    @pytest.mark.auth
    async def test_metadata_filter(
        self,
        auth_subject: AuthSubject[Organization],
        session: AsyncSession,
        save_fixture: SaveFixture,
        user_organization: UserOrganization,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription_1 = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            user_metadata={"reference_id": "ABC"},
        )
        subscription_2 = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            user_metadata={"reference_id": "DEF"},
        )
        await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            user_metadata={"reference_id": "GHI"},
        )

        # then
        session.expunge_all()

        results, count = await subscription_service.list(
            session,
            auth_subject,
            pagination=PaginationParams(1, 10),
            metadata={"reference_id": ["ABC", "DEF"]},
        )

        assert len(results) == 2
        assert count == 2

        assert subscription_1 in results
        assert subscription_2 in results


@pytest.mark.asyncio
class TestUpdate:
    async def test_product_update_prorate(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
        webhook_service_send_mock: MagicMock,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        new_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated = await subscription_service.update(
                session,
                ctx,
                subscription,
                update=SubscriptionUpdateProduct(product_id=new_product.id),
            )

        assert updated.product == new_product

        assert_webhook_sent_once(
            webhook_service_send_mock,
            WebhookEventType.subscription_updated,
            organization,
            updated,
        )

    async def test_product_update_invoice(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
        webhook_service_send_mock: MagicMock,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        new_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated = await subscription_service.update(
                session,
                ctx,
                subscription,
                update=SubscriptionUpdateProduct(
                    product_id=new_product.id,
                    proration_behavior=SubscriptionProrationBehavior.invoice,
                ),
            )

        assert updated.product == new_product
        await assert_order_exists(session, subscription)
        assert_webhook_sent_once(
            webhook_service_send_mock,
            WebhookEventType.subscription_updated,
            organization,
            updated,
        )

    async def test_discount_update(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
        discount_percentage_50: Discount,
        webhook_service_send_mock: MagicMock,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )
        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated = await subscription_service.update(
                session,
                ctx,
                subscription,
                update=SubscriptionUpdateDiscount(
                    discount_id=discount_percentage_50.id
                ),
            )

        assert updated.discount == discount_percentage_50
        assert_webhook_sent_once(
            webhook_service_send_mock,
            WebhookEventType.subscription_updated,
            organization,
            updated,
        )

    async def test_trial_update_extends(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
        webhook_service_send_mock: MagicMock,
    ) -> None:
        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            trial_interval=TrialInterval.day,
            trial_interval_count=7,
        )
        initial_trial_end = subscription.trial_end
        assert initial_trial_end is not None

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated = await subscription_service.update(
                session,
                ctx,
                subscription,
                update=SubscriptionUpdateTrial(
                    trial_end=initial_trial_end + timedelta(days=7),
                ),
            )

        assert updated.status == SubscriptionStatus.trialing
        assert updated.trial_end == initial_trial_end + timedelta(days=7)
        assert_webhook_sent_once(
            webhook_service_send_mock,
            WebhookEventType.subscription_updated,
            organization,
            updated,
        )

    async def test_trial_update_ends(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
        webhook_service_send_mock: MagicMock,
        enqueue_job_mock: MagicMock,
    ) -> None:
        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            trial_interval=TrialInterval.day,
            trial_interval_count=7,
        )
        initial_trial_end = subscription.trial_end
        assert initial_trial_end is not None

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated = await subscription_service.update(
                session,
                ctx,
                subscription,
                update=SubscriptionUpdateTrial(trial_end="now"),
            )

        assert updated.status == SubscriptionStatus.active
        assert updated.trial_end is not None
        assert updated.trial_end == updated.current_period_start
        assert updated.trial_end < initial_trial_end

        assert_webhook_sent_once(
            webhook_service_send_mock,
            WebhookEventType.subscription_updated,
            organization,
            updated,
        )
        enqueue_job_mock.assert_any_call(
            "order.create_subscription_order",
            updated.id,
            OrderBillingReasonInternal.subscription_cycle_after_trial,
        )

    async def test_billing_period_end_update(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
        webhook_service_send_mock: MagicMock,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )
        initial_period_end = subscription.current_period_end
        assert initial_period_end is not None

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated = await subscription_service.update(
                session,
                ctx,
                subscription,
                update=SubscriptionUpdateBillingPeriod(
                    current_billing_period_end=initial_period_end + timedelta(days=7)
                ),
            )

        assert updated.current_period_end == initial_period_end + timedelta(days=7)

        assert_webhook_sent_once(
            webhook_service_send_mock,
            WebhookEventType.subscription_updated,
            organization,
            updated,
        )


@pytest.mark.asyncio
class TestUpdateProduct:
    async def test_trial_to_equal_trial_succeeds(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
    ) -> None:
        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            trial_interval=TrialInterval.month,
            trial_interval_count=1,
        )
        original_trial_end = subscription.trial_end

        new_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
            trial_interval=TrialInterval.month,
            trial_interval_count=1,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated = await subscription_service.update_product(
                session, ctx, subscription, product_id=new_product.id
            )

        assert updated.product == new_product
        assert updated.status == SubscriptionStatus.trialing
        assert updated.trial_end == original_trial_end
        assert updated.current_period_end == original_trial_end

    async def test_trial_to_longer_trial_extends(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
    ) -> None:
        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            trial_interval=TrialInterval.day,
            trial_interval_count=7,
        )
        trial_start = subscription.trial_start
        assert trial_start is not None

        new_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
            trial_interval=TrialInterval.day,
            trial_interval_count=14,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated = await subscription_service.update_product(
                session, ctx, subscription, product_id=new_product.id
            )

        expected_trial_end = TrialInterval.day.get_end(trial_start, 14)
        assert updated.product == new_product
        assert updated.status == SubscriptionStatus.trialing
        assert updated.trial_end == expected_trial_end
        assert updated.current_period_end == expected_trial_end

    async def test_trial_to_shorter_trial_with_remaining_time_succeeds(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
    ) -> None:
        trial_creation_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        with freezegun.freeze_time(trial_creation_time):
            subscription = await create_trialing_subscription(
                save_fixture,
                product=product,
                customer=customer,
                trial_interval=TrialInterval.day,
                trial_interval_count=30,
            )
        trial_start = subscription.trial_start
        assert trial_start is not None

        new_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
            trial_interval=TrialInterval.day,
            trial_interval_count=7,
        )

        with freezegun.freeze_time(trial_creation_time + timedelta(days=1)):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                updated = await subscription_service.update_product(
                    session, ctx, subscription, product_id=new_product.id
                )

        expected_trial_end = TrialInterval.day.get_end(trial_start, 7)
        assert updated.product == new_product
        assert updated.status == SubscriptionStatus.trialing
        assert updated.trial_end == expected_trial_end
        assert updated.current_period_end == expected_trial_end

    async def test_trial_to_shorter_trial_already_elapsed_ends_trial(
        self,
        enqueue_job_mock: MagicMock,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
    ) -> None:
        trial_creation_time = datetime(2025, 1, 1, 12, 0, 0, tzinfo=UTC)
        with freezegun.freeze_time(trial_creation_time):
            subscription = await create_trialing_subscription(
                save_fixture,
                product=product,
                customer=customer,
                trial_interval=TrialInterval.day,
                trial_interval_count=30,
            )

        new_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
            trial_interval=TrialInterval.day,
            trial_interval_count=7,
        )

        change_time = trial_creation_time + timedelta(days=10)
        with freezegun.freeze_time(change_time):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                updated = await subscription_service.update_product(
                    session, ctx, subscription, product_id=new_product.id
                )

        assert updated.product == new_product
        assert updated.status == SubscriptionStatus.active
        assert updated.trial_end == change_time
        enqueue_job_mock.assert_any_call(
            "order.create_subscription_order",
            updated.id,
            OrderBillingReasonInternal.subscription_cycle_after_trial,
        )

    async def test_trial_to_no_trial_product_ends_trial(
        self,
        enqueue_job_mock: MagicMock,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
    ) -> None:
        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            trial_interval=TrialInterval.month,
            trial_interval_count=1,
        )

        new_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated = await subscription_service.update_product(
                session, ctx, subscription, product_id=new_product.id
            )

        assert updated.product == new_product
        assert updated.status == SubscriptionStatus.active
        enqueue_job_mock.assert_any_call(
            "order.create_subscription_order",
            updated.id,
            OrderBillingReasonInternal.subscription_cycle_after_trial,
        )

    async def test_trial_product_change_skips_proration_billing(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        mocker: MockerFixture,
        customer: Customer,
        organization: Organization,
        product: Product,
    ) -> None:
        create_subscription_update_order_mock = mocker.patch.object(
            subscription_service, "_create_subscription_update_order", new=AsyncMock()
        )

        subscription = await create_trialing_subscription(
            save_fixture,
            product=product,
            customer=customer,
            trial_interval=TrialInterval.month,
            trial_interval_count=1,
        )

        new_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
            trial_interval=TrialInterval.month,
            trial_interval_count=2,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            await subscription_service.update_product(
                session,
                ctx,
                subscription,
                product_id=new_product.id,
                proration_behavior=SubscriptionProrationBehavior.invoice,
            )

        create_subscription_update_order_mock.assert_not_called()

        billing_entry_repository = BillingEntryRepository.from_session(session)
        billing_entries = await billing_entry_repository.get_pending_by_subscription(
            subscription.id
        )
        proration_entries = [
            entry
            for entry in billing_entries
            if entry.type == BillingEntryType.proration
        ]
        assert proration_entries == []

    async def test_unavailable_currency(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        product_recurring_multiple_currencies: Product,
        customer: Customer,
        organization: Organization,
        webhook_service_send_mock: MagicMock,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product_recurring_multiple_currencies,
            customer=customer,
            currency="eur",
        )
        assert len(subscription.prices) == 1

        with pytest.raises(PolarRequestValidationError):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.update_product(
                    session,
                    ctx,
                    subscription,
                    product_id=product.id,
                    proration_behavior=SubscriptionProrationBehavior.prorate,
                )

        webhook_service_send_mock.assert_not_called()

    async def test_available_currency(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        product_recurring_multiple_currencies: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product_recurring_multiple_currencies,
            customer=customer,
            currency="usd",
        )
        assert len(subscription.prices) == 1

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.update_product(
                session,
                ctx,
                subscription,
                product_id=product.id,
                proration_behavior=SubscriptionProrationBehavior.prorate,
            )

        assert updated_subscription.product == product
        assert len(updated_subscription.prices) == 1
        price = updated_subscription.prices[0]
        assert price.price_currency == "usd"

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["product_id"] == str(product.id)
        assert (
            event.user_metadata["proration_behavior"]
            == SubscriptionProrationBehavior.prorate
        )
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

    async def test_upgrade_from_legacy_recurring_product_to_new_recurring_product(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        mocker: MockerFixture,
        organization: Organization,
        customer: Customer,
    ) -> None:
        mocker.patch.object(
            subscription_service, "_create_subscription_update_order", new=AsyncMock()
        )

        legacy_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=None,
            prices=[],
        )
        legacy_price = await create_legacy_recurring_product_price(
            save_fixture,
            amount_type=ProductPriceAmountType.fixed,
            product=legacy_product,
            recurring_interval=SubscriptionRecurringInterval.month,
            amount=1000,
        )
        legacy_product.prices.append(legacy_price)
        legacy_product.all_prices.append(legacy_price)
        await save_fixture(legacy_product)

        new_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
            prices=[(2000, "usd")],
        )

        subscription = await create_active_subscription(
            save_fixture,
            product=legacy_product,
            customer=customer,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.update_product(
                session,
                ctx,
                subscription,
                product_id=new_product.id,
                proration_behavior=SubscriptionProrationBehavior.prorate,
            )

        assert updated_subscription.product == new_product

    async def test_next_period_behavior_new_update(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        product_recurring_multiple_currencies: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product_recurring_multiple_currencies,
            customer=customer,
            currency="usd",
        )
        assert len(subscription.prices) == 1

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.update_product(
                session,
                ctx,
                subscription,
                product_id=product.id,
                proration_behavior=SubscriptionProrationBehavior.next_period,
            )

        assert updated_subscription.product == product_recurring_multiple_currencies

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["product_id"] == str(product.id)
        assert (
            event.user_metadata["proration_behavior"]
            == SubscriptionProrationBehavior.next_period
        )
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

        subscription_update_repository = SubscriptionUpdateRepository.from_session(
            session
        )
        subscription_update = (
            await subscription_update_repository.get_unapplied_by_subscription_id(
                subscription.id
            )
        )
        assert subscription_update is not None
        assert subscription_update.product_id == product.id
        assert subscription_update.applied_at is None
        assert subscription_update.applies_at == subscription.current_period_end

    async def test_next_period_behavior_existing_update(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        product_recurring_multiple_currencies: Product,
        product_second: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product_recurring_multiple_currencies,
            customer=customer,
            currency="usd",
        )
        assert len(subscription.prices) == 1

        subscription_update, _ = generate_subscription_update(
            subscription, SubscriptionProrationBehavior.prorate, product=product
        )
        await save_fixture(subscription_update)

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.update_product(
                session,
                ctx,
                subscription,
                product_id=product_second.id,
                proration_behavior=SubscriptionProrationBehavior.next_period,
            )

        assert updated_subscription.product == product_recurring_multiple_currencies

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["product_id"] == str(product_second.id)
        assert (
            event.user_metadata["proration_behavior"]
            == SubscriptionProrationBehavior.next_period
        )
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

        subscription_update_repository = SubscriptionUpdateRepository.from_session(
            session
        )
        updated_subscription_update = (
            await subscription_update_repository.get_unapplied_by_subscription_id(
                subscription.id
            )
        )
        assert updated_subscription_update is not None
        assert updated_subscription_update.id == subscription_update.id
        assert updated_subscription_update.applied_at is None
        assert updated_subscription_update.product_id == product_second.id
        assert subscription_update.applies_at == subscription.current_period_end

    async def test_proration_behavior_deletes_existing_update(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        product_recurring_multiple_currencies: Product,
        product_second: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product_recurring_multiple_currencies,
            customer=customer,
            currency="usd",
        )
        assert len(subscription.prices) == 1

        subscription_update, _ = generate_subscription_update(
            subscription, SubscriptionProrationBehavior.prorate, product=product
        )
        await save_fixture(subscription_update)

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.update_product(
                session,
                ctx,
                subscription,
                product_id=product_second.id,
                proration_behavior=SubscriptionProrationBehavior.prorate,
            )

        assert updated_subscription.product == product_second

        subscription_update_repository = SubscriptionUpdateRepository.from_session(
            session
        )
        updated_subscription_update = (
            await subscription_update_repository.get_unapplied_by_subscription_id(
                subscription.id
            )
        )
        assert updated_subscription_update is None

    async def test_next_period_behavior_changed_current_period_end(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        product_recurring_multiple_currencies: Product,
        customer: Customer,
        organization: Organization,
    ) -> None:
        current_period_start = utc_now()
        # Period end different than the real one, simulates a manual change of the subscription period end
        current_period_end = current_period_start + timedelta(days=10)
        subscription = await create_active_subscription(
            save_fixture,
            product=product_recurring_multiple_currencies,
            customer=customer,
            currency="usd",
            current_period_start=current_period_start,
            current_period_end=current_period_end,
        )
        assert len(subscription.prices) == 1

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.update_product(
                session,
                ctx,
                subscription,
                product_id=product.id,
                proration_behavior=SubscriptionProrationBehavior.next_period,
            )

        assert updated_subscription.product == product_recurring_multiple_currencies

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["product_id"] == str(product.id)
        assert (
            event.user_metadata["proration_behavior"]
            == SubscriptionProrationBehavior.next_period
        )
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

        subscription_update_repository = SubscriptionUpdateRepository.from_session(
            session
        )
        subscription_update = (
            await subscription_update_repository.get_unapplied_by_subscription_id(
                subscription.id
            )
        )
        assert subscription_update is not None
        assert subscription_update.product_id == product.id
        assert subscription_update.applied_at is None
        assert subscription_update.applies_at == subscription.current_period_end
        assert subscription_update.new_cycle_start == current_period_start
        assert subscription_update.new_cycle_end == current_period_end


@pytest.mark.asyncio
class TestUpdateDiscount:
    async def test_not_existing_discount(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        product: Product,
        customer: Customer,
        discount_percentage_50: Discount,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            discount=discount_percentage_50,
        )

        with pytest.raises(PolarRequestValidationError):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.update_discount(
                    session, ctx, subscription, discount_id=uuid.uuid4()
                )

    async def test_same_discount(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product: Product,
        customer: Customer,
        discount_percentage_50: Discount,
        discount_percentage_100: Discount,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            discount=discount_percentage_50,
        )

        with pytest.raises(PolarRequestValidationError):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.update_discount(
                    session, ctx, subscription, discount_id=discount_percentage_50.id
                )

    async def test_valid_removed(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product: Product,
        customer: Customer,
        discount_percentage_50: Discount,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            discount=discount_percentage_50,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            subscription = await subscription_service.update_discount(
                session, ctx, subscription, discount_id=None
            )

        assert subscription.discount is None

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["discount_id"] is None
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

    async def test_valid_added(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product: Product,
        customer: Customer,
        discount_percentage_50: Discount,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture, product=product, customer=customer
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            subscription = await subscription_service.update_discount(
                session, ctx, subscription, discount_id=discount_percentage_50.id
            )

        assert subscription.discount == discount_percentage_50

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["discount_id"] == str(discount_percentage_50.id)
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

    async def test_valid_modified(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product: Product,
        customer: Customer,
        discount_percentage_50: Discount,
        discount_percentage_100: Discount,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
            discount=discount_percentage_50,
        )

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            subscription = await subscription_service.update_discount(
                session, ctx, subscription, discount_id=discount_percentage_100.id
            )

        assert subscription.discount == discount_percentage_100

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["discount_id"] == str(discount_percentage_100.id)
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id


@pytest.mark.asyncio
class TestUpdateTrial:
    async def test_trialing_subscription_ending_now(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_trialing_subscription(
            save_fixture, product=product, customer=customer
        )

        assert subscription.trial_end is not None
        original_trial_end = subscription.trial_end

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.update_trial(
                session, ctx, subscription, trial_end="now"
            )

        assert updated_subscription.status == SubscriptionStatus.active
        assert updated_subscription.trial_end is not None
        assert (
            updated_subscription.trial_end == updated_subscription.current_period_start
        )
        assert updated_subscription.trial_end < original_trial_end

    async def test_trialing_subscription_extending(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
        subscription_hooks: Hooks,
    ) -> None:
        subscription = await create_trialing_subscription(
            save_fixture, product=product, customer=customer
        )

        assert subscription.trial_end is not None
        original_trial_end = subscription.trial_end

        new_trial_end = original_trial_end + timedelta(days=30)

        reset_hooks(subscription_hooks)
        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.update_trial(
                session, ctx, subscription, trial_end=new_trial_end
            )

        assert updated_subscription.status == SubscriptionStatus.trialing
        assert updated_subscription.current_period_end == new_trial_end
        assert updated_subscription.trial_end == new_trial_end
        assert updated_subscription.trial_end is not None
        assert updated_subscription.trial_end > original_trial_end

        # Verify that the webhook was triggered
        assert_hooks_called_once(subscription_hooks, {"updated"})

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["trial_end"] == new_trial_end.isoformat()
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

    async def test_active_subscription_ending_now_validation_error(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        with pytest.raises(PolarRequestValidationError) as exc_info:
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.update_trial(
                    session, ctx, subscription, trial_end="now"
                )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "value_error"
        assert errors[0]["loc"] == ("body", "trial_end")
        assert "not currently trialing" in errors[0]["msg"]

    async def test_active_subscription_adding_trial(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        assert subscription.current_period_end is not None

        trial_end = subscription.current_period_end + timedelta(days=14)

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.update_trial(
                session, ctx, subscription, trial_end=trial_end
            )

        assert updated_subscription.status == SubscriptionStatus.trialing
        assert updated_subscription.trial_end == trial_end
        assert updated_subscription.current_period_end == trial_end
        assert updated_subscription.trialing

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["trial_end"] == trial_end.isoformat()
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

    async def test_active_subscription_adding_trial_before_current_period_end(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        assert subscription.current_period_end is not None
        trial_end_before_period = subscription.current_period_end - timedelta(days=1)

        with pytest.raises(PolarRequestValidationError) as exc_info:
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.update_trial(
                    session, ctx, subscription, trial_end=trial_end_before_period
                )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "value_error"
        assert errors[0]["loc"] == ("body", "trial_end")
        assert "Trial end must be after the current period end" in errors[0]["msg"]


@pytest.mark.asyncio
async def test_send_past_due_email(
    mocker: MockerFixture,
    save_fixture: SaveFixture,
    session: AsyncSession,
    product: Product,
    customer: Customer,
) -> None:
    subscription = await create_subscription(
        save_fixture, product=product, customer=customer
    )

    await subscription_service.send_past_due_email(session, subscription)


@pytest.mark.asyncio
class TestMarkPastDue:
    """Test subscription service dunning functionality"""

    @freeze_time("2024-01-01 12:00:00")
    async def test_mark_past_due(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        subscription: Subscription,
        enqueue_job_mock: MagicMock,
    ) -> None:
        # Given
        subscription.status = SubscriptionStatus.active
        await save_fixture(subscription)

        # When
        result_subscription = await subscription_service.mark_past_due(
            session, subscription
        )

        # Then
        assert result_subscription.status == SubscriptionStatus.past_due

    @freeze_time("2024-01-01 12:00:00")
    async def test_mark_past_due_sends_email(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        save_fixture: SaveFixture,
        subscription: Subscription,
        enqueue_job_mock: MagicMock,
    ) -> None:
        # Given
        subscription.status = SubscriptionStatus.active
        await save_fixture(subscription)

        send_past_due_email_mock = mocker.patch.object(
            subscription_service, "send_past_due_email"
        )

        # When
        result_subscription = await subscription_service.mark_past_due(
            session, subscription
        )

        # Then
        assert result_subscription.status == SubscriptionStatus.past_due
        send_past_due_email_mock.assert_called_once_with(session, subscription)


@pytest.mark.asyncio
class TestUpdatePaymentMethodFromRetry:
    async def test_existing_method(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        product: Product,
    ) -> None:
        old_payment_method = PaymentMethod(
            processor=PaymentProcessor.crypto,
            processor_id="pm_old",
            type="card",
            customer=customer,
        )
        await save_fixture(old_payment_method)

        subscription = await create_active_subscription(
            save_fixture, product=product, customer=customer
        )
        subscription.payment_method = old_payment_method
        await save_fixture(subscription)

        # New payment method from retry
        new_payment_method = PaymentMethod(
            processor=PaymentProcessor.crypto,
            processor_id="pm_new",
            type="card",
            customer=customer,
        )
        await save_fixture(new_payment_method)

        # When
        updated_subscription = (
            await subscription_service.update_payment_method_from_retry(
                session, subscription, new_payment_method
            )
        )

        # But: Local subscription record is still updated
        assert updated_subscription.payment_method == new_payment_method

    async def test_subscription_without_payment_method(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        product: Product,
    ) -> None:
        # Given: Subscription without payment method
        subscription = await create_active_subscription(
            save_fixture, product=product, customer=customer
        )
        subscription.payment_method = None
        await save_fixture(subscription)

        # New payment method from retry
        new_payment_method = PaymentMethod(
            processor=PaymentProcessor.crypto,
            processor_id="pm_new",
            type="card",
            customer=customer,
        )
        await save_fixture(new_payment_method)

        # When
        updated_subscription = (
            await subscription_service.update_payment_method_from_retry(
                session, subscription, new_payment_method
            )
        )

        # And: Local subscription record is updated
        assert updated_subscription.payment_method == new_payment_method


@pytest.mark.asyncio
class TestUpdateBillingPeriod:
    async def test_basic(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        assert subscription.current_period_end is not None
        new_period_end = subscription.current_period_end + timedelta(days=7)

        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = (
                await subscription_service.update_currrent_billing_period_end(
                    session,
                    ctx,
                    subscription,
                    new_period_end=new_period_end,
                )
            )

        assert updated_subscription.current_period_end == new_period_end
        assert updated_subscription.anchor_day == new_period_end.day

        event_repository = EventRepository.from_session(session)
        events = await event_repository.get_all_by_name(
            SystemEvent.subscription_updated
        )
        assert len(events) == 1
        event = events[0]
        assert event.user_metadata["subscription_id"] == str(subscription.id)
        assert event.user_metadata["billing_period_end"] == new_period_end.isoformat()
        assert event.customer_id == customer.id
        assert event.organization_id == customer.organization_id

    async def test_canceled_subscription_raises(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_canceled_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        new_period_end = utc_now() + timedelta(days=30)

        with pytest.raises(AlreadyCanceledSubscription):
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.update_currrent_billing_period_end(
                    session,
                    ctx,
                    subscription,
                    new_period_end=new_period_end,
                )


@pytest.mark.asyncio
class TestCancelCustomer:
    async def test_basic(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        # Create one active subscription for the customer
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        # Cancel all customer subscriptions
        await subscription_service.cancel_customer(session, customer.id)

        # Verify subscription was canceled
        await session.refresh(subscription)
        assert subscription.status == SubscriptionStatus.canceled
        assert subscription.canceled_at is not None
        assert subscription.ended_at is not None


@pytest.mark.asyncio
class TestClearPendingUpdate:
    async def test_clear_pending_product_update(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        product_second: Product,
        customer: Customer,
    ) -> None:
        # Given: Subscription with a pending product update
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )
        subscription_update, _ = generate_subscription_update(
            subscription,
            SubscriptionProrationBehavior.next_period,
            product=product_second,
        )
        await save_fixture(subscription_update)
        subscription.pending_update = subscription_update
        await save_fixture(subscription)

        # When: Clear the pending update
        async with SubscriptionUpdateContext(
            session, subscription, subscription_service
        ) as ctx:
            updated_subscription = await subscription_service.clear_pending_update(
                session,
                ctx,
                subscription,
            )
        await save_fixture(updated_subscription)

        # Then: Pending update is cleared
        assert updated_subscription.pending_update is None

    async def test_clear_pending_update_no_pending(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        customer: Customer,
    ) -> None:
        # Given: Subscription with no pending update
        subscription = await create_active_subscription(
            save_fixture,
            product=product,
            customer=customer,
        )

        # When/Then: Should raise validation error
        with pytest.raises(PolarRequestValidationError) as exc_info:
            async with SubscriptionUpdateContext(
                session, subscription, subscription_service
            ) as ctx:
                await subscription_service.clear_pending_update(
                    session,
                    ctx,
                    subscription,
                )

        errors = exc_info.value.errors()
        assert len(errors) == 1
        assert errors[0]["type"] == "value_error"
        assert errors[0]["loc"] == ("body", "pending_update")
        assert "no pending update" in errors[0]["msg"]

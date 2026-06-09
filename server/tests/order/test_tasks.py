from datetime import timedelta

import pytest

from polar.config import settings
from polar.kit.db.postgres import AsyncSession
from polar.kit.utils import utc_now
from polar.models import Organization, Product
from polar.models.order import OrderBillingReasonInternal, OrderStatus
from polar.models.payment import PaymentStatus, PaymentTrigger
from polar.models.subscription import SubscriptionStatus
from polar.order.service import order as order_service
from polar.subscription.repository import SubscriptionRepository
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_customer,
    create_order,
    create_payment,
    create_payment_method,
    create_subscription,
)


@pytest.mark.asyncio
class TestProcessDunningOrder:
    async def test_payment_retry_success_clears_dunning_and_reactivates_subscription(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        organization: Organization,
    ) -> None:
        # Given: subscription is past due with pending retry
        customer = await create_customer(save_fixture, organization=organization)
        subscription = await create_subscription(
            save_fixture,
            product=product,
            customer=customer,
            status=SubscriptionStatus.past_due,
        )
        payment_method = await create_payment_method(save_fixture, customer=customer)
        subscription.payment_method = payment_method
        await save_fixture(subscription)

        past_time = utc_now() - timedelta(hours=1)
        order = await create_order(
            save_fixture,
            product=product,
            customer=customer,
            subscription=subscription,
            status=OrderStatus.pending,
            billing_reason=OrderBillingReasonInternal.subscription_cycle,
        )
        order.next_payment_attempt_at = past_time
        await save_fixture(order)

        payment = await create_payment(
            save_fixture,
            organization,
            status=PaymentStatus.succeeded,
            order=order,
        )

        # When: payment succeeds
        result_order = await order_service.handle_payment(session, order, payment)

        # Then: order is paid, dunning cleared, and subscription reactivated
        assert result_order.status == OrderStatus.paid
        assert result_order.next_payment_attempt_at is None

        from polar.subscription.repository import SubscriptionRepository

        subscription_repo = SubscriptionRepository.from_session(session)
        updated_subscription = await subscription_repo.get_by_id(subscription.id)
        assert updated_subscription is not None
        assert updated_subscription.status == SubscriptionStatus.active

    async def test_payment_retry_failure_schedules_next_attempt(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        organization: Organization,
    ) -> None:
        # Given: order already failed once and is due for first retry
        customer = await create_customer(save_fixture, organization=organization)
        subscription = await create_subscription(
            save_fixture,
            product=product,
            customer=customer,
            status=SubscriptionStatus.past_due,
        )

        first_retry_time = utc_now() - timedelta(hours=1)
        order = await create_order(
            save_fixture,
            product=product,
            customer=customer,
            subscription=subscription,
            status=OrderStatus.pending,
            billing_reason=OrderBillingReasonInternal.subscription_cycle,
        )
        order.next_payment_attempt_at = first_retry_time
        await save_fixture(order)

        await create_payment(
            save_fixture,
            organization,
            status=PaymentStatus.failed,
            trigger=PaymentTrigger.purchase,
            order=order,
        )
        await create_payment(
            save_fixture,
            organization,
            status=PaymentStatus.failed,
            trigger=PaymentTrigger.purchase,
            order=order,
        )

        # When: retry attempt fails
        result_order = await order_service.handle_payment_failure(session, order)

        # Then: next retry is scheduled according to second interval (5 days)
        assert result_order.next_payment_attempt_at is not None
        expected_next_attempt = utc_now() + timedelta(days=5)
        time_diff = abs(
            (
                result_order.next_payment_attempt_at - expected_next_attempt
            ).total_seconds()
        )
        assert time_diff < 1  # Within 1 second

    async def test_payment_retry_failure_final_attempt_marks_canceled(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product: Product,
        organization: Organization,
    ) -> None:
        # Given: order has exhausted all retry attempts
        customer = await create_customer(save_fixture, organization=organization)
        subscription = await create_subscription(
            save_fixture,
            product=product,
            customer=customer,
            status=SubscriptionStatus.past_due,
        )

        very_old_time = utc_now() - timedelta(days=30)
        order = await create_order(
            save_fixture,
            product=product,
            customer=customer,
            subscription=subscription,
            status=OrderStatus.pending,
            billing_reason=OrderBillingReasonInternal.subscription_cycle,
        )
        order.next_payment_attempt_at = very_old_time
        await save_fixture(order)

        # Initial cycle failure + all DUNNING_RETRY_INTERVALS retries failed
        for _ in range(len(settings.DUNNING_RETRY_INTERVALS) + 1):
            await create_payment(
                save_fixture,
                organization,
                status=PaymentStatus.failed,
                trigger=PaymentTrigger.purchase,
                order=order,
            )

        # When: final retry attempt fails
        result_order = await order_service.handle_payment_failure(session, order)

        # Then: order removed from dunning and subscription marked as canceled
        assert result_order.next_payment_attempt_at is None

        subscription_repo = SubscriptionRepository.from_session(session)
        updated_subscription = await subscription_repo.get_by_id(subscription.id)
        assert updated_subscription is not None
        assert updated_subscription.status == SubscriptionStatus.canceled

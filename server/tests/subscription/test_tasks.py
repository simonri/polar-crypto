import uuid

import pytest
from pytest_mock import MockerFixture

from polar.models import Customer, Product
from polar.postgres import AsyncSession
from polar.subscription.service import SubscriptionService
from polar.subscription.tasks import (  # type: ignore[attr-defined]
    SubscriptionDoesNotExist,
    subscription_enqueue_benefits_grants,
    subscription_service,
)
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import create_subscription


@pytest.mark.asyncio
class TestSubscriptionEnqueueBenefitsGrants:
    async def test_not_existing_subscription(self, session: AsyncSession) -> None:
        session.expunge_all()

        with pytest.raises(SubscriptionDoesNotExist):
            await subscription_enqueue_benefits_grants(uuid.uuid4())

    async def test_existing_subscription(
        self,
        mocker: MockerFixture,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product: Product,
        customer: Customer,
    ) -> None:
        subscription = await create_subscription(
            save_fixture, product=product, customer=customer
        )
        enqueue_benefits_grants_mock = mocker.patch.object(
            subscription_service,
            "enqueue_benefits_grants",
            spec=SubscriptionService.enqueue_benefits_grants,
        )

        session.expunge_all()

        await subscription_enqueue_benefits_grants(subscription.id)

        enqueue_benefits_grants_mock.assert_called_once()

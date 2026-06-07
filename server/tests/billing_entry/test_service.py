import pytest

from polar.billing_entry.service import billing_entry as billing_entry_service
from polar.event.system import SystemEvent
from polar.models import (
    BillingEntry,
    Customer,
    Order,
    OrderItem,
    Organization,
    Product,
    Subscription,
)
from polar.models.billing_entry import BillingEntryDirection, BillingEntryType
from polar.models.event import EventSource
from polar.postgres import AsyncSession
from polar.product.guard import (
    StaticPrice,
    is_custom_price,
    is_fixed_price,
    is_free_price,
)
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_active_subscription,
    create_event,
    create_order,
    create_product,
)


async def create_static_price_billing_entry(
    save_fixture: SaveFixture,
    *,
    type: BillingEntryType = BillingEntryType.cycle,
    customer: Customer,
    price: StaticPrice,
    subscription: Subscription,
    pending: bool = True,
    order: Order | None = None,
) -> BillingEntry:
    amount = 0
    if is_fixed_price(price):
        amount = price.price_amount
    elif is_free_price(price):
        amount = 0
    elif is_custom_price(price):
        raise NotImplementedError()

    event = await create_event(
        save_fixture,
        source=EventSource.system,
        name=SystemEvent.subscription_cycled,
        organization=customer.organization,
        customer=customer,
        metadata={"subscription_id": str(subscription.id)},
    )
    billing_entry = BillingEntry(
        start_timestamp=subscription.current_period_start,
        end_timestamp=subscription.current_period_end,
        type=type,
        direction=BillingEntryDirection.debit,
        customer=customer,
        product_price=price,
        subscription=subscription,
        event=event,
        amount=amount,
        currency=subscription.currency,
    )
    if not pending:
        assert order is not None, "Order must be provided if not pending"
        order_item = OrderItem(
            label="",
            amount=amount,
            net_amount=amount,
            product_price=price,
        )
        order.items.append(order_item)
        await save_fixture(order)
        billing_entry.order_item = order_item

    await save_fixture(billing_entry)
    return billing_entry


@pytest.mark.asyncio
class TestCreateOrderItemsFromPending:
    async def test_static_price(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        customer: Customer,
        product: Product,
        order: Order,
    ) -> None:
        subscription = await create_active_subscription(
            save_fixture, product=product, customer=customer
        )
        price = product.prices[0]
        assert is_fixed_price(price)

        entries = [
            await create_static_price_billing_entry(
                save_fixture,
                customer=customer,
                price=price,
                subscription=subscription,
                pending=False,
                order=order,
            ),
            await create_static_price_billing_entry(
                save_fixture,
                customer=customer,
                price=price,
                subscription=subscription,
                pending=True,
            ),
            await create_static_price_billing_entry(
                save_fixture,
                type=BillingEntryType.proration,
                customer=customer,
                price=price,
                subscription=subscription,
                pending=True,
            ),
        ]

        async with billing_entry_service.create_order_items_from_pending(
            session, subscription
        ) as order_items:
            assert len(order_items) == 2

            order_item_1 = order_items[0]
            assert product.name in order_item_1.label
            assert order_item_1.proration is False

            order_item_2 = order_items[1]
            assert product.name in order_item_2.label
            assert order_item_2.proration is True

            order = await create_order(
                save_fixture,
                customer=customer,
                order_items=list(order_items),
            )

        await session.refresh(entries[1])
        assert entries[1].order_item_id == order_item_1.id

        await session.refresh(entries[2])
        assert entries[2].order_item_id == order_item_2.id

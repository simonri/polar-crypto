import contextlib
import dataclasses
import uuid
from collections.abc import Sequence
from typing import cast

import structlog
from babel.dates import format_date
from typing_extensions import AsyncGenerator

from polar.models import BillingEntry, OrderItem, Subscription
from polar.models.billing_entry import BillingEntryDirection, BillingEntryType
from polar.postgres import AsyncSession
from polar.product.guard import StaticPrice
from polar.product.repository import ProductRepository

from .repository import BillingEntryRepository

log = structlog.get_logger(__name__)


@dataclasses.dataclass
class StaticLineItem:
    price: StaticPrice
    amount: int
    currency: str
    label: str
    proration: bool


class BillingEntryService:
    @contextlib.asynccontextmanager
    async def create_order_items_from_pending(
        self, session: AsyncSession, subscription: Subscription
    ) -> AsyncGenerator[Sequence[OrderItem]]:
        repository = BillingEntryRepository.from_session(session)
        await repository.lock_pending_by_subscription(subscription.id)

        item_entries_map: dict[OrderItem, Sequence[uuid.UUID]] = {}
        async for line_item, entries in self.compute_pending_subscription_line_items(
            session, subscription
        ):
            order_item = OrderItem(
                id=uuid.uuid4(),
                label=line_item.label,
                amount=line_item.amount,
                net_amount=line_item.amount,
                proration=line_item.proration,
                product_price=line_item.price,
            )
            item_entries_map[order_item] = entries

        yield list(item_entries_map.keys())

        repository = BillingEntryRepository.from_session(session)
        for order_item, entries in item_entries_map.items():
            await repository.update_order_item_id(entries, order_item.id)

    async def compute_pending_subscription_line_items(
        self, session: AsyncSession, subscription: Subscription
    ) -> AsyncGenerator[tuple[StaticLineItem, Sequence[uuid.UUID]]]:
        repository = BillingEntryRepository.from_session(session)

        async for entry in repository.get_static_pending_by_subscription(
            subscription.id
        ):
            static_price = cast(StaticPrice, entry.product_price)
            static_line_item = await self._get_static_price_line_item(
                session, static_price, entry
            )
            yield static_line_item, [entry.id]

    async def _get_static_price_line_item(
        self, session: AsyncSession, price: StaticPrice, entry: BillingEntry
    ) -> StaticLineItem:
        assert entry.amount is not None
        assert entry.currency is not None

        product_repository = ProductRepository.from_session(session)
        product = await product_repository.get_by_id(price.product_id)
        assert product is not None

        start = format_date(entry.start_timestamp.date(), locale="en_US")
        end = format_date(entry.end_timestamp.date(), locale="en_US")
        amount = entry.amount

        match entry.direction:
            case BillingEntryDirection.credit:
                label = f"Remaining time on {product.name} — From {start} to {end}"
                amount = -amount
            case BillingEntryDirection.debit:
                label = f"{product.name} — From {start} to {end}"
                amount = amount

        return StaticLineItem(
            price=price,
            amount=amount,
            currency=entry.currency,
            label=label,
            proration=entry.type
            in (
                BillingEntryType.proration,
                BillingEntryType.subscription_seats_increase,
                BillingEntryType.subscription_seats_decrease,
            ),
        )


billing_entry = BillingEntryService()

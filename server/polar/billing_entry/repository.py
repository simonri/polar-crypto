from collections.abc import AsyncGenerator, Sequence
from itertools import batched
from uuid import UUID

from sqlalchemy import Select, select, update
from sqlalchemy.orm.strategy_options import contains_eager

from polar.kit.repository import (
    Options,
    RepositoryBase,
    RepositorySoftDeletionIDMixin,
    RepositorySoftDeletionMixin,
)
from polar.models import BillingEntry
from polar.models.product_price import ProductPrice


class BillingEntryRepository(
    RepositorySoftDeletionIDMixin[BillingEntry, UUID],
    RepositorySoftDeletionMixin[BillingEntry],
    RepositoryBase[BillingEntry],
):
    model = BillingEntry

    async def update_order_item_id(
        self, billing_entries: Sequence[UUID], order_item_id: UUID
    ) -> None:
        for batch in batched(billing_entries, 1000):
            statement = (
                update(self.model)
                .where(
                    self.model.id.in_(batch),
                    self.model.order_item_id.is_(None),
                )
                .values(order_item_id=order_item_id)
            )
            await self.session.execute(statement)

    async def get_all_by_subscription(
        self, subscription_id: UUID
    ) -> Sequence[BillingEntry]:
        statement = select(self.model).where(
            self.model.subscription_id == subscription_id
        )
        return await self.get_all(statement)

    async def get_pending_by_subscription(
        self, subscription_id: UUID, *, options: Options = ()
    ) -> Sequence[BillingEntry]:
        statement = self.get_pending_by_subscription_statement(
            subscription_id, options=options
        )
        return await self.get_all(statement)

    async def get_static_pending_by_subscription(
        self, subscription_id: UUID
    ) -> AsyncGenerator[BillingEntry]:
        statement = (
            self.get_pending_by_subscription_statement(subscription_id)
            .join(BillingEntry.product_price)
            .where(ProductPrice.is_static.is_(True))
            .options(contains_eager(BillingEntry.product_price))
        )
        async for result in self.stream(statement):
            yield result

    async def get_pending_ids_by_subscription_and_price(
        self, subscription_id: UUID, product_price_id: UUID
    ) -> Sequence[UUID]:
        statement = (
            self.get_pending_by_subscription_statement(subscription_id)
            .with_only_columns(BillingEntry.id)
            .where(BillingEntry.product_price_id == product_price_id)
        )
        results = await self.session.execute(statement)
        return results.scalars().unique().all()

    async def lock_pending_by_subscription(self, subscription_id: UUID) -> None:
        """
        Acquire FOR UPDATE locks on all pending billing entries for a subscription.

        This prevents concurrent order creation from reading the same pending
        entries. With READ COMMITTED isolation, a blocked transaction will
        re-evaluate the WHERE clause after acquiring the lock and see that
        the entries are no longer pending.
        """
        statement = (
            self.get_pending_by_subscription_statement(subscription_id)
            .with_only_columns(BillingEntry.id)
            .with_for_update()
        )
        await self.session.execute(statement)

    def get_pending_by_subscription_statement(
        self, subscription_id: UUID, *, options: Options = ()
    ) -> Select[tuple["BillingEntry"]]:
        return (
            self.get_base_statement()
            .where(
                BillingEntry.order_item_id.is_(None),
                BillingEntry.subscription_id == subscription_id,
            )
            .order_by(BillingEntry.product_price_id.asc())
            .options(*options)
        )

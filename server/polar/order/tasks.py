import uuid

import structlog
from dramatiq import Retry

from polar.exceptions import PolarTaskError
from polar.logging import Logger
from polar.models.order import OrderBillingReasonInternal
from polar.subscription.repository import SubscriptionRepository
from polar.transaction.service.balance import PaymentTransactionForChargeDoesNotExist
from polar.worker import (
    AsyncSessionMaker,
    CronTrigger,
    TaskPriority,
    actor,
    can_retry,
)

from .repository import OrderRepository
from .service import (
    NoPendingBillingEntries,
    PaymentAlreadyInProgress,
)
from .service import order as order_service

log: Logger = structlog.get_logger()


class OrderTaskError(PolarTaskError): ...


class SubscriptionDoesNotExist(OrderTaskError):
    def __init__(self, subscription_id: uuid.UUID) -> None:
        self.subscription_id = subscription_id
        message = f"The subscription with id {subscription_id} does not exist."
        super().__init__(message)


class ProductDoesNotExist(OrderTaskError):
    def __init__(self, product_id: uuid.UUID) -> None:
        self.product_id = product_id
        message = f"The product with id {product_id} does not exist."
        super().__init__(message)


class OrderDoesNotExist(OrderTaskError):
    def __init__(self, order_id: uuid.UUID) -> None:
        self.order_id = order_id
        message = f"The order with id {order_id} does not exist."
        super().__init__(message)


@actor(actor_name="order.created", priority=TaskPriority.LOW)
async def order_created(order_id: uuid.UUID) -> None:
    async with AsyncSessionMaker() as session:
        repository = OrderRepository.from_session(session)
        order = await repository.get_by_id(
            order_id, options=repository.get_eager_options()
        )
        if order is None:
            raise OrderDoesNotExist(order_id)


@actor(actor_name="order.create_subscription_order", priority=TaskPriority.LOW)
async def create_subscription_order(
    subscription_id: uuid.UUID, order_reason: OrderBillingReasonInternal
) -> None:
    async with AsyncSessionMaker() as session:
        repository = SubscriptionRepository.from_session(session)
        subscription = await repository.get_by_id(
            subscription_id, options=repository.get_eager_options()
        )
        if subscription is None:
            raise SubscriptionDoesNotExist(subscription_id)

        try:
            await order_service.create_subscription_order(
                session, subscription, order_reason
            )
        except NoPendingBillingEntries:
            pass


@actor(actor_name="order.trigger_payment", priority=TaskPriority.LOW)
async def trigger_payment(
    order_id: uuid.UUID,
    payment_method_id: uuid.UUID,
    payment_trigger: str | None = None,
) -> None:
    # Stripe off-session payments removed — this task is now a no-op
    log.info(
        "order.trigger_payment.noop",
        order_id=str(order_id),
        note="Stripe payments removed; use crypto checkout instead",
    )


@actor(actor_name="order.balance", priority=TaskPriority.LOW)
async def create_order_balance(order_id: uuid.UUID, charge_id: str) -> None:
    async with AsyncSessionMaker() as session:
        repository = OrderRepository.from_session(session)
        order = await repository.get_by_id(
            order_id, options=repository.get_eager_options()
        )
        if order is None:
            raise OrderDoesNotExist(order_id)

        try:
            await order_service.create_order_balance(session, order, charge_id)
        except PaymentTransactionForChargeDoesNotExist as e:
            if can_retry():
                raise Retry() from e
            else:
                raise


@actor(actor_name="order.confirmation_email", priority=TaskPriority.LOW)
async def order_confirmation_email(order_id: uuid.UUID) -> None:
    async with AsyncSessionMaker() as session:
        repository = OrderRepository.from_session(session)
        order = await repository.get_by_id(
            order_id, options=repository.get_eager_options()
        )
        if order is None:
            raise OrderDoesNotExist(order_id)

        await order_service.send_confirmation_email(session, order)


@actor(
    actor_name="order.process_dunning",
    cron_trigger=CronTrigger.from_crontab("0 * * * *"),
    priority=TaskPriority.MEDIUM,
)
async def process_dunning() -> None:
    # Dunning removed (no card retries with crypto payments)
    pass


@actor(actor_name="order.process_dunning_order", priority=TaskPriority.MEDIUM)
async def process_dunning_order(order_id: uuid.UUID) -> None:
    # Dunning removed (no card retries with crypto payments)
    pass


@actor(
    actor_name="order.void_pending_orders_for_subscription", priority=TaskPriority.LOW
)
async def void_pending_orders_for_subscription(subscription_id: uuid.UUID) -> None:
    async with AsyncSessionMaker() as session:
        subscription_repository = SubscriptionRepository.from_session(session)
        subscription = await subscription_repository.get_by_id(
            subscription_id, options=subscription_repository.get_eager_options()
        )
        if subscription is None:
            raise SubscriptionDoesNotExist(subscription_id)

        try:
            await order_service.void_pending_orders_for_subscription(
                session, subscription
            )
        except PaymentAlreadyInProgress:
            if can_retry():
                raise Retry()
            raise

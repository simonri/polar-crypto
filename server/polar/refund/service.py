import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    pass
from uuid import UUID

import structlog

from polar.auth.models import AuthSubject
from polar.auth.permission import OrganizationPermission
from polar.authz.service import assert_resource_permission, get_accessible_org_ids
from polar.event.service import event as event_service
from polar.event.system import OrderRefundedMetadata, SystemEvent, build_system_event
from polar.exceptions import PolarError, PolarRequestValidationError, ResourceNotFound
from polar.kit.db.postgres import AsyncSession
from polar.kit.pagination import PaginationParams
from polar.kit.sorting import Sorting
from polar.logging import Logger
from polar.models import (
    Customer,
    Order,
    Organization,
    Payment,
    User,
)
from polar.models.order import RefundAmountTooHigh
from polar.models.refund import Refund, RefundStatus
from polar.models.webhook_endpoint import WebhookEventType
from polar.order.repository import OrderRepository
from polar.order.service import order as order_service
from polar.payment.repository import PaymentRepository
from polar.transaction.service.refund import (
    RefundTransactionAlreadyExistsError,
)
from polar.transaction.service.refund import (
    refund_transaction as refund_transaction_service,
)
from polar.wallet.service import wallet as wallet_service
from polar.webhook.service import webhook as webhook_service
from polar.worker import enqueue_job

from .repository import RefundRepository
from .schemas import RefundCreate
from .sorting import RefundSortProperty

log: Logger = structlog.get_logger()


class RefundError(PolarError): ...


class RefundUnknownPayment(ResourceNotFound):
    def __init__(
        self, id: str | UUID, payment_type: Literal["charge", "order", "pledge"]
    ) -> None:
        self.id = id
        message = f"Refund issued for unknown {payment_type}: {id}"
        super().__init__(message, 404)


class RefundedAlready(RefundError):
    def __init__(self, order: Order) -> None:
        self.order = order
        message = f"Order is already fully refunded: {order.id}"
        super().__init__(message, 403)


class RefundDisputedPayment(RefundError):
    def __init__(self, order: Order) -> None:
        self.order = order
        message = f"Refund cannot be issued for order with disputed payment: {order.id}"
        super().__init__(message, 403)


class RefundPendingCreation(RefundError):
    def __init__(self, refund_id: UUID) -> None:
        self.refund_id = refund_id
        message = f"Refund is pending creation: {refund_id}"
        super().__init__(message, 409)


class RevokeSubscriptionBenefitsProhibited(RefundError):
    def __init__(self) -> None:
        message = "Subscription benefits can only be revoked upon cancellation"
        super().__init__(message, 400)


class RefundsBlocked(RefundError):
    def __init__(self, order: Order) -> None:
        self.order = order
        message = f"Refunds are blocked for order: {order.id}"
        super().__init__(message, 403)


class MissingRelatedDispute(RefundError):
    def __init__(self, id: str, related_dispute_id: str) -> None:
        self.id = id
        self.related_dispute_id = related_dispute_id
        message = f"Refund {id} is missing related dispute {related_dispute_id}"
        super().__init__(message)


class RefundService:
    async def list(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        *,
        id: Sequence[UUID] | None = None,
        organization_id: Sequence[UUID] | None = None,
        order_id: Sequence[UUID] | None = None,
        subscription_id: Sequence[UUID] | None = None,
        customer_id: Sequence[UUID] | None = None,
        external_customer_id: Sequence[str] | None = None,
        succeeded: bool | None = None,
        pagination: PaginationParams,
        sorting: list[Sorting[RefundSortProperty]] = [
            (RefundSortProperty.created_at, True)
        ],
    ) -> tuple[Sequence[Refund], int]:
        repository = RefundRepository.from_session(session)
        org_ids = await get_accessible_org_ids(
            session, auth_subject, permission=OrganizationPermission.sales_read
        )
        statement = repository.get_statement_by_org_ids(org_ids)

        if id is not None:
            statement = statement.where(Refund.id.in_(id))

        if organization_id is not None:
            statement = statement.where(Refund.organization_id.in_(organization_id))

        if order_id is not None:
            statement = statement.where(Refund.order_id.in_(order_id))

        if subscription_id is not None:
            statement = statement.where(Refund.subscription_id.in_(subscription_id))

        if customer_id is not None:
            statement = statement.where(Refund.customer_id.in_(customer_id))

        if external_customer_id is not None:
            statement = statement.join(Customer).where(
                Customer.external_id.in_(external_customer_id)
            )

        if succeeded is not None:
            if succeeded:
                statement = statement.where(Refund.status == RefundStatus.succeeded)
            else:
                statement = statement.where(Refund.status != RefundStatus.succeeded)

        statement = repository.apply_sorting(statement, sorting).options(
            *repository.get_eager_options()
        )

        return await repository.paginate(
            statement, limit=pagination.limit, page=pagination.page
        )

    async def user_create(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        create_schema: RefundCreate,
    ) -> Refund:
        order_repository = OrderRepository.from_session(session)
        order = await order_repository.get_one_or_none(
            order_repository.get_readable_statement(auth_subject)
            .where(Order.id == create_schema.order_id)
            .options(*order_repository.get_eager_options())
        )
        if not order:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "order_id"),
                        "msg": "Order not found",
                        "input": create_schema.order_id,
                    }
                ]
            )

        await assert_resource_permission(
            session, auth_subject, order, OrganizationPermission.sales_manage
        )

        try:
            return await self.create(session, order, create_schema=create_schema)
        except RefundAmountTooHigh as e:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "amount"),
                        "msg": "Refund amount exceeds refundable amount",
                        "input": create_schema.amount,
                    }
                ]
            ) from e

    async def create(
        self, session: AsyncSession, order: Order, create_schema: RefundCreate
    ) -> Refund:
        repository = RefundRepository.from_session(session)

        if order.refunds_blocked or not order.organization.can_refund:
            raise RefundsBlocked(order)

        if order.refunded:
            raise RefundedAlready(order)

        is_subscription = order.subscription_id is not None
        if create_schema.revoke_benefits and is_subscription:
            raise RevokeSubscriptionBenefitsProhibited()

        refund_amount = create_schema.amount
        refund_id: UUID = uuid.uuid4()

        payment_repository = PaymentRepository.from_session(session)
        payment = await payment_repository.get_succeeded_by_order(order.id)
        if payment is None:
            raise RefundUnknownPayment(order.id, payment_type="order")

        # Crypto refunds: customers must supply a return wallet address.
        # The actual TX broadcast is handled by the CryptoPayoutAccountService.
        # For now create a pending refund record; the merchant triggers the TX.
        refund = Refund(
            id=refund_id,
            status=RefundStatus.pending,
            reason=create_schema.reason,
            amount=refund_amount,
            currency=order.currency,
            failure_reason=None,
            payment=payment,
            order=order,
            subscription=order.subscription,
            customer=order.customer,
            organization=order.organization,
            pledge=None,
            processor=payment.processor,
            processor_id=None,
            processor_receipt_number=None,
            processor_reason=create_schema.reason.value,
            processor_balance_transaction_id=None,
            revoke_benefits=create_schema.revoke_benefits,
        )

        refund.reason = create_schema.reason
        refund.comment = create_schema.comment
        refund.revoke_benefits = create_schema.revoke_benefits
        refund.user_metadata = create_schema.metadata
        refund = await repository.create(refund, flush=True)

        await self._on_created(session, refund)

        return refund

    async def upsert_from_stripe(self, *args: Any, **kwargs: Any) -> "Refund":
        raise NotImplementedError("Stripe refunds removed")

    async def create_from_stripe(self, *args: Any, **kwargs: Any) -> "Refund":
        raise NotImplementedError("Stripe refunds removed")

    async def update_from_stripe(self, *args: Any, **kwargs: Any) -> "Refund":
        raise NotImplementedError("Stripe refunds removed")

    async def create_from_dispute(self, *args: Any, **kwargs: Any) -> "Refund":
        raise NotImplementedError("Disputes removed")

    async def _on_created(self, session: AsyncSession, refund: Refund) -> None:
        order = refund.order
        customer = refund.customer
        organization = refund.organization
        assert order is not None
        assert customer is not None
        assert organization is not None

        await webhook_service.send(
            session, organization, WebhookEventType.refund_created, refund
        )

        if refund.succeeded:
            await self._on_succeeded(session, refund)

    async def _on_succeeded(
        self,
        session: AsyncSession,
        refund: Refund,
    ) -> None:
        try:
            await refund_transaction_service.create(session, refund=refund)
        except RefundTransactionAlreadyExistsError:
            pass

        order = refund.order
        if order is not None:
            await order_service.update_refunds(
                session,
                order,
                refunded_amount=refund.amount,
            )

            # Reduce positive customer balance
            customer_balance = await wallet_service.get_billing_wallet_balance(
                session, order.customer, order.currency, for_update=True
            )
            if customer_balance > 0:
                reduction_amount = min(customer_balance, order.refunded_amount)
                await wallet_service.create_balance_transaction(
                    session,
                    order.customer,
                    -reduction_amount,
                    order.currency,
                    order=order,
                )

            await event_service.create_event(
                session,
                build_system_event(
                    SystemEvent.order_refunded,
                    customer=order.customer,
                    organization=order.organization,
                    metadata=OrderRefundedMetadata(
                        order_id=str(order.id),
                        refunded_amount=order.refunded_amount,
                        currency=order.currency,
                    ),
                ),
            )

            # Send order.refunded
            await webhook_service.send(
                session, order.organization, WebhookEventType.order_refunded, order
            )

    async def _on_updated(self, session: AsyncSession, refund: Refund) -> None:
        if refund.organization is not None:
            await webhook_service.send(
                session, refund.organization, WebhookEventType.refund_updated, refund
            )

    async def _get_resources(self, *args: Any, **kwargs: Any) -> tuple[Order, Payment]:
        raise NotImplementedError("Stripe refund resource lookup removed")


refund = RefundService()

import uuid
from collections.abc import Sequence

from sqlalchemy import func, or_

from polar.auth.models import AuthSubject, Organization, User
from polar.auth.permission import OrganizationPermission
from polar.authz.service import get_accessible_org_ids
from polar.exceptions import PolarError
from polar.kit.pagination import PaginationParams
from polar.kit.sorting import Sorting
from polar.models import Checkout, Order, Payment
from polar.models.payment import PaymentStatus
from polar.postgres import AsyncReadSession

from .repository import PaymentRepository
from .sorting import PaymentSortProperty


class PaymentError(PolarError): ...


class UnlinkedPaymentError(PaymentError):
    def __init__(self, processor_id: str) -> None:
        self.processor_id = processor_id
        message = (
            f"Received a payment with id {processor_id} that is not linked "
            "to any checkout or order."
        )
        super().__init__(message)


class UnhandledPaymentIntent(PaymentError):
    def __init__(self, payment_intent_id: str) -> None:
        self.payment_intent_id = payment_intent_id
        message = (
            f"Received a payment intent with id {payment_intent_id} "
            "that we shouldn't handle."
        )
        super().__init__(message)


class PaymentService:
    async def list(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User | Organization],
        *,
        organization_id: Sequence[uuid.UUID] | None = None,
        checkout_id: Sequence[uuid.UUID] | None = None,
        order_id: Sequence[uuid.UUID] | None = None,
        customer_id: Sequence[uuid.UUID] | None = None,
        status: Sequence[PaymentStatus] | None = None,
        method: Sequence[str] | None = None,
        customer_email: Sequence[str] | None = None,
        pagination: PaginationParams,
        sorting: list[Sorting[PaymentSortProperty]] = [
            (PaymentSortProperty.created_at, True)
        ],
    ) -> tuple[Sequence[Payment], int]:
        repository = PaymentRepository.from_session(session)
        org_ids = await get_accessible_org_ids(
            session, auth_subject, permission=OrganizationPermission.sales_read
        )
        statement = repository.get_statement_by_org_ids(org_ids)

        if organization_id is not None:
            statement = statement.where(Payment.organization_id.in_(organization_id))

        if checkout_id is not None:
            statement = statement.where(Payment.checkout_id.in_(checkout_id))

        if order_id is not None:
            statement = statement.where(Payment.order_id.in_(order_id))

        if customer_id is not None:
            statement = statement.outerjoin(Order, Payment.order_id == Order.id)
            statement = statement.outerjoin(
                Checkout, Payment.checkout_id == Checkout.id
            )
            effective_customer_id = func.coalesce(
                Order.customer_id, Checkout.customer_id
            )

            statement = statement.where(
                effective_customer_id.in_(customer_id),
                or_(Order.is_deleted.is_(False), Order.id.is_(None)),
            )

        if status is not None:
            statement = statement.where(Payment.status.in_(status))

        if method is not None:
            statement = statement.where(Payment.method.in_(method))

        if customer_email is not None:
            statement = statement.where(Payment.customer_email.in_(customer_email))

        statement = repository.apply_sorting(statement, sorting)

        return await repository.paginate(
            statement, limit=pagination.limit, page=pagination.page
        )

    async def get(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User | Organization],
        id: uuid.UUID,
    ) -> Payment | None:
        repository = PaymentRepository.from_session(session)
        org_ids = await get_accessible_org_ids(
            session, auth_subject, permission=OrganizationPermission.sales_read
        )
        statement = repository.get_statement_by_org_ids(org_ids).where(Payment.id == id)
        return await repository.get_one_or_none(statement)


payment = PaymentService()

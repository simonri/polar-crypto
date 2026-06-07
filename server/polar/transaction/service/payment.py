from uuid import UUID

from sqlalchemy import select

from polar.models import Transaction
from polar.models.transaction import TransactionType
from polar.postgres import AsyncSession

from .base import BaseTransactionService, BaseTransactionServiceError


class PaymentTransactionError(BaseTransactionServiceError): ...


class BalanceTransactionNotAvailableError(PaymentTransactionError):
    def __init__(self, charge_id: str) -> None:
        message = f"Balance transaction not available for charge {charge_id}"
        super().__init__(message)


class PaymentTransactionService(BaseTransactionService):
    async def get_by_charge_id(
        self, session: AsyncSession, charge_id: str
    ) -> Transaction | None:
        statement = select(Transaction).where(
            Transaction.type == TransactionType.payment,
            Transaction.charge_id == charge_id,
        )
        result = await session.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_order_id(
        self, session: AsyncSession, order_id: UUID
    ) -> Transaction | None:
        return await self.get_by(
            session,
            type=TransactionType.payment,
            order_id=order_id,
        )

    async def create_payment(self, *args: object, **kwargs: object) -> Transaction:
        raise NotImplementedError("Stripe charge-based payment transactions removed")


payment_transaction = PaymentTransactionService(Transaction)

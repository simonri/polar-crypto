from polar.models import Refund, Transaction
from polar.models.transaction import ProcessorFeeType
from polar.postgres import AsyncSession

from .base import BaseTransactionService, BaseTransactionServiceError


class ProcessorFeeTransactionError(BaseTransactionServiceError): ...


class BalanceTransactionNotFound(ProcessorFeeTransactionError):
    def __init__(self, payment_transaction: Transaction) -> None:
        message = (
            f"Balance transaction not found for payment transaction "
            f"{payment_transaction.id} with charge ID {payment_transaction.charge_id}"
        )
        super().__init__(message)


class UnsupportedStripeFeeType(ProcessorFeeTransactionError):
    def __init__(self, description: str) -> None:
        self.description = description
        message = f"Unsupported Stripe fee type: {description}"
        super().__init__(message)


def _get_stripe_processor_fee_type(description: str) -> ProcessorFeeType:
    description = description.lower()
    if "payout fee" in description or "account volume" in description:
        return ProcessorFeeType.payout
    if "cross-border transfers" in description:
        return ProcessorFeeType.cross_border_transfer
    if "active account" in description:
        return ProcessorFeeType.account
    if "billing" in description:
        return ProcessorFeeType.subscription
    if (
        "automatic tax" in description
        or "tax api calculation" in description
        or "tax api transaction" in description
    ):
        return ProcessorFeeType.tax
    if "invoicing" in description or "post payment invoices" in description:
        return ProcessorFeeType.invoice
    # Instant Bank Account Validation for ACH payments
    if "connections verification" in description:
        return ProcessorFeeType.payment
    if "radar" in description:
        return ProcessorFeeType.security
    if "3d secure" in description:
        return ProcessorFeeType.payment
    if "authorization optimization" in description:
        return ProcessorFeeType.payment
    if "card account updater" in description:
        return ProcessorFeeType.payment
    if "tax reporting for connect" in description:
        return ProcessorFeeType.tax
    if "identity document check" in description:
        return ProcessorFeeType.security
    if "payments" in description:
        return ProcessorFeeType.payment
    if "card dispute countered fee" in description:
        return ProcessorFeeType.dispute
    if "smart disputes" in description:
        return ProcessorFeeType.dispute
    if "card" in description:
        # Strange fee that popped-up in Feb 2026 for Dec 2024: txn_1SzVb9DG1jUQrXwCPzHjBjhs
        return ProcessorFeeType.payment
    raise UnsupportedStripeFeeType(description)


class ProcessorFeeTransactionService(BaseTransactionService):
    async def create_payment_fees(
        self, session: AsyncSession, *, payment_transaction: Transaction
    ) -> list[Transaction]:
        # Crypto payments: no processor fee transactions from Stripe
        return []

    async def create_refund_fees(
        self,
        session: AsyncSession,
        *,
        refund: Refund,
        refund_transaction: Transaction,
    ) -> list[Transaction]:
        # Crypto refunds: no Stripe fee transactions
        return []

    async def create_dispute_fees(
        self, *args: object, **kwargs: object
    ) -> list[Transaction]:
        return []

    async def sync_stripe_fees(
        self, *args: object, **kwargs: object
    ) -> list[Transaction]:
        return []


processor_fee_transaction = ProcessorFeeTransactionService(Transaction)

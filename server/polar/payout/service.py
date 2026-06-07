import datetime
import uuid
from collections.abc import AsyncIterable, Sequence
from typing import Any

import structlog

from polar.auth.models import AuthSubject, User
from polar.auth.permission import OrganizationPermission
from polar.authz.service import get_accessible_org_ids
from polar.config import settings
from polar.exceptions import PolarError
from polar.kit.csv import IterableCSVWriter
from polar.kit.currency import format_currency
from polar.kit.db.postgres import AsyncSessionMaker
from polar.kit.pagination import PaginationParams
from polar.kit.sorting import Sorting
from polar.kit.utils import utc_now
from polar.locker import Locker
from polar.logging import Logger
from polar.models import Account, Organization, Payout, PayoutAttempt
from polar.models.organization import OrganizationStatus
from polar.models.payout import PayoutStatus
from polar.postgres import AsyncReadSession, AsyncSession
from polar.transaction.repository import (
    PayoutTransactionRepository,
    TransactionRepository,
)
from polar.transaction.service.payout import (
    payout_transaction as payout_transaction_service,
)
from polar.transaction.service.platform_fee import PayoutAmountTooLow
from polar.transaction.service.platform_fee import (
    platform_fee_transaction as platform_fee_transaction_service,
)
from polar.transaction.service.transaction import transaction as transaction_service
from polar.worker import enqueue_job

from .repository import PayoutRepository
from .schemas import PayoutEstimate
from .sorting import PayoutSortProperty

log: Logger = structlog.get_logger()

# Currencies that Stripe treats as zero-decimal for payouts, even though they
# technically have smaller units. For these currencies, payout amounts must be
# in whole units (amounts in our internal representation must end with "00").
# See: https://docs.stripe.com/currencies#special-cases
_STRIPE_PAYOUT_ZERO_DECIMAL_CURRENCIES: frozenset[str] = frozenset(
    {"isk", "huf", "twd", "ugx"}
)


def _adjust_payout_amount_for_zero_decimal_currency(
    amount: int, currency: str
) -> tuple[int, int]:
    """Adjust a payout amount for zero-decimal currencies.

    For currencies like ISK, HUF, TWD, and UGX, Stripe requires payout amounts
    to be in whole units. This function rounds down the amount to the nearest
    valid value (multiple of 100 in our internal cents representation).

    Args:
        amount: The amount in smallest currency units (cents).
        currency: The currency code (e.g., "isk", "huf").

    Returns:
        A tuple of (adjusted_amount, remainder) where:
        - adjusted_amount: The amount rounded down to be valid for Stripe payouts
        - remainder: The amount that could not be paid out (0-99)
    """
    if currency.lower() not in _STRIPE_PAYOUT_ZERO_DECIMAL_CURRENCIES:
        return amount, 0

    remainder = amount % 100
    adjusted_amount = amount - remainder
    return adjusted_amount, remainder


class PayoutError(PolarError): ...


class OrganizationCannotPayout(PayoutError):
    """Raised when an organization is not allowed to request a payout.

    The message reflects the actual organization status. `REVIEW` and `SNOOZED`
    orgs can request a payout (held until approval) and never raise this; only
    `CREATED`, `DENIED`, `OFFBOARDING` and `BLOCKED` do.
    """

    def __init__(self, organization: Organization) -> None:
        self.organization = organization
        match organization.status:
            case OrganizationStatus.CREATED:
                message = (
                    "Your organization isn't active yet. "
                    "Payouts will be available once it's approved."
                )
            case OrganizationStatus.DENIED:
                message = (
                    "Your organization's review was denied, "
                    "so payouts aren't available."
                )
            case OrganizationStatus.OFFBOARDING:
                message = (
                    "Your organization is being offboarded, "
                    "so payouts aren't available."
                )
            case OrganizationStatus.BLOCKED:
                message = "Your organization is blocked, so payouts aren't available."
            case _:
                message = "Your organization can't request a payout at the moment."
        super().__init__(message, 403)


class InsufficientBalance(PayoutError):
    def __init__(
        self, account: Account, balance: int, *, minimum_amount: int | None = None
    ) -> None:
        self.account = account
        self.balance = balance
        message = "You have an insufficient balance to make a payout."
        if minimum_amount is not None:
            formatted = format_currency(minimum_amount, "usd")
            message += f" The minimum withdrawal amount is {formatted}."
        super().__init__(message, 400)


class PayoutAmountTooLarge(PayoutError):
    def __init__(self, payout: Payout, account_amount: int) -> None:
        self.payout = payout
        self.account_amount = account_amount
        message = f"Payout amount {account_amount} is too large for payout {payout.id}."
        super().__init__(message, 400)


class PayoutAccountInsufficientBalance(PayoutError):
    def __init__(self, payout: Payout) -> None:
        self.payout = payout
        message = f"The payout account for payout {payout.id} doesn't have enough balance to make the payout yet."
        super().__init__(message, 400)


class PendingPayoutCreation(PayoutError):
    def __init__(self, account: Account) -> None:
        self.account = account
        message = f"A payout is already being created for the account {account.id}."
        super().__init__(message, 409)


class PayoutIntervalLimitReached(PayoutError):
    def __init__(self, account: Account, interval: datetime.timedelta) -> None:
        self.account = account
        self.interval = interval
        hours = max(1, int(interval.total_seconds() // 3600))
        message = f"You can only request a payout once per {hours} hours."
        super().__init__(message, 400)


class PayoutAttemptDoesNotExist(PayoutError):
    def __init__(self, payout_id: str) -> None:
        self.payout_id = payout_id
        message = (
            f"Received payout {payout_id} from Stripe, "
            "but it's not associated to a Payout."
        )
        super().__init__(message, 404)


class PayoutAlreadyTriggered(PayoutError):
    def __init__(self, payout: Payout) -> None:
        self.payout = payout
        message = f"Payout {payout.id} has already been triggered."
        super().__init__(message)


class PayoutCanceled(PayoutError):
    def __init__(self, payout: Payout) -> None:
        self.payout = payout
        message = f"Payout {payout.id} has been canceled and cannot be retried."
        super().__init__(message, 400)


class PayoutNotCancelable(PayoutError):
    def __init__(self, payout: Payout) -> None:
        self.payout = payout
        message = (
            f"Payout {payout.id} cannot be canceled because of its current status."
        )
        super().__init__(message)


class NoSyncableAttempt(PayoutError):
    def __init__(self, payout: Payout) -> None:
        self.payout = payout
        message = (
            f"Payout {payout.id} has no attempt with a provider ID that can be synced."
        )
        super().__init__(message, 400)


class PayoutService:
    async def list(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        *,
        account_id: Sequence[uuid.UUID] | None = None,
        status: Sequence[PayoutStatus] | None = None,
        pagination: PaginationParams,
        sorting: list[Sorting[PayoutSortProperty]] = [
            (PayoutSortProperty.created_at, False)
        ],
    ) -> tuple[Sequence[Payout], int]:
        repository = PayoutRepository.from_session(session)
        org_ids = await get_accessible_org_ids(
            session, auth_subject, permission=OrganizationPermission.finance_read
        )
        statement = repository.get_statement_by_org_ids(org_ids).options(
            *repository.get_eager_options()
        )

        if account_id is not None:
            statement = statement.where(Payout.account_id.in_(account_id))

        if status is not None:
            statement = statement.where(Payout.status.in_(status))

        statement = repository.apply_sorting(statement, sorting)

        return await repository.paginate(
            statement, limit=pagination.limit, page=pagination.page
        )

    async def get(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User],
        id: uuid.UUID,
        *,
        permission: OrganizationPermission = OrganizationPermission.finance_read,
    ) -> Payout | None:
        repository = PayoutRepository.from_session(session)
        org_ids = await get_accessible_org_ids(
            session, auth_subject, permission=permission
        )
        statement = (
            repository.get_statement_by_org_ids(org_ids)
            .where(Payout.id == id)
            .options(*repository.get_eager_options())
        )
        return await repository.get_one_or_none(statement)

    async def estimate(
        self, session: AsyncSession, organization: Organization
    ) -> PayoutEstimate:
        if not organization.can_payout:
            raise OrganizationCannotPayout(organization)

        account = organization.account
        payout_account = organization.get_ready_payout_account()

        summary = await transaction_service.get_summary(session, account)
        balance_amount = summary.available_balance.amount
        minimum_amount = settings.get_minimum_payout(
            payout_account.currency, payout_account.country
        )
        if balance_amount < minimum_amount:
            raise InsufficientBalance(
                account, balance_amount, minimum_amount=minimum_amount
            )

        try:
            payout_fees = await platform_fee_transaction_service.get_payout_fees(
                session,
                account=account,
                payout_account=payout_account,
                balance_amount=balance_amount,
            )
        except PayoutAmountTooLow as e:
            raise InsufficientBalance(account, balance_amount) from e

        return PayoutEstimate(
            account_id=account.id,
            payout_account_id=payout_account.id,
            gross_amount=balance_amount,
            fees_amount=sum(fee for _, fee in payout_fees),
            net_amount=balance_amount - sum(fee for _, fee in payout_fees),
        )

    async def get_next_payout_at(
        self,
        session: AsyncReadSession,
        account: Account,
    ) -> datetime.datetime | None:
        """Earliest time a new payout can be requested for ``account``.

        Returns ``None`` when a payout can be requested immediately.
        """
        repository = PayoutRepository.from_session(session)
        latest_payout = await repository.get_latest_by_account(account.id)
        if latest_payout is None:
            return None
        next_at = latest_payout.created_at + account.payout_interval
        if next_at <= utc_now():
            return None
        return next_at

    async def create(
        self, session: AsyncSession, locker: Locker, organization: Organization
    ) -> Payout:
        account = organization.account

        lock_name = f"payout:{account.id}"
        if await locker.is_locked(lock_name):
            raise PendingPayoutCreation(account)

        async with locker.lock(lock_name, timeout=60, blocking_timeout=1):
            # Lock the org row so a concurrent approval can't land between the
            # status read and the payout insert and strand a held payout on an
            # already-active org. Refresh only status/capabilities to keep the
            # eager-loaded relationships.
            await session.refresh(
                organization,
                attribute_names=["status", "capabilities"],
                with_for_update=True,
            )

            if not organization.can_payout:
                raise OrganizationCannotPayout(organization)

            held = organization.status in (
                OrganizationStatus.REVIEW,
                OrganizationStatus.SNOOZED,
            )

            next_payout_at = await self.get_next_payout_at(session, account)
            if next_payout_at is not None:
                raise PayoutIntervalLimitReached(account, account.payout_interval)

            payout_account = organization.get_ready_payout_account()

            summary = await transaction_service.get_summary(session, account)
            balance_amount = summary.available_balance.amount
            minimum_amount = settings.get_minimum_payout(
                payout_account.currency, payout_account.country
            )
            if balance_amount < minimum_amount:
                raise InsufficientBalance(
                    account, balance_amount, minimum_amount=minimum_amount
                )

            try:
                (
                    balance_amount_after_fees,
                    payout_fees_balances,
                ) = await platform_fee_transaction_service.create_payout_fees_balances(
                    session,
                    account=account,
                    payout_account=payout_account,
                    balance_amount=balance_amount,
                )
            except PayoutAmountTooLow as e:
                raise InsufficientBalance(account, balance_amount) from e

            repository = PayoutRepository.from_session(session)
            payout = await repository.create(
                Payout(
                    processor=payout_account.type,
                    status=PayoutStatus.held if held else PayoutStatus.pending,
                    currency="usd",  # FIXME: Main Polar currency
                    amount=balance_amount_after_fees,
                    fees_amount=balance_amount - balance_amount_after_fees,
                    account_currency=payout_account.currency,
                    account_amount=balance_amount_after_fees,
                    account=account,
                    payout_account=payout_account,
                    attempts=[],
                )
            )
            transaction = await payout_transaction_service.create(
                session, payout, payout_fees_balances
            )

            if payout.currency != payout.account_currency:
                await repository.update(
                    payout,
                    update_dict={"account_amount": -transaction.account_amount},
                )

            # A held payout's transfer is deferred until release_held_payouts.
            enqueue_job("payout.created", payout_id=payout.id)
            if not held:
                enqueue_job("payout.transfer", payout_id=payout.id)

            return payout

    async def update_from_stripe(self, *args: Any, **kwargs: Any) -> "PayoutAttempt":
        raise NotImplementedError("Stripe payouts removed")

    async def sync_with_provider(self, *args: Any, **kwargs: Any) -> "PayoutAttempt":
        raise NotImplementedError("Stripe payouts removed")

    async def trigger_stripe_payouts(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("Stripe payouts removed")

    async def trigger_stripe_payout(self, *args: Any, **kwargs: Any) -> "PayoutAttempt":
        raise NotImplementedError("Stripe payouts removed")

    async def cancel(self, session: AsyncSession, payout: Payout) -> Payout:
        # Lock + re-read before reversing: serializes concurrent cancels (e.g. a
        # backoffice cancel racing cancel_pending_payouts) so they can't each
        # write a reversal and double-credit the merchant.
        await session.refresh(payout, attribute_names=["status"], with_for_update=True)
        if not payout.status.is_cancelable():
            raise PayoutNotCancelable(payout)

        payout_transaction = payout.transaction
        await payout_transaction_service.reverse(session, payout_transaction)

        repository = PayoutRepository.from_session(session)
        return await repository.update(
            payout, update_dict={"status": PayoutStatus.canceled}
        )

    async def get_csv(
        self, session: AsyncSession, sessionmaker: AsyncSessionMaker, payout: Payout
    ) -> AsyncIterable[str]:
        payout_transaction_repository = PayoutTransactionRepository.from_session(
            session
        )
        payout_transaction = await payout_transaction_repository.get_by_payout_id(
            payout.id
        )
        assert payout_transaction is not None

        transaction_repository = TransactionRepository.from_session(session)
        statement = transaction_repository.get_paid_transactions_statement(
            payout_transaction.id
        )

        csv_writer = IterableCSVWriter(dialect="excel")
        yield csv_writer.getrow(
            (
                "Date",
                "Payout ID",
                "Transaction ID",
                "Description",
                "Currency",
                "Amount",
                "Payout Total",
                "Account Currency",
                "Account Payout Total",
            )
        )

        # StreamingResponse is running its own async task to exhaust the iterator
        # Thus, rely on the main session generated by the FastAPI dependency leads to
        # garbage collection problems.
        # We create a new session to avoid this.
        async with sessionmaker() as sub_session:
            transactions = await sub_session.stream_scalars(
                statement,
                execution_options={"yield_per": settings.DATABASE_STREAM_YIELD_PER},
            )
            async for transaction in transactions:
                description = ""
                if transaction.platform_fee_type is not None:
                    if transaction.platform_fee_type == "platform":
                        description = "Polar fee"
                    else:
                        description = (
                            f"Payment processor fee ({transaction.platform_fee_type})"
                        )
                elif transaction.pledge is not None:
                    description = f"Pledge to {transaction.pledge.issue_reference}"
                elif transaction.order is not None:
                    description = transaction.order.description

                transaction_id = (
                    str(transaction.id)
                    if transaction.incurred_by_transaction_id is None
                    else str(transaction.incurred_by_transaction_id)
                )

                yield csv_writer.getrow(
                    (
                        transaction.created_at.isoformat(),
                        str(payout.id),
                        transaction_id,
                        description,
                        transaction.currency,
                        transaction.amount / 100,
                        abs(payout.amount / 100),
                        payout.account_currency,
                        abs(payout.account_amount / 100),
                    )
                )


payout = PayoutService()

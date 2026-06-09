import datetime
from datetime import timedelta
from functools import partial
from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from polar.config import settings
from polar.enums import PayoutAccountType
from polar.kit.utils import utc_now
from polar.locker import Locker
from polar.models import Account, Organization, Transaction, User
from polar.models.organization import OrganizationStatus, PayoutAccountNotReady
from polar.models.payout import PayoutStatus
from polar.models.transaction import Processor, TransactionType
from polar.payout.repository import PayoutRepository
from polar.payout.service import (
    InsufficientBalance,
    OrganizationCannotPayout,
    PayoutIntervalLimitReached,
    PayoutNotCancelable,
)
from polar.payout.service import payout as payout_service
from polar.postgres import AsyncSession
from polar.transaction.service.payout import (
    PayoutTransactionService,
)
from tests.fixtures import random_objects as ro
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_account,
    create_payout,
    create_payout_account,
)
from tests.transaction.conftest import create_transaction


@pytest.fixture(autouse=True)
def payout_transaction_service_mock(mocker: MockerFixture) -> MagicMock:
    mock = MagicMock(spec=PayoutTransactionService)
    mocker.patch("polar.payout.service.payout_transaction_service", new=mock)
    return mock


ten_days_ago = utc_now() - timedelta(days=10)
create_payment_transaction = partial(
    ro.create_payment_transaction, amount=10000, created_at=ten_days_ago
)
create_refund_transaction = partial(
    ro.create_refund_transaction, amount=-10000, created_at=ten_days_ago
)
create_balance_transaction = partial(
    ro.create_balance_transaction, amount=10000, created_at=ten_days_ago
)


@pytest.mark.asyncio
class TestCreate:
    @pytest.mark.parametrize(
        ("currency", "country", "balance"),
        [
            ("usd", "US", -1000),
            ("usd", "US", 0),
            ("usd", "US", settings.get_minimum_payout("usd", "US") - 1),
            ("eur", "FR", -1000),
            ("eur", "FR", 0),
            ("eur", "FR", settings.get_minimum_payout("eur", "FR") - 1),
            # Country-specific minimum (Panama: $50 USD) dominates the
            # currency-based USD default ($10).
            ("usd", "PA", settings.get_minimum_payout("usd", "PA") - 1),
        ],
    )
    async def test_insufficient_balance(
        self,
        currency: str,
        country: str,
        balance: int,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        account: Account,
        user: User,
    ) -> None:
        payout_account = await create_payout_account(
            save_fixture, organization, user, currency=currency, country=country
        )
        await create_balance_transaction(save_fixture, account=account, amount=balance)

        with pytest.raises(InsufficientBalance):
            await payout_service.create(session, locker, organization)

    async def test_missing_payout_account(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        account: Account,
        user: User,
    ) -> None:
        with pytest.raises(PayoutAccountNotReady):
            await payout_service.create(session, locker, organization)

    async def test_disabled_payout_account(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        account: Account,
        user: User,
    ) -> None:
        payout_account = await create_payout_account(
            save_fixture,
            organization,
            user,
            type=PayoutAccountType.manual,
            is_payouts_enabled=False,
        )

        with pytest.raises(PayoutAccountNotReady):
            await payout_service.create(session, locker, organization)

    @pytest.mark.parametrize(
        "status",
        [
            OrganizationStatus.CREATED,
            OrganizationStatus.DENIED,
            OrganizationStatus.OFFBOARDING,
            OrganizationStatus.BLOCKED,
        ],
    )
    async def test_organization_cannot_payout(
        self,
        status: OrganizationStatus,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        account: Account,
        user: User,
    ) -> None:
        # Bypass set_status's transition validation to seed any starting status.
        organization.status = status
        organization.set_status(status)
        await save_fixture(organization)

        payout_account = await create_payout_account(save_fixture, organization, user)

        payment_transaction_1 = await create_payment_transaction(save_fixture)
        balance_transaction_1 = await create_balance_transaction(
            save_fixture, account=account, payment_transaction=payment_transaction_1
        )

        payment_transaction_2 = await create_payment_transaction(save_fixture)
        balance_transaction_2 = await create_balance_transaction(
            save_fixture, account=account, payment_transaction=payment_transaction_2
        )

        with pytest.raises(OrganizationCannotPayout):
            await payout_service.create(session, locker, organization)

    @pytest.mark.parametrize(
        "status",
        [OrganizationStatus.REVIEW, OrganizationStatus.SNOOZED],
    )
    async def test_held_for_organization_under_review(
        self,
        status: OrganizationStatus,
        mocker: MockerFixture,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        account: Account,
        user: User,
        payout_transaction_service_mock: MagicMock,
    ) -> None:
        # A REVIEW/SNOOZED org can request a payout: it is reserved and held
        # until the org is approved, instead of being blocked.
        enqueue_job_mock = mocker.patch("polar.payout.service.enqueue_job")

        organization.status = status
        organization.set_status(status)
        await save_fixture(organization)

        await create_payout_account(save_fixture, organization, user)

        payment_transaction_1 = await create_payment_transaction(save_fixture)
        await create_balance_transaction(
            save_fixture, account=account, payment_transaction=payment_transaction_1
        )

        payout_transaction_service_mock.create.return_value = Transaction()

        payout = await payout_service.create(session, locker, organization)

        assert payout.status == PayoutStatus.held
        assert payout.amount > 0

        # The created event fires, but the Stripe transfer is held back until
        # the org is approved.
        enqueue_job_mock.assert_called_once_with("payout.created", payout_id=payout.id)

    async def test_active_enqueues_created_and_transfer(
        self,
        mocker: MockerFixture,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        account: Account,
        user: User,
        payout_transaction_service_mock: MagicMock,
    ) -> None:
        # The default organization fixture is ACTIVE: the payout is pending and
        # both the created event and the Stripe transfer are enqueued.
        enqueue_job_mock = mocker.patch("polar.payout.service.enqueue_job")

        await create_payout_account(save_fixture, organization, user)

        payment_transaction_1 = await create_payment_transaction(save_fixture)
        await create_balance_transaction(
            save_fixture, account=account, payment_transaction=payment_transaction_1
        )

        payout_transaction_service_mock.create.return_value = Transaction()

        payout = await payout_service.create(session, locker, organization)

        assert payout.status == PayoutStatus.pending
        assert enqueue_job_mock.call_count == 2
        enqueue_job_mock.assert_any_call("payout.created", payout_id=payout.id)
        enqueue_job_mock.assert_any_call("payout.transfer", payout_id=payout.id)

    async def test_valid(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        user: User,
        account: Account,
        payout_transaction_service_mock: MagicMock,
    ) -> None:
        payout_account = await create_payout_account(save_fixture, organization, user)

        payment_transaction_1 = await create_payment_transaction(save_fixture)
        balance_transaction_1 = await create_balance_transaction(
            save_fixture, account=account, payment_transaction=payment_transaction_1
        )

        payment_transaction_2 = await create_payment_transaction(save_fixture)
        balance_transaction_2 = await create_balance_transaction(
            save_fixture, account=account, payment_transaction=payment_transaction_2
        )

        payout_transaction_service_mock.create.return_value = Transaction()

        payout = await payout_service.create(session, locker, organization)

        assert payout.account == account
        assert payout.payout_account == payout_account
        assert payout.processor == payout_account.type
        assert payout.currency == "usd"
        assert payout.amount > 0
        assert payout.fees_amount >= 0
        assert payout.account_currency == "usd"
        assert payout.account_amount > 0

        payout_transaction_service_mock.create.assert_called_once()

    async def test_available_balance_with_delay(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        user: User,
        account: Account,
        payout_transaction_service_mock: MagicMock,
    ) -> None:
        payout_account = await create_payout_account(save_fixture, organization, user)

        now = utc_now()

        # Create an old balance transaction (8 days ago - should be available)
        payment_transaction_old = await create_payment_transaction(
            save_fixture, created_at=now - timedelta(days=8)
        )
        balance_transaction_old = await create_balance_transaction(
            save_fixture,
            account=account,
            payment_transaction=payment_transaction_old,
            created_at=now - timedelta(days=8),
        )

        # Create a recent balance transaction (2 days ago - should NOT be available)
        payment_transaction_recent = await create_payment_transaction(
            save_fixture, created_at=now - timedelta(days=2)
        )
        balance_transaction_recent = await create_balance_transaction(
            save_fixture,
            account=account,
            payment_transaction=payment_transaction_recent,
            created_at=now - timedelta(days=2),
        )

        payout_transaction_service_mock.create.return_value = Transaction()

        payout = await payout_service.create(session, locker, organization)

        assert payout.account == account
        assert payout.payout_account == payout_account
        # The payout amount should only include the old balance (10000) that's available
        # The recent balance (10000) is excluded because it's only 2 days old (< 7 days)
        # So we expect payout amount to be based on available_balance = 10000 (from old balance only)
        assert payout.amount == 10000 - payout.fees_amount
        assert payout.account_amount == 10000 - payout.fees_amount

        payout_transaction_service_mock.create.assert_called_once()

    async def test_valid_different_currencies(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        user: User,
        account: Account,
        payout_transaction_service_mock: MagicMock,
    ) -> None:
        payout_account = await create_payout_account(
            save_fixture,
            organization,
            user,
            type=PayoutAccountType.manual,
            country="FR",
            currency="eur",
        )

        payment_transaction_1 = await create_payment_transaction(save_fixture)
        balance_transaction_1 = await create_balance_transaction(
            save_fixture, account=account, payment_transaction=payment_transaction_1
        )

        payment_transaction_2 = await create_payment_transaction(save_fixture)
        balance_transaction_2 = await create_balance_transaction(
            save_fixture, account=account, payment_transaction=payment_transaction_2
        )

        payout_transaction_service_mock.create.return_value = Transaction(
            account_currency="eur", account_amount=-1000
        )

        payout = await payout_service.create(session, locker, organization)

        assert payout.account == account
        assert payout.payout_account == payout_account
        assert payout.processor == payout_account.type
        assert payout.account_currency == "eur"
        assert payout.account_amount == 1000

        payout_transaction_service_mock.create.assert_called_once()

    async def test_recent_payout_within_24h(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        user: User,
        account: Account,
    ) -> None:
        payout_account = await create_payout_account(save_fixture, organization, user)

        await create_payout(
            save_fixture,
            account=account,
            payout_account=payout_account,
            created_at=utc_now() - datetime.timedelta(hours=1),
        )

        with pytest.raises(PayoutIntervalLimitReached):
            await payout_service.create(session, locker, organization)

    async def test_previous_payout_older_than_24h(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        locker: Locker,
        organization: Organization,
        user: User,
        account: Account,
        payout_transaction_service_mock: MagicMock,
    ) -> None:
        payout_account = await create_payout_account(save_fixture, organization, user)

        await create_payout(
            save_fixture,
            account=account,
            payout_account=payout_account,
            created_at=utc_now() - datetime.timedelta(hours=25),
        )

        payment_transaction_1 = await create_payment_transaction(save_fixture)
        await create_balance_transaction(
            save_fixture, account=account, payment_transaction=payment_transaction_1
        )

        payout_transaction_service_mock.create.return_value = Transaction()

        payout = await payout_service.create(session, locker, organization)

        assert payout.account == account


@pytest.mark.asyncio
class TestEstimate:
    async def test_regular_currency(
        self,
        mocker: MockerFixture,
        save_fixture: SaveFixture,
        session: AsyncSession,
        organization: Organization,
        account: Account,
        user: User,
    ) -> None:
        """Test that regular currencies return net_amount unchanged."""
        mocker.patch(
            "polar.payout.service.platform_fee_transaction_service.get_payout_fees",
            return_value=[],
        )

        await create_payout_account(save_fixture, organization, user, currency="usd")

        await create_balance_transaction(save_fixture, account=account, amount=12345)

        estimate = await payout_service.estimate(session, organization)

        assert estimate.gross_amount == 12345
        assert estimate.net_amount == 12345


@pytest.mark.asyncio
class TestGetByIdForUpdate:
    async def test_locks_and_eager_loads(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        user: User,
    ) -> None:
        # FOR UPDATE OF payouts must lock only the payout row so the eager-load
        # joins (account, payout_account, transactions) don't trip the
        # nullable-outer-join lock error. Exercises the real SQL on Postgres.
        account = await create_account(save_fixture, user)
        payout_account = await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )
        payout = await create_payout(
            save_fixture, account=account, payout_account=payout_account
        )
        await create_transaction(
            save_fixture,
            account=account,
            type=TransactionType.payout,
            amount=-payout.amount,
            account_currency=account.currency,
            payout=payout,
        )

        repository = PayoutRepository.from_session(session)
        locked = await repository.get_by_id_for_update(
            payout.id, options=repository.get_eager_options()
        )

        assert locked is not None
        assert locked.id == payout.id
        # Relationships resolve without a lazy load, confirming eager loading.
        assert locked.account.id == account.id
        assert locked.payout_account.id == payout_account.id


@pytest.mark.asyncio
class TestCancel:
    async def test_not_cancelable(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        organization: Organization,
        user: User,
    ) -> None:
        account = await create_account(save_fixture, user)
        payout_account = await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )
        payout = await create_payout(
            save_fixture,
            account=account,
            payout_account=payout_account,
            status=PayoutStatus.succeeded,
        )

        with pytest.raises(PayoutNotCancelable):
            await payout_service.cancel(session, payout)

    async def test_valid(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        organization: Organization,
        user: User,
        payout_transaction_service_mock: MagicMock,
    ) -> None:
        account = await create_account(save_fixture, user)
        payout_account = await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )
        payout = await create_payout(
            save_fixture,
            account=account,
            payout_account=payout_account,
            status=PayoutStatus.pending,
            attempts=[],
        )
        payout_transaction = Transaction(
            type=TransactionType.payout,
            account=account,
            processor=Processor.crypto,
            currency=payout.currency,
            amount=payout.amount,
            account_currency=payout.account_currency,
            account_amount=payout.account_amount,
            pledge=None,
            issue_reward=None,
            order=None,
            paid_transactions=[],
            incurred_transactions=[],
            account_incurred_transactions=[],
            payout=payout,
        )
        await save_fixture(payout_transaction)

        payout_transaction_service_mock.reverse.return_value = Transaction()

        canceled_payout = await payout_service.cancel(session, payout)

        assert canceled_payout.status == PayoutStatus.canceled

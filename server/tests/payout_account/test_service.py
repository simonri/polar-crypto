from unittest.mock import MagicMock

import pytest
from pytest_mock import MockerFixture

from polar.auth.models import AuthSubject
from polar.enums import PayoutAccountType
from polar.models import Organization, User
from polar.models.payout_attempt import PayoutAttemptStatus
from polar.payout_account.service import (
    PayoutAccountHasPendingPayouts,
    PayoutAccountLinkedToOrganization,
)
from polar.payout_account.service import (
    payout_account as payout_account_service,
)
from polar.postgres import AsyncSession
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_account,
    create_payout,
    create_payout_account,
)


@pytest.fixture(autouse=True)
def stripe_service_mock(mocker: MockerFixture) -> MagicMock:
    mock = MagicMock()
    mocker.patch("polar.payout_account.service.stripe", new=mock, create=True)
    return mock


@pytest.mark.asyncio
class TestDelete:
    @pytest.mark.auth
    async def test_linked_to_organization_raises_error(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        auth_subject: AuthSubject[User],
        organization: Organization,
        user: User,
    ) -> None:
        """Cannot delete a payout account linked to an organization."""
        payout_account = await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )

        with pytest.raises(PayoutAccountLinkedToOrganization):
            await payout_account_service.delete(session, payout_account)

    @pytest.mark.auth
    @pytest.mark.parametrize(
        "attempt_status", [PayoutAttemptStatus.pending, PayoutAttemptStatus.in_transit]
    )
    async def test_pending_payouts_raises_error(
        self,
        attempt_status: PayoutAttemptStatus,
        session: AsyncSession,
        save_fixture: SaveFixture,
        auth_subject: AuthSubject[User],
        organization: Organization,
        user: User,
    ) -> None:
        """Cannot delete a payout account that has pending or in-transit payouts."""
        payout_account = await create_payout_account(
            save_fixture, organization, user, type=PayoutAccountType.manual
        )
        # Unlink from org first so we get past the linked check
        organization.payout_account = None
        await save_fixture(organization)

        account = await create_account(save_fixture, user)
        await create_payout(
            save_fixture,
            payout_account=payout_account,
            account=account,
            attempts=[attempt_status],
        )

        with pytest.raises(PayoutAccountHasPendingPayouts):
            await payout_account_service.delete(session, payout_account)

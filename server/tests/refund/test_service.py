from typing import Any
from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient, Response
from pytest_mock import MockerFixture

from polar.models import (
    Customer,
    Order,
    Organization,
    Product,
    Transaction,
)
from polar.models.order import OrderStatus
from polar.models.refund import RefundReason
from polar.order.repository import OrderRepository
from polar.postgres import AsyncSession
from polar.refund.schemas import RefundCreate
from polar.refund.service import refund as refund_service
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_order,
    create_order_and_payment,
)


def build_stripe_refund(**kwargs):
    """Stub - Stripe removed."""
    return MagicMock(**kwargs)


class StripeRefund:
    """Helper mixin for refund endpoint tests (Stripe removed; crypto flow)."""

    async def create(
        self,
        client: AsyncClient,
        stripe_service_mock: MagicMock,
        order: Order,
        transaction: Transaction,
        create_schema: RefundCreate,
        *,
        refund_amount: int,
    ) -> Response:
        response = await client.post(
            "/v1/refunds/",
            json={
                "order_id": str(create_schema.order_id),
                "reason": str(create_schema.reason),
                "amount": refund_amount,
            },
        )
        return response

    async def create_and_assert(
        self,
        client: AsyncClient,
        stripe_service_mock: MagicMock,
        order: Order,
        transaction: Transaction,
        create_schema: RefundCreate,
        expected: dict[str, Any] | None = None,
    ) -> Response:
        refund_amount = create_schema.amount
        response = await self.create(
            client,
            stripe_service_mock,
            order,
            transaction,
            create_schema,
            refund_amount=refund_amount,
        )

        assert response.status_code == 201
        if expected:
            data = response.json()
            for k, v in expected.items():
                assert data[k] == v

        return response

    async def create_order_refund(
        self,
        session: AsyncSession,
        client: AsyncClient,
        stripe_service_mock: MagicMock,
        order: Order,
        transaction: Transaction,
        *,
        amount: int,
    ) -> tuple[Order, Response]:
        response = await self.create_and_assert(
            client,
            stripe_service_mock,
            order,
            transaction,
            RefundCreate(
                order_id=order.id,
                reason=RefundReason.service_disruption,
                amount=amount,
                comment=None,
                revoke_benefits=False,
            ),
        )

        order_repository = OrderRepository.from_session(session)
        updated = await order_repository.get_by_id(order.id)
        assert updated is not None
        return updated, response


@pytest.fixture(autouse=True)
def stripe_service_mock(mocker: MockerFixture) -> MagicMock:
    mock = MagicMock()
    mocker.patch("polar.refund.service.stripe_service", new=mock, create=True)
    return mock


@pytest.fixture(autouse=True)
def refund_transaction_service_mock(mocker: MockerFixture) -> MagicMock:
    mock = mocker.patch(
        "polar.refund.service.refund_transaction_service", autospec=True
    )
    return mock


@pytest.mark.asyncio
class TestCreate:
    pass


@pytest.mark.asyncio
class TestOrganizationRefundsBlocked:
    async def test_create_refund_blocked_by_organization(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        product: Product,
        customer: Customer,
    ) -> None:
        """Test that refunds are blocked when the refunds capability is disabled."""
        from polar.organization.repository import OrganizationRepository

        org_repository = OrganizationRepository.from_session(session)
        organization = await org_repository.update(
            organization,
            update_dict={
                "capabilities": {
                    **organization.capabilities,
                    "refunds": False,
                },
            },
        )

        # Create an order
        order = await create_order(
            save_fixture,
            product=product,
            customer=customer,
            status=OrderStatus.paid,
        )

        # Try to create a refund
        create_schema = RefundCreate(
            order_id=order.id,
            amount=100,
            reason=RefundReason.customer_request,
        )

        from polar.refund.service import RefundsBlocked

        # Should raise RefundsBlocked exception
        with pytest.raises(RefundsBlocked) as exc_info:
            await refund_service.create(session, order, create_schema)

        assert exc_info.value.order.id == order.id

    async def test_create_refund_allowed_when_organization_not_blocked(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        stripe_service_mock: MagicMock,
        product: Product,
        customer: Customer,
    ) -> None:
        """Test that refunds work normally when the refunds capability is enabled."""
        order, payment, _transaction = await create_order_and_payment(
            save_fixture,
            product=product,
            customer=customer,
            subtotal_amount=100,
        )

        # Update order to paid status
        from polar.order.repository import OrderRepository

        order_repository = OrderRepository.from_session(session)
        order = await order_repository.update(
            order, update_dict={"status": OrderStatus.paid}
        )

        # Mock Stripe refund creation
        stripe_service_mock.create_refund.return_value = build_stripe_refund(
            amount=100,
            charge_id=payment.processor_id,
        )

        # Create a refund (should succeed)
        create_schema = RefundCreate(
            order_id=order.id,
            amount=100,
            reason=RefundReason.customer_request,
        )

        refund = await refund_service.create(session, order, create_schema)

        # Verify refund was created
        assert refund is not None
        assert refund.order_id == order.id

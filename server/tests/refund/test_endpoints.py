from unittest.mock import MagicMock

import pytest
from httpx import AsyncClient
from pytest_mock import MockerFixture

from polar.auth.scope import Scope
from polar.kit.utils import generate_uuid
from polar.models import (
    Customer,
    Order,
    Organization,
    Payment,
    Product,
    Subscription,
    Transaction,
    UserOrganization,
)
from polar.models.dispute import DisputeStatus
from polar.models.order import OrderStatus
from polar.models.refund import RefundReason, RefundStatus
from polar.models.user_organization import OrganizationRole
from polar.order.repository import OrderRepository
from polar.postgres import AsyncSession
from polar.refund.schemas import RefundCreate
from tests.fixtures import random_objects as ro
from tests.fixtures.auth import AuthSubjectFixture
from tests.fixtures.database import SaveFixture

from .test_service import StripeRefund


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


async def create_order_and_payment(
    save_fixture: SaveFixture,
    *,
    product: Product,
    customer: Customer,
    amount: int,
    subscription: Subscription | None = None,
) -> tuple[Order, Payment, Transaction]:
    order = await ro.create_order(
        save_fixture,
        product=product,
        customer=customer,
        subtotal_amount=amount,
        subscription=subscription,
    )
    payment = await ro.create_payment(
        save_fixture, product.organization, amount=amount, order=order
    )
    transaction = await ro.create_payment_transaction(
        save_fixture,
        amount=amount,
        order=order,
        charge_id=payment.processor_id,
    )
    return order, payment, transaction


@pytest.mark.asyncio
class TestListRefunds(StripeRefund):
    async def seed_refunds(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        client: AsyncClient,
        organization: Organization,
        user_organization: UserOrganization,  # makes User a member of Organization
        product_organization_second: Product,
        stripe_service_mock: MagicMock,
        product: Product,
        customer: Customer,
        customer_second: Customer,
        customer_organization_second: Customer,
    ) -> tuple[Order, Order, Order]:
        order, payment, _ = await create_order_and_payment(
            save_fixture,
            product=product,
            customer=customer,
            amount=1000,
        )
        order_second, payment_second, _ = await create_order_and_payment(
            save_fixture,
            product=product,
            customer=customer,
            amount=1000,
        )
        order_second_org, payment_second_org, _ = await create_order_and_payment(
            save_fixture,
            product=product_organization_second,
            customer=customer_organization_second,
            amount=1000,
        )

        def refund_id() -> str:
            id = generate_uuid()
            return f"re_{id}"

        # First order
        await ro.create_refund(
            save_fixture,
            order,
            payment,
            status=RefundStatus.pending,
            amount=80,
            processor_id=refund_id(),
        )
        await ro.create_refund(
            save_fixture,
            order,
            payment,
            status=RefundStatus.succeeded,
            amount=80,
            processor_id=refund_id(),
        )
        await ro.create_refund(
            save_fixture,
            order,
            payment,
            status=RefundStatus.succeeded,
            amount=160,
            processor_id=refund_id(),
        )
        await ro.create_refund(
            save_fixture,
            order,
            payment,
            status=RefundStatus.succeeded,
            amount=160,
            processor_id=refund_id(),
        )
        # Second order
        await ro.create_refund(
            save_fixture,
            order_second,
            payment_second,
            status=RefundStatus.succeeded,
            amount=240,
            processor_id=refund_id(),
        )
        await ro.create_refund(
            save_fixture,
            order_second,
            payment_second,
            status=RefundStatus.succeeded,
            amount=240,
            processor_id=refund_id(),
        )
        dispute = await ro.create_dispute(
            save_fixture,
            order=order_second,
            payment=payment_second,
            status=DisputeStatus.prevented,
        )
        await ro.create_refund(
            save_fixture,
            order_second,
            payment_second,
            status=RefundStatus.succeeded,
            reason=RefundReason.dispute_prevention,
            amount=240,
            processor_id=refund_id(),
            dispute=dispute,
        )

        # Second organization order
        await ro.create_refund(
            save_fixture,
            order_second_org,
            payment_second_org,
            status=RefundStatus.succeeded,
            amount=1000,
            processor_id=refund_id(),
        )

        return order, order_second, order_second_org

    async def test_anonymous(
        self, client: AsyncClient, organization: Organization
    ) -> None:
        response = await client.get("/v1/refunds/")
        assert response.status_code == 401

    @pytest.mark.auth
    async def test_valid(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        client: AsyncClient,
        organization: Organization,
        user_organization: UserOrganization,  # makes User a member of Organization
        product_organization_second: Product,
        stripe_service_mock: MagicMock,
        product: Product,
        customer: Customer,
        customer_second: Customer,
        customer_organization_second: Customer,
    ) -> None:
        order, order_second, _ = await self.seed_refunds(
            session,
            save_fixture,
            client,
            organization,
            user_organization,
            product_organization_second,
            stripe_service_mock,
            product,
            customer,
            customer_second,
            customer_organization_second,
        )

        # Get all for organization
        response = await client.get("/v1/refunds/")
        json = response.json()
        assert json["pagination"]["total_count"] == 7

        # Get all succeeded for first order
        response = await client.get(
            "/v1/refunds/",
            params={
                "order_id": str(order.id),
                "succeeded": True,
            },
        )
        json = response.json()
        assert json["pagination"]["total_count"] == 3

        # Get non-succeeded refunds
        response = await client.get(
            "/v1/refunds/",
            params={
                "succeeded": False,
            },
        )
        json = response.json()
        assert json["pagination"]["total_count"] == 1

        # Get all for first order regardless of status
        response = await client.get(
            "/v1/refunds/",
            params={
                "order_id": str(order.id),
            },
        )
        json = response.json()
        assert json["pagination"]["total_count"] == 4

        # Get all for second order
        response = await client.get(
            "/v1/refunds/",
            params={
                "order_id": str(order_second.id),
                "succeeded": True,
            },
        )
        json = response.json()
        assert json["pagination"]["total_count"] == 3


@pytest.mark.asyncio
class TestCreateRefunds(StripeRefund):
    async def test_anonymous(
        self, client: AsyncClient, organization: Organization
    ) -> None:
        response = await client.post("/v1/refunds/")
        assert response.status_code == 401

    @pytest.mark.auth
    async def test_tampered(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        client: AsyncClient,
        organization: Organization,
        user_organization: UserOrganization,  # makes User a member of Organization
        product_organization_second: Product,
        stripe_service_mock: MagicMock,
        product: Product,
        customer_organization_second: Customer,
    ) -> None:
        order, payment, transaction = await create_order_and_payment(
            save_fixture,
            product=product_organization_second,
            customer=customer_organization_second,
            amount=1000,
        )

        response = await self.create(
            client,
            stripe_service_mock,
            order,
            transaction,
            RefundCreate(
                order_id=order.id,
                reason=RefundReason.service_disruption,
                amount=500,
                comment=None,
                revoke_benefits=False,
            ),
            refund_amount=500,
        )
        assert response.status_code == 422

        order_repository = OrderRepository.from_session(session)
        updated = await order_repository.get_by_id(order.id)
        assert updated is not None

        assert updated.status == OrderStatus.paid
        assert updated.refunded_amount == 0

    @pytest.mark.auth(
        AuthSubjectFixture(scopes={Scope.refunds_write}),
    )
    async def test_valid_partial_to_full(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        client: AsyncClient,
        organization: Organization,
        user_organization: UserOrganization,  # makes User a member of Organization
        stripe_service_mock: MagicMock,
        product: Product,
        customer: Customer,
    ) -> None:
        order, payment, transaction = await create_order_and_payment(
            save_fixture,
            product=product,
            customer=customer,
            amount=9_990,
        )

        order, response = await self.create_order_refund(
            session,
            client,
            stripe_service_mock,
            order,
            transaction,
            amount=1110,
        )
        assert order.status == OrderStatus.partially_refunded

        # 8_880 remaining
        order, response = await self.create_order_refund(
            session,
            client,
            stripe_service_mock,
            order,
            transaction,
            amount=993,
        )
        assert order.status == OrderStatus.partially_refunded

        # 7_887 remaining
        order, response = await self.create_order_refund(
            session,
            client,
            stripe_service_mock,
            order,
            transaction,
            amount=5887,
        )
        assert order.status == OrderStatus.partially_refunded

        # 2_000 remaining
        amount_before_exceed_attempt = order.refunded_amount
        response = await self.create(
            client,
            stripe_service_mock,
            order,
            transaction,
            RefundCreate(
                order_id=order.id,
                reason=RefundReason.service_disruption,
                amount=2001,
                comment=None,
                revoke_benefits=False,
            ),
            refund_amount=2001,
        )
        assert response.status_code == 422

        order_repository = OrderRepository.from_session(session)
        updated = await order_repository.get_by_id(order.id)
        assert updated is not None
        assert updated.refunded_amount == amount_before_exceed_attempt
        assert updated.refundable_amount == 2000

        # Still 2_000 remaining
        order, response = await self.create_order_refund(
            session,
            client,
            stripe_service_mock,
            order,
            transaction,
            amount=2000,
        )
        assert order.status == OrderStatus.refunded
        assert order.refunded

    @pytest.mark.auth(
        AuthSubjectFixture(scopes={Scope.refunds_write}),
    )
    async def test_valid_full_refund(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        client: AsyncClient,
        organization: Organization,
        user_organization: UserOrganization,  # makes User a member of Organization
        stripe_service_mock: MagicMock,
        product: Product,
        customer: Customer,
    ) -> None:
        order_amount = 2000
        order, payment, transaction = await create_order_and_payment(
            save_fixture,
            product=product,
            customer=customer,
            amount=order_amount,
        )

        assert not order.refunded
        assert order.status == OrderStatus.paid

        order, response = await self.create_order_refund(
            session,
            client,
            stripe_service_mock,
            order,
            transaction,
            amount=order_amount,
        )
        assert order.status == OrderStatus.refunded
        assert order.refunded_amount == order_amount
        assert order.refunded

    @pytest.mark.auth(
        AuthSubjectFixture(scopes={Scope.refunds_write}),
    )
    async def test_member_role_can_create_refund(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        client: AsyncClient,
        organization: Organization,
        user_organization: UserOrganization,
        stripe_service_mock: MagicMock,
        product: Product,
        customer: Customer,
    ) -> None:
        user_organization.role = OrganizationRole.member
        await save_fixture(user_organization)

        order, payment, transaction = await create_order_and_payment(
            save_fixture,
            product=product,
            customer=customer,
            amount=2000,
        )

        order, _ = await self.create_order_refund(
            session,
            client,
            stripe_service_mock,
            order,
            transaction,
            amount=2000,
        )
        assert order.status == OrderStatus.refunded

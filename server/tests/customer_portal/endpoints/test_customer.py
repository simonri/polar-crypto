from unittest.mock import MagicMock

import pytest
import pytest_asyncio
from httpx import AsyncClient
from pytest_mock import MockerFixture

from polar.config import settings
from polar.customer_email_update.service import TOKEN_PREFIX
from polar.kit.crypto import generate_token_hash_pair
from polar.models import Customer, Organization
from polar.models.customer_email_verification import CustomerEmailVerification
from polar.postgres import AsyncSession
from tests.fixtures.auth import CUSTOMER_AUTH_SUBJECT
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_customer,
)


@pytest.fixture(autouse=True)
def stripe_service_mock(mocker: MockerFixture) -> MagicMock:
    mock = MagicMock()
    mocker.patch(
        "polar.customer_email_update.service.stripe_service", new=mock, create=True
    )
    mocker.patch(
        "polar.customer_portal.service.customer.stripe_service", new=mock, create=True
    )
    return mock


@pytest_asyncio.fixture
async def organization_allow_email_change(
    save_fixture: SaveFixture, organization: Organization
) -> Organization:
    organization.customer_portal_settings = {
        **organization.customer_portal_settings,
        "customer": {"allow_email_change": True},
    }
    await save_fixture(organization)
    return organization


async def _create_verification(
    save_fixture: SaveFixture,
    customer: Customer,
    email: str = "new@example.com",
) -> tuple[CustomerEmailVerification, str]:
    token, token_hash = generate_token_hash_pair(
        secret=settings.SECRET, prefix=TOKEN_PREFIX
    )
    record = CustomerEmailVerification(
        email=email,
        token_hash=token_hash,
        customer_id=customer.id,
        organization_id=customer.organization_id,
    )
    await save_fixture(record)
    return record, token


@pytest.mark.asyncio
class TestRequestEmailUpdate:
    async def test_anonymous(self, client: AsyncClient) -> None:
        response = await client.post(
            "/v1/customer-portal/customers/me/email-update/request",
            json={"email": "new@example.com"},
        )
        assert response.status_code == 401

    @pytest.mark.auth(CUSTOMER_AUTH_SUBJECT)
    @pytest.mark.keep_session_state
    async def test_not_allowed(
        self,
        client: AsyncClient,
        customer: Customer,
    ) -> None:
        response = await client.post(
            "/v1/customer-portal/customers/me/email-update/request",
            json={"email": "brand-new@example.com"},
        )
        assert response.status_code == 403

    @pytest.mark.auth(CUSTOMER_AUTH_SUBJECT)
    @pytest.mark.keep_session_state
    async def test_request_email_update(
        self,
        client: AsyncClient,
        mocker: MockerFixture,
        customer: Customer,
        organization_allow_email_change: Organization,
    ) -> None:
        mocker.patch("polar.customer_email_update.service.enqueue_email_template")
        response = await client.post(
            "/v1/customer-portal/customers/me/email-update/request",
            json={"email": "brand-new@example.com"},
        )
        assert response.status_code == 202

    @pytest.mark.auth(CUSTOMER_AUTH_SUBJECT)
    @pytest.mark.keep_session_state
    async def test_same_email(
        self,
        client: AsyncClient,
        customer: Customer,
        organization_allow_email_change: Organization,
    ) -> None:
        response = await client.post(
            "/v1/customer-portal/customers/me/email-update/request",
            json={"email": customer.email},
        )
        assert response.status_code == 422


@pytest.mark.asyncio
class TestCheckEmailUpdate:
    async def test_valid_token(
        self,
        client: AsyncClient,
        save_fixture: SaveFixture,
        customer: Customer,
    ) -> None:
        _record, token = await _create_verification(save_fixture, customer)
        response = await client.get(
            "/v1/customer-portal/customers/me/email-update/check",
            params={"token": token},
        )
        assert response.status_code == 204

    async def test_invalid_token(self, client: AsyncClient) -> None:
        response = await client.get(
            "/v1/customer-portal/customers/me/email-update/check",
            params={"token": "polar_cev_bogus"},
        )
        assert response.status_code == 401


@pytest.mark.asyncio
class TestVerifyEmailUpdate:
    async def test_invalid_token(self, client: AsyncClient) -> None:
        response = await client.post(
            "/v1/customer-portal/customers/me/email-update/verify",
            json={"token": "polar_cev_bogus"},
        )
        assert response.status_code == 401

    @pytest.mark.keep_session_state
    async def test_verify_success(
        self,
        client: AsyncClient,
        session: AsyncSession,
        save_fixture: SaveFixture,
        customer: Customer,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch("polar.customer_email_update.service.enqueue_email_template")
        _record, token = await _create_verification(
            save_fixture, customer, "verified@example.com"
        )
        response = await client.post(
            "/v1/customer-portal/customers/me/email-update/verify",
            json={"token": token},
        )
        assert response.status_code == 200
        data = response.json()
        assert "token" in data
        assert len(data["token"]) > 0

    @pytest.mark.keep_session_state
    async def test_verify_email_taken(
        self,
        client: AsyncClient,
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        customer: Customer,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch("polar.customer_email_update.service.enqueue_email_template")
        # Create another customer with the target email
        await create_customer(
            save_fixture,
            organization=organization,
            email="taken@example.com",
        )

        _record, token = await _create_verification(
            save_fixture, customer, "taken@example.com"
        )
        response = await client.post(
            "/v1/customer-portal/customers/me/email-update/verify",
            json={"token": token},
        )
        assert response.status_code == 422

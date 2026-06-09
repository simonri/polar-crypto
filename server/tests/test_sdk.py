from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from polar_sdk import Polar

from polar.models import Organization, UserOrganization


@pytest_asyncio.fixture
async def polar(app: FastAPI) -> AsyncGenerator[Polar]:
    async with AsyncClient(transport=ASGITransport(app=app)) as client:
        yield Polar(access_token="", async_client=client)


@pytest.mark.asyncio
@pytest.mark.auth
class TestSDK:
    """
    Those tests are here to ensure we do not introduce changes to the API that would break the SDK.

    Basically, we just run queries against our ASGI app to see if the SDK is able to parse the response without errors.
    """

    async def test_get_organization(
        self,
        polar: Polar,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        response = await polar.organizations.get_async(id=str(organization.id))
        assert response is not None

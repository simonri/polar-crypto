"""
External service mocks for E2E tests.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pytest_mock import MockerFixture

ALLOWED_HOSTS = {"test", "localhost", "127.0.0.1"}


@pytest.fixture(autouse=True)
def _block_external_http(mocker: MockerFixture) -> None:
    """Reject any HTTP call to a non-localhost host."""
    _original_send = httpx.AsyncClient.send

    async def _guarded_send(
        self: httpx.AsyncClient, request: httpx.Request, **kwargs: Any
    ) -> httpx.Response:
        if request.url.host not in ALLOWED_HOSTS:
            raise RuntimeError(
                f"Unmocked external HTTP call to {request.url.host}{request.url.path} "
                f"— add a mock to tests/e2e/external_mocks.py"
            )
        return await _original_send(self, request, **kwargs)

    mocker.patch.object(httpx.AsyncClient, "send", _guarded_send)


@pytest.fixture(autouse=True)
def mock_posthog(mocker: MockerFixture) -> MagicMock:
    """Silence PostHog analytics calls."""
    return mocker.patch("polar.checkout.service.posthog")


@pytest.fixture(autouse=True)
def mock_webhook_send(mocker: MockerFixture) -> MagicMock:
    """Mock webhook sending to avoid external HTTP calls."""
    return mocker.patch(
        "polar.webhook.service.WebhookService.send",
        new_callable=AsyncMock,
    )


@pytest.fixture(autouse=True)
def mock_publish_checkout_event(mocker: MockerFixture) -> AsyncMock:
    """Mock checkout event stream publishing."""
    return mocker.patch(
        "polar.checkout.eventstream.publish",
        new_callable=AsyncMock,
    )


@pytest.fixture(autouse=True)
def mock_crypto_service(mocker: MockerFixture) -> MagicMock:
    """Mock crypto daemon service for E2E tests."""
    from polar.integrations.crypto.service import CryptoService

    mock = MagicMock(spec=CryptoService)
    mock._initialized = True
    mock.supported_currencies.return_value = ["btc"]
    mock.add_payment_request = AsyncMock(return_value=("bc1qtest...", "req_test"))
    mock.get_request_status = AsyncMock(return_value={"status": "pending"})
    mock.broadcast_transaction = AsyncMock(return_value="txhash_test")
    mock.validate_address = AsyncMock(return_value=True)
    mocker.patch("polar.integrations.crypto.invoice_service.crypto_service", new=mock)
    mocker.patch("polar.integrations.crypto.payment_processor.crypto_service", new=mock)
    return mock

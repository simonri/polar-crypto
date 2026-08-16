from datetime import timedelta
from decimal import Decimal
from typing import Any

import pytest
from httpx import AsyncClient
from pytest_mock import MockerFixture

from polar.auth.models import Anonymous, AuthSubject
from polar.checkout.schemas import CheckoutConfirm
from polar.checkout.service import (
    CryptoInvoiceNotRenewable,
    PaymentError,
)
from polar.checkout.service import checkout as checkout_service
from polar.integrations.crypto.invoice_service import (
    CryptoInvoiceService,
    crypto_invoice_service,
)
from polar.kit.utils import utc_now
from polar.models import Checkout, Product
from polar.models.checkout import CheckoutStatus
from polar.models.crypto_invoice import CryptoInvoiceStatus
from polar.postgres import AsyncSession
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import create_checkout


async def _confirm(
    session: AsyncSession,
    auth_subject: AuthSubject[Anonymous],
    checkout: Checkout,
) -> Checkout:
    return await checkout_service.confirm(
        session,
        auth_subject,
        checkout,
        CheckoutConfirm.model_validate(
            {
                "customer_name": "Customer Name",
                "customer_email": "customer@example.com",
            }
        ),
    )


@pytest.mark.asyncio
class TestConfirmCrypto:
    async def test_no_payable_currency_fails_loudly(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[Anonymous],
        save_fixture: SaveFixture,
        product_one_time: Product,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(save_fixture, products=[product_one_time])

        async def _boom(*args: Any, **kwargs: Any) -> None:
            raise RuntimeError("daemon down")

        mocker.patch.object(CryptoInvoiceService, "_create_payment_method", new=_boom)

        with pytest.raises(PaymentError):
            await _confirm(session, auth_subject, checkout)

        # Checkout stays open so the customer can simply retry
        assert checkout.status == CheckoutStatus.open
        assert checkout.crypto_invoice_id is None

    async def test_status_payload(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[Anonymous],
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        checkout = await _confirm(session, auth_subject, checkout)

        payload = await checkout_service.get_crypto_invoice_status(session, checkout)

        assert payload["status"] == "pending"
        assert payload["fiat_currency"] == checkout.currency.upper()
        assert payload["monitoring_expiry"] is not None
        assert payload["received_amount"] is None
        assert payload["remaining_amount"] is None
        assert payload["customer_email"] == "customer@example.com"
        assert payload["tx_hashes"] == []
        assert len(payload["payment_methods"]) >= 1
        pm = payload["payment_methods"][0]
        assert pm["required_confirmations"] >= 1
        assert "rate" in pm
        assert pm["payment_address"] in pm["payment_url"]


@pytest.mark.asyncio
class TestRenewCryptoInvoice:
    async def test_not_confirmed(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        with pytest.raises(CryptoInvoiceNotRenewable):
            await checkout_service.renew_crypto_invoice(session, checkout)

    async def test_renew_expired_gives_fresh_invoice(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[Anonymous],
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        checkout = await _confirm(session, auth_subject, checkout)
        old_id = checkout.crypto_invoice_id
        assert old_id is not None

        old = await crypto_invoice_service.get_invoice_with_methods(session, old_id)
        assert old is not None
        old.status = CryptoInvoiceStatus.expired
        old.expiry = utc_now() - timedelta(minutes=1)
        session.add(old)
        await session.flush()

        payload = await checkout_service.renew_crypto_invoice(session, checkout)

        assert checkout.crypto_invoice_id != old_id
        assert payload["status"] == "pending"
        assert checkout.payment_processor_metadata["crypto_invoice_id"] == str(
            checkout.crypto_invoice_id
        )
        # Old invoice still watched for late payments
        await session.refresh(old)
        assert old.status == CryptoInvoiceStatus.expired
        assert old.monitoring_expiry is not None
        assert old.monitoring_expiry > utc_now()

    async def test_renew_pending_expires_old(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[Anonymous],
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        checkout = await _confirm(session, auth_subject, checkout)
        old_id = checkout.crypto_invoice_id
        assert old_id is not None

        await checkout_service.renew_crypto_invoice(session, checkout)

        old = await crypto_invoice_service.get_invoice_with_methods(session, old_id)
        assert old is not None
        assert old.status == CryptoInvoiceStatus.expired

    async def test_refuses_when_payment_detected(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[Anonymous],
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        checkout = await _confirm(session, auth_subject, checkout)
        assert checkout.crypto_invoice_id is not None
        inv = await crypto_invoice_service.get_invoice_with_methods(
            session, checkout.crypto_invoice_id
        )
        assert inv is not None
        inv.status = CryptoInvoiceStatus.paid_partial
        inv.paid_crypto_amount = Decimal("0.0001")
        inv.paid_crypto_currency = inv.payment_methods[0].currency
        session.add(inv)
        await session.flush()

        with pytest.raises(CryptoInvoiceNotRenewable):
            await checkout_service.renew_crypto_invoice(session, checkout)

    async def test_status_prefers_invoice_with_money(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[Anonymous],
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        """
        Renewed invoice is pending, but the *old* one received a late payment:
        the customer must see the payment, never "expired"/"pending".
        """
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        checkout = await _confirm(session, auth_subject, checkout)
        old_id = checkout.crypto_invoice_id
        assert old_id is not None
        await checkout_service.renew_crypto_invoice(session, checkout)

        old = await crypto_invoice_service.get_invoice_with_methods(session, old_id)
        assert old is not None
        pm = old.payment_methods[0]
        old.status = CryptoInvoiceStatus.paid_partial
        old.paid_crypto_amount = pm.amount / 2
        old.paid_crypto_currency = pm.currency
        session.add(old)
        await session.flush()

        payload = await checkout_service.get_crypto_invoice_status(session, checkout)
        assert payload["status"] == "paid_partial"
        assert payload["received_currency"] == pm.currency
        assert Decimal(payload["remaining_amount"]) == pm.amount - pm.amount / 2

    async def test_status_reports_complete_when_checkout_succeeded(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[Anonymous],
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        checkout = await _confirm(session, auth_subject, checkout)
        checkout.status = CheckoutStatus.succeeded
        session.add(checkout)
        await session.flush()

        payload = await checkout_service.get_crypto_invoice_status(session, checkout)
        assert payload["status"] == "complete"


@pytest.mark.asyncio
class TestPaymentInstructionsEmail:
    async def test_sent_on_confirm(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[Anonymous],
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        enqueue_mock = mocker.patch("polar.email.sender.enqueue_email_template")
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        await _confirm(session, auth_subject, checkout)

        enqueue_mock.assert_called_once()
        email = enqueue_mock.call_args[0][0]
        assert email.template == "crypto_payment_instructions"
        assert email.props.url == checkout.url
        assert enqueue_mock.call_args.kwargs["to_email_addr"] == (
            "customer@example.com"
        )

    async def test_confirm_survives_email_failure(
        self,
        mocker: MockerFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[Anonymous],
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        mocker.patch(
            "polar.email.sender.enqueue_email_template",
            side_effect=RuntimeError("boom"),
        )
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        confirmed = await _confirm(session, auth_subject, checkout)
        assert confirmed.status == CheckoutStatus.confirmed


@pytest.mark.asyncio
class TestRenewEndpoint:
    async def test_not_found(self, client: AsyncClient) -> None:
        response = await client.post("/v1/checkouts/client/nope/crypto-invoice/renew")
        assert response.status_code == 404

    async def test_open_checkout_conflict(
        self,
        client: AsyncClient,
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(save_fixture, products=[product_one_time])
        response = await client.post(
            f"/v1/checkouts/client/{checkout.client_secret}/crypto-invoice/renew"
        )
        assert response.status_code == 409

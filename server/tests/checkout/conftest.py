from decimal import Decimal
from typing import Any

import pytest
from pytest_mock import MockerFixture

from polar.integrations.crypto.exchange_rate import ExchangeRateService
from polar.integrations.crypto.invoice_service import (
    CryptoInvoiceService,
    build_payment_url,
)
from polar.models.crypto_invoice import CryptoInvoice
from polar.models.crypto_payment_method import CryptoPaymentMethod
from polar.postgres import AsyncSession

FAKE_RATE = Decimal("50000")


@pytest.fixture(autouse=True)
def crypto_daemon_mock(mocker: MockerFixture) -> None:
    """
    Checkout confirmation creates a crypto invoice, which normally asks the
    coin daemons for a fresh address. There are no daemons in tests, and since
    an invoice without a single payable address is now rejected, stub the
    per-currency method creation with a deterministic fake.
    """

    async def _fake_create_payment_method(
        self: CryptoInvoiceService,
        session: AsyncSession,
        *,
        invoice: CryptoInvoice,
        currency: str,
        expiry_minutes: int,
        exchange_rate_service: ExchangeRateService,
        **_: Any,
    ) -> CryptoPaymentMethod:
        amount = (invoice.price / FAKE_RATE).quantize(Decimal("0.00000001"))
        address = f"{currency}-address-{str(invoice.id)[:8]}"
        pm = CryptoPaymentMethod(
            invoice_id=invoice.id,
            currency=currency.lower(),
            amount=amount,
            rate=FAKE_RATE,
            payment_address=address,
            lookup_field=address,
            payment_url=build_payment_url(currency, address, amount),
            lightning=False,
            confirmations=0,
            is_used=False,
        )
        session.add(pm)
        return pm

    mocker.patch.object(
        CryptoInvoiceService,
        "_create_payment_method",
        new=_fake_create_payment_method,
    )

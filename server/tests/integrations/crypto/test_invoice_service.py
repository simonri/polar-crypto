from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import select

from polar.integrations.crypto.invoice_service import (
    _MAX_ADDRESS_ATTEMPTS,
    CryptoInvoiceService,
    NoPaymentMethodAvailableError,
)
from polar.kit.utils import utc_now
from polar.models import Checkout, Product
from polar.models.checkout import CheckoutStatus
from polar.models.crypto_invoice import CryptoInvoice, CryptoInvoiceStatus
from polar.models.crypto_payment_method import CryptoPaymentMethod
from polar.postgres import AsyncSession
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import create_checkout


async def _existing_payment_method(
    save_fixture: SaveFixture,
    checkout: Checkout,
    *,
    address: str,
    status: CryptoInvoiceStatus = CryptoInvoiceStatus.pending,
    expiry_delta: timedelta = timedelta(minutes=30),
    monitoring_delta: timedelta = timedelta(hours=24),
    is_used: bool = False,
) -> CryptoPaymentMethod:
    now = utc_now()
    invoice = CryptoInvoice(
        order_id=checkout.id,
        price=Decimal("10.00"),
        currency="USD",
        status=status,
        exception_status="none",
        buyer_email="customer@example.com",
        expiry=now + expiry_delta,
        monitoring_expiry=now + expiry_delta + monitoring_delta,
    )
    await save_fixture(invoice)
    pm = CryptoPaymentMethod(
        invoice_id=invoice.id,
        currency="btc",
        amount=Decimal("0.0001"),
        rate=Decimal("100000"),
        payment_address=address,
        lookup_field=f"req-{invoice.id}",
        payment_url=f"bitcoin:{address}",
        lightning=False,
        confirmations=0,
        is_used=is_used,
    )
    await save_fixture(pm)
    return pm


async def _addresses(session: AsyncSession, invoice: CryptoInvoice) -> list[str]:
    result = await session.execute(
        select(CryptoPaymentMethod.payment_address).where(
            CryptoPaymentMethod.invoice_id == invoice.id
        )
    )
    return list(result.scalars().all())


def _service(
    addresses: list[tuple[str, str]], *, per_invoice: bool = True
) -> MagicMock:
    service = MagicMock()
    service.add_payment_request = AsyncMock(side_effect=addresses)
    service.has_per_invoice_addresses = MagicMock(return_value=per_invoice)
    return service


def _rates() -> MagicMock:
    rates = MagicMock()
    rates.get_rate = AsyncMock(return_value=Decimal("100000"))
    return rates


async def _create(
    session: AsyncSession,
    checkout: Checkout,
    service: MagicMock,
    *,
    expiry_minutes: int = 60,
    monitoring_window_hours: int = 24,
) -> CryptoInvoice:
    invoice_service = CryptoInvoiceService(service)
    return await invoice_service.create_invoice(
        session,
        order_id=checkout.id,
        amount_cents=1000,
        fiat_currency="usd",
        buyer_email="customer@example.com",
        accepted_currencies=["btc"],
        expiry_minutes=expiry_minutes,
        exchange_rate_service=_rates(),
        monitoring_window_hours=monitoring_window_hours,
    )


@pytest.mark.asyncio
class TestAddressReservation:
    async def test_daemon_request_outlives_the_monitoring_window(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_one_time: Product,
    ) -> None:
        """
        Electrum recycles an address as soon as its request expires. The
        request must therefore last at least as long as Polar keeps
        attributing funds on that address to the invoice: price lock plus
        the late-payment monitoring window.
        """
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        service = _service([("bc1-fresh", "req-1")])

        await _create(
            session, checkout, service, expiry_minutes=60, monitoring_window_hours=24
        )

        kwargs = service.add_payment_request.await_args.kwargs
        assert kwargs["expiry_seconds"] == 60 * 60 + 24 * 3600


@pytest.mark.asyncio
class TestAddressUniqueness:
    async def test_address_still_watched_by_another_invoice_is_skipped(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        other = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        await _existing_payment_method(save_fixture, other, address="bc1-busy")
        service = _service([("bc1-busy", "req-1"), ("bc1-fresh", "req-2")])

        invoice = await _create(session, checkout, service)

        assert await _addresses(session, invoice) == ["bc1-fresh"]
        assert service.add_payment_request.await_count == 2

    async def test_expired_monitoring_window_frees_the_address(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        other = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        await _existing_payment_method(
            save_fixture,
            other,
            address="bc1-old",
            status=CryptoInvoiceStatus.expired,
            expiry_delta=timedelta(hours=-48),
            monitoring_delta=timedelta(hours=24),
        )
        service = _service([("bc1-old", "req-1")])

        invoice = await _create(session, checkout, service)

        assert await _addresses(session, invoice) == ["bc1-old"]
        assert service.add_payment_request.await_count == 1

    async def test_address_holding_funds_is_never_reissued(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        other = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        await _existing_payment_method(
            save_fixture,
            other,
            address="bc1-paid",
            status=CryptoInvoiceStatus.complete,
            expiry_delta=timedelta(days=-30),
            monitoring_delta=timedelta(hours=24),
            is_used=True,
        )
        service = _service([("bc1-paid", "req-1"), ("bc1-fresh", "req-2")])

        invoice = await _create(session, checkout, service)

        assert await _addresses(session, invoice) == ["bc1-fresh"]

    async def test_shared_address_adapters_skip_the_check(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_one_time: Product,
    ) -> None:
        """Solana Pay: one merchant wallet, invoices told apart by reference key."""
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        other = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        await _existing_payment_method(save_fixture, other, address="merchant")
        service = _service([("merchant", "ref-2")], per_invoice=False)

        invoice = await _create(session, checkout, service)

        assert await _addresses(session, invoice) == ["merchant"]
        assert service.add_payment_request.await_count == 1

    async def test_gives_up_instead_of_issuing_a_contaminated_address(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        other = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        await _existing_payment_method(save_fixture, other, address="bc1-busy")
        service = _service(
            [("bc1-busy", f"req-{i}") for i in range(_MAX_ADDRESS_ATTEMPTS)]
        )

        with pytest.raises(NoPaymentMethodAvailableError):
            await _create(session, checkout, service)

        assert service.add_payment_request.await_count == _MAX_ADDRESS_ATTEMPTS

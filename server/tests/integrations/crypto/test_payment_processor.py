from datetime import timedelta
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from pytest_mock import MockerFixture

from polar.integrations.crypto.payment_processor import (
    CryptoPaymentProcessor,
    _expire_stale_invoices,
    _is_watched,
)
from polar.kit.utils import utc_now
from polar.models import Checkout, Product
from polar.models.checkout import CheckoutStatus
from polar.models.crypto_invoice import CryptoInvoice, CryptoInvoiceStatus
from polar.models.crypto_payment_method import CryptoPaymentMethod
from polar.postgres import AsyncSession
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import create_checkout


async def _make_invoice(
    save_fixture: SaveFixture,
    checkout: Checkout,
    *,
    amount: Decimal = Decimal("0.001"),
    currency: str = "btc",
    status: CryptoInvoiceStatus = CryptoInvoiceStatus.pending,
    expiry_delta: timedelta = timedelta(minutes=15),
    monitoring_delta: timedelta = timedelta(hours=24),
) -> tuple[CryptoInvoice, CryptoPaymentMethod]:
    now = utc_now()
    invoice = CryptoInvoice(
        order_id=checkout.id,
        price=Decimal("50.00"),
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
        currency=currency,
        amount=amount,
        rate=Decimal("50000"),
        payment_address=f"addr-{invoice.id}",
        lookup_field=f"addr-{invoice.id}",
        payment_url=f"bitcoin:addr-{invoice.id}?amount={amount}",
        lightning=False,
        confirmations=0,
        is_used=False,
    )
    await save_fixture(pm)
    return invoice, pm


def _status(invoice: CryptoInvoice) -> CryptoInvoiceStatus:
    # Indirection so mypy doesn't narrow the Mapped attribute between asserts.
    return invoice.status


@pytest.fixture
def processor(mocker: MockerFixture) -> CryptoPaymentProcessor:
    service = MagicMock()
    service.get_address_received = AsyncMock(return_value=None)
    return CryptoPaymentProcessor(service)


@pytest.fixture
def finalize_mock(mocker: MockerFixture) -> AsyncMock:
    return mocker.patch.object(
        CryptoPaymentProcessor, "_finalize_order", new_callable=AsyncMock
    )


@pytest.mark.asyncio
class TestProcessPayment:
    async def test_full_payment_completes(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 1}
        )

        assert _status(invoice) == CryptoInvoiceStatus.complete
        assert invoice.exception_status == "none"
        assert invoice.paid_crypto_amount == Decimal("0.001")
        assert invoice.payment_detected_at is not None
        assert pm.is_used is True
        finalize_mock.assert_awaited_once()

    async def test_underpayment_is_partial_not_complete(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)

        # 5% short, fully confirmed
        await processor._process_payment(
            session, invoice, pm, {"amount": "0.00095", "confirmations": 3}
        )

        assert _status(invoice) == CryptoInvoiceStatus.paid_partial
        assert invoice.exception_status == "paid_partial"
        assert invoice.paid_crypto_amount == Decimal("0.00095")
        # keep watching the address for the top-up
        assert pm.is_used is False
        assert _is_watched(invoice) is True
        finalize_mock.assert_not_awaited()

    async def test_topup_after_partial_completes(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.0009", "confirmations": 1}
        )
        assert _status(invoice) == CryptoInvoiceStatus.paid_partial

        # Cumulative balance now covers the invoice
        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice) == CryptoInvoiceStatus.complete
        assert invoice.exception_status == "none"
        finalize_mock.assert_awaited_once()

    async def test_smaller_reading_never_regresses(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 0}
        )
        assert _status(invoice) == CryptoInvoiceStatus.unconfirmed
        # A later poll (e.g. an event without amount) must not shrink the total
        await processor._process_payment(
            session, invoice, pm, {"amount": "0.0001", "confirmations": 1}
        )
        assert invoice.paid_crypto_amount == Decimal("0.001")
        assert _status(invoice) == CryptoInvoiceStatus.complete

    async def test_unconfirmed_until_threshold(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        # LTC needs 6 confirmations
        invoice, pm = await _make_invoice(save_fixture, checkout, currency="ltc")

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 2}
        )
        assert _status(invoice) == CryptoInvoiceStatus.unconfirmed
        assert pm.confirmations == 2
        assert pm.is_used is False
        finalize_mock.assert_not_awaited()

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 6}
        )
        assert _status(invoice) == CryptoInvoiceStatus.complete
        finalize_mock.assert_awaited_once()

    async def test_overpayment_completes_with_flag(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.002", "confirmations": 1}
        )
        assert _status(invoice) == CryptoInvoiceStatus.complete
        assert invoice.exception_status == "paid_over"
        finalize_mock.assert_awaited_once()


@pytest.mark.asyncio
class TestLatePayment:
    async def test_late_payment_worth_enough_completes(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(
            save_fixture,
            checkout,
            status=CryptoInvoiceStatus.expired,
            expiry_delta=timedelta(minutes=-10),
        )
        assert _is_watched(invoice) is True

        rate_service = MagicMock()
        rate_service.get_rate = AsyncMock(return_value=Decimal("50000"))
        mocker.patch(
            "polar.integrations.crypto.exchange_rate.ExchangeRateService",
            return_value=rate_service,
        )
        mocker.patch("polar.redis.create_redis")

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice) == CryptoInvoiceStatus.complete
        assert invoice.exception_status == "paid_late"
        finalize_mock.assert_awaited_once()

    async def test_late_payment_worth_less_needs_review(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(
            save_fixture,
            checkout,
            status=CryptoInvoiceStatus.expired,
            expiry_delta=timedelta(minutes=-10),
        )

        # BTC dropped 20% since the invoice was priced
        rate_service = MagicMock()
        rate_service.get_rate = AsyncMock(return_value=Decimal("40000"))
        mocker.patch(
            "polar.integrations.crypto.exchange_rate.ExchangeRateService",
            return_value=rate_service,
        )
        mocker.patch("polar.redis.create_redis")

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice) == CryptoInvoiceStatus.needs_review
        assert invoice.exception_status == "paid_late_short"
        assert invoice.paid_crypto_amount == Decimal("0.001")
        assert pm.is_used is True
        finalize_mock.assert_not_awaited()

    async def test_expired_outside_monitoring_window_not_watched(
        self,
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, _ = await _make_invoice(
            save_fixture,
            checkout,
            status=CryptoInvoiceStatus.expired,
            expiry_delta=timedelta(days=-2),
            monitoring_delta=timedelta(hours=24),
        )
        assert _is_watched(invoice) is False

    async def test_on_payment_event_processes_expired_invoice(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(
            save_fixture,
            checkout,
            status=CryptoInvoiceStatus.expired,
            expiry_delta=timedelta(minutes=-5),
        )

        class _Maker:
            async def __aenter__(self) -> AsyncSession:
                return session

            async def __aexit__(self, *args: Any) -> None:
                return None

        mocker.patch(
            "polar.integrations.crypto.payment_processor.AsyncSessionMaker",
            return_value=_Maker(),
        )

        await processor._on_payment_event(
            "btc",
            {"lookup_field": pm.lookup_field, "amount": 0.001, "confirmations": 0},
        )
        await session.refresh(invoice)
        assert _status(invoice) == CryptoInvoiceStatus.unconfirmed
        assert invoice.exception_status == "paid_late"


@pytest.mark.asyncio
class TestPollPaymentMethod:
    async def test_partial_detected_via_address_balance(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)

        # Electrum says "Unpaid" (short payments never flip to Paid) …
        mocker.patch.object(
            processor._service,
            "get_request_status",
            AsyncMock(return_value={"status_str": "Unpaid", "confirmations": 0}),
        )
        # … but the address balance shows money arrived.
        mocker.patch.object(
            processor._service,
            "get_address_received",
            AsyncMock(return_value=Decimal("0.0005")),
        )

        processed = await processor.poll_payment_method(session, invoice, pm)
        assert processed is True
        assert _status(invoice) == CryptoInvoiceStatus.paid_partial
        assert invoice.paid_crypto_amount == Decimal("0.0005")

    async def test_paid_status_without_amount_uses_requested(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)
        mocker.patch.object(
            processor._service,
            "get_request_status",
            AsyncMock(return_value={"status_str": "Paid", "tx_hashes": ["abc"]}),
        )
        mocker.patch.object(
            processor._service, "get_address_received", AsyncMock(return_value=None)
        )

        await processor.poll_payment_method(session, invoice, pm)
        assert _status(invoice) == CryptoInvoiceStatus.complete
        assert invoice.tx_hashes == ["abc"]
        finalize_mock.assert_awaited_once()

    async def test_nothing_received_returns_false(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)
        mocker.patch.object(
            processor._service,
            "get_request_status",
            AsyncMock(return_value={"status_str": "Unpaid"}),
        )
        mocker.patch.object(
            processor._service,
            "get_address_received",
            AsyncMock(return_value=Decimal(0)),
        )
        assert await processor.poll_payment_method(session, invoice, pm) is False
        assert _status(invoice) == CryptoInvoiceStatus.pending


@pytest.mark.asyncio
class TestSSEPublish:
    async def test_invoice_update_published_to_checkout_channel(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        publish_mock = mocker.patch(
            "polar.checkout.eventstream.publish_checkout_event",
            new_callable=AsyncMock,
        )
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 0}
        )

        publish_mock.assert_awaited_once()
        args = publish_mock.await_args.args
        assert args[0] == checkout.client_secret
        assert args[2] == {"status": "unconfirmed"}

    async def test_publish_failure_never_breaks_payment(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        mocker.patch(
            "polar.checkout.eventstream.publish_checkout_event",
            side_effect=RuntimeError("no job queue context"),
        )
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)
        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice) == CryptoInvoiceStatus.complete


@pytest.mark.asyncio
class TestAddressReuse:
    """
    Reproduces the 2026-08-17 incident: the wallet reused one BTC address
    across many invoices, and one real payment auto-completed all of them.
    Every watcher of a shared address sees the same cumulative balance, so
    the processor has to arbitrate who the money belongs to.
    """

    async def _pair(
        self,
        save_fixture: SaveFixture,
        product_one_time: Product,
        *,
        first: dict[str, Any] | None = None,
        second: dict[str, Any] | None = None,
    ) -> tuple[
        tuple[CryptoInvoice, CryptoPaymentMethod],
        tuple[CryptoInvoice, CryptoPaymentMethod],
    ]:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        shared_address = "bc1qshared-address-reused-by-two-invoices"
        a = await _make_invoice(save_fixture, checkout, **(first or {}))
        b = await _make_invoice(save_fixture, checkout, **(second or {}))
        for _, pm in (a, b):
            pm.payment_address = shared_address
            await save_fixture(pm)
        return a, b

    async def test_funds_already_claimed_by_another_invoice_are_ignored(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        (invoice_a, pm_a), (invoice_b, pm_b) = await self._pair(
            save_fixture, product_one_time
        )
        await processor._process_payment(
            session, invoice_a, pm_a, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice_a) == CryptoInvoiceStatus.complete
        assert pm_a.is_used is True

        # Same balance observed through the second invoice: not its money.
        await processor._process_payment(
            session, invoice_b, pm_b, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice_b) == CryptoInvoiceStatus.pending
        assert invoice_b.exception_status == "none"
        assert invoice_b.paid_crypto_amount is None
        assert pm_b.is_used is False
        finalize_mock.assert_awaited_once()

    async def test_surplus_over_the_claimed_amount_needs_review(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        (invoice_a, pm_a), (invoice_b, pm_b) = await self._pair(
            save_fixture, product_one_time
        )
        await processor._process_payment(
            session, invoice_a, pm_a, {"amount": "0.001", "confirmations": 1}
        )
        # A second payment landed on the shared address on top of A's.
        await processor._process_payment(
            session, invoice_b, pm_b, {"amount": "0.0025", "confirmations": 1}
        )
        assert _status(invoice_b) == CryptoInvoiceStatus.needs_review
        assert invoice_b.exception_status == "address_reused"
        assert invoice_b.paid_crypto_amount == Decimal("0.0015")
        assert pm_b.is_used is True
        finalize_mock.assert_awaited_once()

    async def test_late_claimant_defers_to_the_live_invoice(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        """
        The post-guard failure of 2026-08-18: an expired invoice polled first
        and completed on funds that exactly matched a newer, still-open
        invoice on the same address. Amount match and liveness both point at
        the newer invoice, so the old one must stand aside.
        """
        (invoice_old, pm_old), (invoice_new, pm_new) = await self._pair(
            save_fixture,
            product_one_time,
            first={
                "status": CryptoInvoiceStatus.expired,
                "expiry_delta": timedelta(hours=-2),
                "amount": Decimal("0.0009"),
            },
            second={"amount": Decimal("0.001")},
        )
        await processor._process_payment(
            session, invoice_old, pm_old, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice_old) == CryptoInvoiceStatus.expired
        assert invoice_old.paid_crypto_amount is None
        assert pm_old.is_used is False
        finalize_mock.assert_not_awaited()

        await processor._process_payment(
            session, invoice_new, pm_new, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice_new) == CryptoInvoiceStatus.complete
        assert pm_new.is_used is True
        finalize_mock.assert_awaited_once()

        # And now the old invoice sees fully-claimed funds: still nothing.
        await processor._process_payment(
            session, invoice_old, pm_old, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice_old) == CryptoInvoiceStatus.expired
        finalize_mock.assert_awaited_once()

    async def test_matching_amount_beats_liveness(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        """A late payer who sends exactly their old amount is a better match
        than a fresh invoice for a different amount on the same address."""
        (invoice_old, pm_old), (invoice_new, pm_new) = await self._pair(
            save_fixture,
            product_one_time,
            first={
                "status": CryptoInvoiceStatus.expired,
                "expiry_delta": timedelta(hours=-2),
                "amount": Decimal("0.001"),
            },
            second={"amount": Decimal("0.002")},
        )
        rate_service = MagicMock()
        rate_service.get_rate = AsyncMock(return_value=Decimal("50000"))
        mocker.patch(
            "polar.integrations.crypto.exchange_rate.ExchangeRateService",
            return_value=rate_service,
        )
        mocker.patch("polar.redis.create_redis")

        # The live invoice sees the funds first but they are not its amount.
        await processor._process_payment(
            session, invoice_new, pm_new, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice_new) == CryptoInvoiceStatus.pending
        assert invoice_new.paid_crypto_amount is None

        await processor._process_payment(
            session, invoice_old, pm_old, {"amount": "0.001", "confirmations": 1}
        )
        assert _status(invoice_old) == CryptoInvoiceStatus.complete
        assert invoice_old.exception_status == "paid_late"
        finalize_mock.assert_awaited_once()

    async def test_unclaimed_shared_address_still_completes_normally(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
    ) -> None:
        """No other watcher, no other claim: business as usual."""
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)

        await processor._process_payment(
            session, invoice, pm, {"amount": "0.001", "confirmations": 1}
        )

        assert _status(invoice) == CryptoInvoiceStatus.complete
        finalize_mock.assert_awaited_once()


@pytest.mark.asyncio
class TestPollAmountFallback:
    """
    The secondary bug that let the address-reuse incident produce full
    "complete" results instead of merely ambiguous ones: a "Paid" status
    with no trustworthy amount was silently upgraded to the full requested
    amount, even when a real (smaller) amount was available.
    """

    async def test_short_reported_amount_is_trusted_not_rounded_up(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(
            save_fixture, checkout, amount=Decimal("0.001")
        )
        mocker.patch.object(
            processor._service,
            "get_request_status",
            AsyncMock(return_value={"status_str": "Paid", "amount": "0.0002"}),
        )

        await processor.poll_payment_method(session, invoice, pm)

        # A real (short) amount must be trusted as-is, landing in
        # paid_partial, not silently upgraded to "fully paid".
        assert _status(invoice) == CryptoInvoiceStatus.paid_partial
        assert invoice.paid_crypto_amount == Decimal("0.0002")
        finalize_mock.assert_not_awaited()

    async def test_no_amount_at_all_still_falls_back_to_requested(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        """Daemons that never report an amount at all (only a boolean-ish
        Paid) still get the safe-floor fallback -- this is the legitimate
        case the original fallback was written for."""
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(
            save_fixture, checkout, amount=Decimal("0.001")
        )
        mocker.patch.object(
            processor._service,
            "get_request_status",
            AsyncMock(return_value={"status_str": "Paid"}),
        )
        mocker.patch.object(
            processor._service, "get_address_received", AsyncMock(return_value=None)
        )

        await processor.poll_payment_method(session, invoice, pm)

        assert _status(invoice) == CryptoInvoiceStatus.complete
        finalize_mock.assert_awaited_once()


@pytest.mark.asyncio
class TestLightning:
    async def test_lightning_method_polled_and_completes(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        finalize_mock: AsyncMock,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)
        pm.lightning = True
        pm.payment_address = "lnbc1..."
        pm.lookup_field = "rhash123"
        await save_fixture(pm)

        mocker.patch.object(
            processor._service,
            "get_lightning_invoice_status",
            AsyncMock(return_value={"status": "Paid"}),
        )

        processed = await processor.poll_payment_method(session, invoice, pm)
        assert processed is True
        # Lightning settles atomically at the confirmation threshold
        assert _status(invoice) == CryptoInvoiceStatus.complete
        finalize_mock.assert_awaited_once()

    async def test_unpaid_lightning_returns_false(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
        processor: CryptoPaymentProcessor,
        mocker: MockerFixture,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        invoice, pm = await _make_invoice(save_fixture, checkout)
        pm.lightning = True
        await save_fixture(pm)
        mocker.patch.object(
            processor._service,
            "get_lightning_invoice_status",
            AsyncMock(return_value={"status": "Pending"}),
        )
        assert await processor.poll_payment_method(session, invoice, pm) is False
        assert _status(invoice) == CryptoInvoiceStatus.pending


@pytest.mark.asyncio
class TestExpireStaleInvoices:
    async def test_only_pending_past_expiry(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        product_one_time: Product,
    ) -> None:
        checkout = await create_checkout(
            save_fixture, products=[product_one_time], status=CheckoutStatus.confirmed
        )
        stale, _ = await _make_invoice(
            save_fixture, checkout, expiry_delta=timedelta(minutes=-1)
        )
        fresh, _ = await _make_invoice(save_fixture, checkout)
        partial, _ = await _make_invoice(
            save_fixture,
            checkout,
            status=CryptoInvoiceStatus.paid_partial,
            expiry_delta=timedelta(minutes=-1),
        )

        await _expire_stale_invoices(session)
        for inv in (stale, fresh, partial):
            await session.refresh(inv)
        assert stale.status == CryptoInvoiceStatus.expired
        assert fresh.status == CryptoInvoiceStatus.pending
        # money already arrived: never expire under the customer
        assert partial.status == CryptoInvoiceStatus.paid_partial

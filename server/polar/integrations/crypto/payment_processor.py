"""
CryptoPaymentProcessor: subscribes to daemon WebSocket events and drives
CryptoInvoice state transitions.

Runs inside Polar's Dramatiq worker process — started at worker boot and
long-lived for the process lifetime.  Falls back to periodic polling when
WebSocket subscriptions are unavailable.

State machine (per invoice)::

    pending ──(funds seen, confs < threshold)──▶ unconfirmed ──▶ complete
       │                                             │
       │ (funds seen, amount short)                  │ (amount short)
       ▼                                             ▼
    paid_partial ◀────────────────────────────────────┘
       │ (top-up brings total within tolerance) ──▶ unconfirmed / complete
       │
    expired ──(late funds, still inside monitoring window)──▶ same as pending,
              but the invoice is re-valued at the *current* rate: if the
              funds are worth at least the order (minus tolerance) they
              complete with exception_status "paid_late"; otherwise the
              invoice lands in needs_review for a human to accept or refund.

Every terminal-looking state where money exists (paid_partial, needs_review,
complete/paid_over) is surfaced to the customer; nothing is dropped silently.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal
from typing import Any

import structlog
from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import selectinload

from polar.integrations.crypto.service import (
    CONFIRMATION_THRESHOLDS,
    CryptoService,
    crypto_service,
)
from polar.kit.utils import utc_now
from polar.logging import Logger
from polar.models.crypto_invoice import CryptoInvoice, CryptoInvoiceStatus
from polar.models.crypto_payment_method import CryptoPaymentMethod
from polar.postgres import AsyncSession
from polar.worker import AsyncSessionMaker

log: Logger = structlog.get_logger()

# Tolerance: if the received amount is within 1% of expected, treat as exact
_PAYMENT_TOLERANCE = Decimal("0.01")

# Invoice statuses whose addresses we still watch for incoming funds.
WATCHED_STATUSES: tuple[CryptoInvoiceStatus, ...] = (
    CryptoInvoiceStatus.pending,
    CryptoInvoiceStatus.unconfirmed,
    CryptoInvoiceStatus.paid_partial,
    CryptoInvoiceStatus.expired,
)


def _watched_invoice_filter() -> Any:
    """
    SQL predicate: invoices whose payment methods should be (re)checked.

    - pending: until the price lock expires (after that the cron flips them
      to expired and they fall into the next bucket)
    - unconfirmed: always, the customer already paid
    - paid_partial / expired: while inside the monitoring window
    """
    now = utc_now()
    return or_(
        and_(
            CryptoInvoice.status == CryptoInvoiceStatus.pending,
            CryptoInvoice.expiry > now,
        ),
        CryptoInvoice.status == CryptoInvoiceStatus.unconfirmed,
        and_(
            CryptoInvoice.status.in_(
                [CryptoInvoiceStatus.paid_partial, CryptoInvoiceStatus.expired]
            ),
            or_(
                CryptoInvoice.monitoring_expiry.is_(None),
                CryptoInvoice.monitoring_expiry > now,
            ),
        ),
    )


class CryptoPaymentProcessor:
    def __init__(self, service: CryptoService) -> None:
        self._service = service
        self._polling_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """
        Subscribe to all configured daemon WebSockets.
        Also starts the expiry-checker background loop.
        """
        for currency in self._service.supported_currencies():
            self._service.subscribe_to_payments(
                currency,
                self._on_payment_event,
            )
            log.info("crypto.processor.subscribed", currency=currency)

        # Periodic fallback poll + expiry check
        self._polling_task = asyncio.create_task(self._polling_loop())

    async def _on_payment_event(
        self,
        currency: str,
        event: dict,  # type: ignore[type-arg]
    ) -> None:
        """
        Called by daemon WebSocket on 'new_payment'.
        event keys: lookup_field, amount, confirmations, tx_hashes
        """
        lookup_field = (
            event.get("lookup_field") or event.get("address") or event.get("ID")
        )
        if not lookup_field:
            return

        async with AsyncSessionMaker() as session:
            pm = await _get_payment_method_by_lookup(
                session, currency, str(lookup_field)
            )
            if pm is None:
                return
            invoice = await _get_invoice_with_methods(session, pm.invoice_id)
            if invoice is None or not _is_watched(invoice):
                return
            await self._process_payment(session, invoice, pm, event)

    async def _process_payment(
        self,
        session: AsyncSession,
        invoice: CryptoInvoice,
        pm: CryptoPaymentMethod,
        event: dict,  # type: ignore[type-arg]
    ) -> None:
        """
        Apply a payment observation to the invoice.

        `event["amount"]` is the *cumulative* amount received on the payment
        method's address (daemons report totals, and top-ups accumulate).
        """
        received = Decimal(str(event.get("amount", 0)))
        if received <= 0:
            return
        expected = pm.amount
        confirmations = int(event.get("confirmations", 0))
        threshold = CONFIRMATION_THRESHOLDS.get(pm.currency, 1)
        now = utc_now()

        # Never let a smaller reading regress a larger one (daemon restarts,
        # different code paths reporting different granularity).
        if invoice.paid_crypto_currency == pm.currency and invoice.paid_crypto_amount:
            received = max(received, invoice.paid_crypto_amount)

        first_detection = invoice.payment_detected_at is None
        if first_detection:
            invoice.payment_detected_at = now
        late = (
            invoice.payment_detected_at is not None
            and invoice.payment_detected_at > invoice.expiry
        )

        underpaid = received < expected * (1 - _PAYMENT_TOLERANCE)
        overpaid = received > expected * (1 + _PAYMENT_TOLERANCE)

        exception_status = "none"
        if underpaid:
            new_status = CryptoInvoiceStatus.paid_partial
            exception_status = "paid_partial"
        elif confirmations < threshold:
            new_status = CryptoInvoiceStatus.unconfirmed
            exception_status = "paid_late" if late else "none"
        else:
            new_status = CryptoInvoiceStatus.complete
            if late:
                new_status, exception_status = await self._value_late_payment(
                    invoice, pm, received
                )
            if new_status == CryptoInvoiceStatus.complete and overpaid:
                exception_status = "paid_over"

        # Update payment method
        pm.confirmations = confirmations
        # Only mark the address as used once the invoice is fully settled so
        # that the polling loop keeps rechecking it until then.
        pm.is_used = new_status in (
            CryptoInvoiceStatus.complete,
            CryptoInvoiceStatus.needs_review,
        )
        tx_hashes = event.get("tx_hashes", []) or []

        # Update invoice
        invoice.status = new_status
        invoice.exception_status = exception_status
        invoice.paid_crypto_amount = received
        invoice.paid_crypto_currency = pm.currency
        if tx_hashes:
            merged = list(dict.fromkeys([*(invoice.tx_hashes or []), *tx_hashes]))
            invoice.tx_hashes = merged
        if new_status == CryptoInvoiceStatus.complete:
            invoice.paid_at = now

        session.add(pm)
        session.add(invoice)
        await session.flush()
        log.info(
            "crypto.invoice.updated",
            invoice_id=str(invoice.id),
            status=new_status,
            exception_status=exception_status,
            confirmations=confirmations,
            received=str(received),
            expected=str(expected),
            late=late,
        )

        await _publish_invoice_event(session, invoice)

        if new_status == CryptoInvoiceStatus.complete:
            await self._finalize_order(session, invoice, pm)
        elif new_status == CryptoInvoiceStatus.needs_review:
            log.warning(
                "crypto.invoice.needs_review",
                invoice_id=str(invoice.id),
                checkout_id=str(invoice.order_id),
                exception_status=exception_status,
                received=str(received),
                currency=pm.currency,
            )

    async def _value_late_payment(
        self,
        invoice: CryptoInvoice,
        pm: CryptoPaymentMethod,
        received: Decimal,
    ) -> tuple[CryptoInvoiceStatus, str]:
        """
        A payment that arrived after the price lock is re-valued at today's
        rate. Worth at least the order (minus tolerance) → accept as paid_late.
        Otherwise → needs_review so a human can accept it or refund.
        """
        try:
            from polar.integrations.crypto.exchange_rate import ExchangeRateService
            from polar.redis import create_redis

            rate_service = ExchangeRateService(create_redis("app"))
            rate = await rate_service.get_rate(pm.currency, invoice.currency.lower())
        except Exception as e:  # pragma: no cover - network failure path
            log.warning(
                "crypto.invoice.late_rate_unavailable",
                invoice_id=str(invoice.id),
                error=str(e),
            )
            return CryptoInvoiceStatus.needs_review, "paid_late_unpriced"

        fiat_value = received * rate
        if fiat_value >= invoice.price * (1 - _PAYMENT_TOLERANCE):
            return CryptoInvoiceStatus.complete, "paid_late"
        log.warning(
            "crypto.invoice.late_payment_short",
            invoice_id=str(invoice.id),
            fiat_value=str(fiat_value),
            price=str(invoice.price),
        )
        return CryptoInvoiceStatus.needs_review, "paid_late_short"

    async def _finalize_order(
        self,
        session: AsyncSession,
        invoice: CryptoInvoice,
        pm: CryptoPaymentMethod,
    ) -> None:
        """Trigger Polar order confirmation flow."""
        from polar.order.service import order as order_service

        await order_service.confirm_order_from_crypto(session, invoice, pm)

    async def poll_payment_method(
        self,
        session: AsyncSession,
        invoice: CryptoInvoice,
        pm: CryptoPaymentMethod,
    ) -> bool:
        """
        Ask the daemon about one payment method and apply the result.
        Returns True if a payment observation was processed.
        """
        if pm.lightning:
            return await self._poll_lightning(session, invoice, pm)

        status = await self._service.get_request_status(pm.currency, pm.lookup_field)
        status_str = status.get("status_str") or status.get("status")
        tx_hashes = status.get("tx_hashes", []) or []

        if status_str in ("Paid", "Confirmed", "complete"):
            # Daemons that don't report the received amount (Electrum) only
            # flag "Paid" once at least the requested amount arrived, so the
            # requested amount is a safe floor. Prefer the real balance when
            # the daemon can tell us (catches overpayments).
            amount = status.get("amount")
            if amount is None:
                amount = await self._service.get_address_received(
                    pm.currency, pm.payment_address
                )
            if amount is None or Decimal(str(amount)) < pm.amount:
                amount = float(pm.amount)
            await self._process_payment(
                session,
                invoice,
                pm,
                {
                    "amount": amount,
                    "confirmations": status.get("confirmations", 1),
                    "tx_hashes": tx_hashes,
                },
            )
            return True

        confirmations = int(status.get("confirmations", 0) or 0)

        # Not "Paid" — but maybe *something* arrived (partial payment). Electrum
        # only reports Paid for >= requested, so ask for the address balance.
        received = await self._service.get_address_received(
            pm.currency, pm.payment_address
        )
        if received is not None and received > 0:
            await self._process_payment(
                session,
                invoice,
                pm,
                {
                    "amount": received,
                    "confirmations": confirmations,
                    "tx_hashes": tx_hashes,
                },
            )
            return True
        return False

    async def _poll_lightning(
        self,
        session: AsyncSession,
        invoice: CryptoInvoice,
        pm: CryptoPaymentMethod,
    ) -> bool:
        """
        Lightning invoices settle atomically: either the full amount arrived
        or nothing did. No partials, no confirmations to wait for.
        """
        status = await self._service.get_lightning_invoice_status(
            pm.currency, pm.lookup_field
        )
        status_str = str(status.get("status_str") or status.get("status") or "").lower()
        if status_str in ("paid", "settled", "complete"):
            threshold = CONFIRMATION_THRESHOLDS.get(pm.currency, 1)
            await self._process_payment(
                session,
                invoice,
                pm,
                {
                    "amount": float(pm.amount),
                    "confirmations": threshold,
                    "tx_hashes": status.get("tx_hashes", []),
                },
            )
            return True
        return False

    async def _polling_loop(self) -> None:
        """
        Fallback: poll pending invoices every 30 seconds.
        Handles cases where daemon WebSocket subscriptions aren't available.
        """
        while True:
            await asyncio.sleep(30)
            try:
                await self._poll_pending_invoices()
            except Exception as e:
                log.warning("crypto.processor.poll_error", error=str(e))

    async def _poll_pending_invoices(self) -> None:
        async with AsyncSessionMaker() as session:
            await _expire_stale_invoices(session)
            for currency in self._service.supported_currencies():
                await self._poll_currency(session, currency)

    async def _poll_currency(self, session: AsyncSession, currency: str) -> None:
        stmt = (
            select(CryptoPaymentMethod)
            .join(CryptoInvoice)
            .where(
                CryptoPaymentMethod.currency == currency,
                CryptoPaymentMethod.is_used.is_(False),
                _watched_invoice_filter(),
            )
            .options(
                selectinload(CryptoPaymentMethod.invoice).selectinload(
                    CryptoInvoice.payment_methods
                )
            )
        )
        result = await session.execute(stmt)
        pms = result.scalars().all()

        for pm in pms:
            try:
                await self.poll_payment_method(session, pm.invoice, pm)
            except Exception as e:
                log.warning(
                    "crypto.processor.poll_payment_error",
                    currency=currency,
                    lookup=pm.lookup_field,
                    error=str(e),
                )


# ─── Helpers ─────────────────────────────────────────────────────────────────


async def _publish_invoice_event(session: AsyncSession, invoice: CryptoInvoice) -> None:
    """
    Push the new invoice status to the customer's browser over SSE so
    "payment detected" appears the moment the transaction is seen, not on
    the next poll. Best-effort: worker paths without a job-queue context
    (e.g. the daemon WebSocket callback) just skip it.
    """
    from polar.checkout.eventstream import CheckoutEvent, publish_checkout_event
    from polar.models import Checkout

    try:
        checkout = await session.get(Checkout, invoice.order_id)
        if checkout is None:
            return
        await publish_checkout_event(
            checkout.client_secret,
            CheckoutEvent.crypto_invoice_updated,
            {"status": str(invoice.status)},
        )
    except Exception as e:
        log.debug("crypto.invoice.publish_skipped", error=str(e))


def _is_watched(invoice: CryptoInvoice) -> bool:
    if invoice.status not in WATCHED_STATUSES:
        return False
    if invoice.status in (
        CryptoInvoiceStatus.expired,
        CryptoInvoiceStatus.paid_partial,
    ):
        return (
            invoice.monitoring_expiry is None or invoice.monitoring_expiry > utc_now()
        )
    return True


async def _get_payment_method_by_lookup(
    session: AsyncSession,
    currency: str,
    lookup_field: str,
) -> CryptoPaymentMethod | None:
    stmt = select(CryptoPaymentMethod).where(
        CryptoPaymentMethod.currency == currency.lower(),
        CryptoPaymentMethod.lookup_field == lookup_field,
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _get_invoice_with_methods(
    session: AsyncSession,
    invoice_id: object,
) -> CryptoInvoice | None:
    stmt = (
        select(CryptoInvoice)
        .where(CryptoInvoice.id == invoice_id)
        .options(selectinload(CryptoInvoice.payment_methods))
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def _expire_stale_invoices(session: AsyncSession) -> None:
    """Move past-expiry pending invoices to 'expired' status."""
    stmt = (
        update(CryptoInvoice)
        .where(
            CryptoInvoice.expiry < utc_now(),
            CryptoInvoice.status == CryptoInvoiceStatus.pending,
        )
        .values(status=CryptoInvoiceStatus.expired)
    )
    await session.execute(stmt)
    await session.flush()


# Singleton
crypto_payment_processor = CryptoPaymentProcessor(crypto_service)

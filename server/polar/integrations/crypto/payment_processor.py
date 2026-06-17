"""
CryptoPaymentProcessor: subscribes to daemon WebSocket events and drives
CryptoInvoice state transitions.

Runs inside Polar's Dramatiq worker process — started at worker boot and
long-lived for the process lifetime.  Falls back to periodic polling when
WebSocket subscriptions are unavailable.
"""

from __future__ import annotations

import asyncio
from decimal import Decimal

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
            if invoice is None or invoice.status not in (
                CryptoInvoiceStatus.pending,
                CryptoInvoiceStatus.unconfirmed,
            ):
                return
            await self._process_payment(session, invoice, pm, event)

    async def _process_payment(
        self,
        session: AsyncSession,
        invoice: CryptoInvoice,
        pm: CryptoPaymentMethod,
        event: dict,  # type: ignore[type-arg]
    ) -> None:
        received = Decimal(str(event.get("amount", 0)))
        expected = pm.amount
        confirmations = int(event.get("confirmations", 0))
        threshold = CONFIRMATION_THRESHOLDS.get(pm.currency, 1)

        deviation = abs(received - expected) / expected if expected else Decimal(0)
        overpaid = received > expected * (1 + _PAYMENT_TOLERANCE)
        underpaid = received < expected * (1 - _PAYMENT_TOLERANCE)

        if confirmations < threshold:
            new_status = CryptoInvoiceStatus.unconfirmed
            exception_status = "none"
        elif underpaid:
            new_status = CryptoInvoiceStatus.complete
            exception_status = "paid_partial"
        elif overpaid:
            new_status = CryptoInvoiceStatus.complete
            exception_status = "paid_over"
        else:
            new_status = CryptoInvoiceStatus.complete
            exception_status = "none"

        # Update payment method
        pm.confirmations = confirmations
        # Only mark the address as used once the invoice is fully confirmed so
        # that the polling loop keeps rechecking it until the threshold is met.
        pm.is_used = new_status == CryptoInvoiceStatus.complete
        tx_hashes = event.get("tx_hashes", [])

        # Update invoice
        invoice.status = new_status
        invoice.exception_status = exception_status
        invoice.tx_hashes = tx_hashes
        if new_status == CryptoInvoiceStatus.complete:
            invoice.paid_at = utc_now()
            invoice.paid_crypto_amount = received
            invoice.paid_crypto_currency = pm.currency

        await session.flush()
        log.info(
            "crypto.invoice.updated",
            invoice_id=str(invoice.id),
            status=new_status,
            exception_status=exception_status,
            confirmations=confirmations,
        )

        if new_status == CryptoInvoiceStatus.complete:
            await self._finalize_order(session, invoice, pm)

    async def _finalize_order(
        self,
        session: AsyncSession,
        invoice: CryptoInvoice,
        pm: CryptoPaymentMethod,
    ) -> None:
        """Trigger Polar order confirmation flow."""
        from polar.order.service import order as order_service

        await order_service.confirm_order_from_crypto(session, invoice, pm)

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
                # For pending invoices stop polling once they expire (no payment
                # seen yet).  For unconfirmed invoices the customer already sent
                # money so we must keep polling until confirmations are reached,
                # regardless of the original invoice expiry.
                or_(
                    and_(
                        CryptoInvoice.status == CryptoInvoiceStatus.pending,
                        CryptoInvoice.expiry > utc_now(),
                    ),
                    CryptoInvoice.status == CryptoInvoiceStatus.unconfirmed,
                ),
            )
            .options(selectinload(CryptoPaymentMethod.invoice))
        )
        result = await session.execute(stmt)
        pms = result.scalars().all()

        for pm in pms:
            try:
                status = await self._service.get_request_status(
                    pm.currency, pm.lookup_field
                )
                status_str = status.get("status_str") or status.get("status")
                if status_str in ("Paid", "Confirmed", "complete"):
                    await self._process_payment(
                        session,
                        pm.invoice,
                        pm,
                        {
                            "amount": status.get("amount", float(pm.amount)),
                            "confirmations": status.get("confirmations", 1),
                            "tx_hashes": status.get("tx_hashes", []),
                        },
                    )
            except Exception as e:
                log.warning(
                    "crypto.processor.poll_payment_error",
                    currency=currency,
                    lookup=pm.lookup_field,
                    error=str(e),
                )


# ─── Helpers ─────────────────────────────────────────────────────────────────


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

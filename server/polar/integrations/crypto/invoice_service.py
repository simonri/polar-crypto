"""
CryptoInvoiceService: creates CryptoInvoice + CryptoPaymentMethod records.

Ported from bitcart/api/services/crud/invoices.py, adapted for Polar's
SQLAlchemy conventions (session managed by caller, no commit inside service).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from polar.integrations.crypto.exchange_rate import ExchangeRateService
from polar.integrations.crypto.service import CryptoService, crypto_service
from polar.kit.utils import utc_now
from polar.logging import Logger
from polar.models.crypto_invoice import CryptoInvoice, CryptoInvoiceStatus
from polar.models.crypto_payment_method import CryptoPaymentMethod
from polar.postgres import AsyncSession

log: Logger = structlog.get_logger()


class InvoiceCreationError(Exception):
    pass


class CryptoInvoiceService:
    def __init__(self, service: CryptoService) -> None:
        self._service = service

    async def create_invoice(
        self,
        session: AsyncSession,
        *,
        order_id: UUID,
        amount_cents: int,
        fiat_currency: str,
        buyer_email: str | None,
        accepted_currencies: list[str],
        expiry_minutes: int = 15,
        exchange_rate_service: ExchangeRateService,
    ) -> CryptoInvoice:
        """
        Create a CryptoInvoice and generate one CryptoPaymentMethod per
        accepted_currencies entry by calling the appropriate daemon.
        """
        if not accepted_currencies:
            raise InvoiceCreationError("At least one currency must be accepted")

        expiry = utc_now() + timedelta(minutes=expiry_minutes)
        invoice = CryptoInvoice(
            order_id=order_id,
            price=Decimal(amount_cents) / Decimal(100),
            currency=fiat_currency.upper(),
            status=CryptoInvoiceStatus.pending,
            exception_status="none",
            buyer_email=buyer_email,
            expiry=expiry,
        )
        session.add(invoice)
        await session.flush()  # get invoice.id

        for crypto in accepted_currencies:
            try:
                await self._create_payment_method(
                    session,
                    invoice=invoice,
                    currency=crypto,
                    expiry_minutes=expiry_minutes,
                    exchange_rate_service=exchange_rate_service,
                )
            except Exception as e:
                log.warning(
                    "crypto.invoice.payment_method_failed",
                    invoice_id=str(invoice.id),
                    currency=crypto,
                    error=str(e),
                )
                # Continue with other currencies even if one fails

        return invoice

    async def _create_payment_method(
        self,
        session: AsyncSession,
        *,
        invoice: CryptoInvoice,
        currency: str,
        expiry_minutes: int,
        exchange_rate_service: ExchangeRateService,
    ) -> CryptoPaymentMethod:
        # 1. Fetch exchange rate
        rate = await exchange_rate_service.get_rate(currency, invoice.currency.lower())
        amount_crypto = (invoice.price / rate).quantize(Decimal("0.00000001"))

        # 2. Generate payment address via daemon
        payment_address, lookup_field = await self._service.add_payment_request(
            currency=currency,
            amount_crypto=amount_crypto,
            description=f"Polar checkout {invoice.order_id}",
            expiry_seconds=expiry_minutes * 60,
        )

        # 3. Build payment URL (BIP21 / EIP681)
        payment_url = _build_payment_url(currency, payment_address, amount_crypto)

        pm = CryptoPaymentMethod(
            invoice_id=invoice.id,
            currency=currency.lower(),
            amount=amount_crypto,
            rate=rate,
            payment_address=payment_address,
            lookup_field=lookup_field,
            payment_url=payment_url,
            lightning=False,
            confirmations=0,
            is_used=False,
        )
        session.add(pm)
        log.info(
            "crypto.payment_method.created",
            invoice_id=str(invoice.id),
            currency=currency,
            address=payment_address,
            amount=str(amount_crypto),
        )
        return pm

    async def get_invoice_with_methods(
        self,
        session: AsyncSession,
        invoice_id: UUID,
    ) -> CryptoInvoice | None:
        stmt = (
            select(CryptoInvoice)
            .where(CryptoInvoice.id == invoice_id)
            .options(selectinload(CryptoInvoice.payment_methods))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_by_order_id(
        self,
        session: AsyncSession,
        order_id: UUID,
    ) -> CryptoInvoice | None:
        stmt = (
            select(CryptoInvoice)
            .where(CryptoInvoice.order_id == order_id)
            .options(selectinload(CryptoInvoice.payment_methods))
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


def _build_payment_url(currency: str, address: str, amount: Decimal) -> str:
    cur = currency.lower()
    if cur == "btc":
        return f"bitcoin:{address}?amount={amount}"
    if cur == "ltc":
        return f"litecoin:{address}?amount={amount}"
    if cur in ("eth", "matic", "bnb"):
        return f"ethereum:{address}?value={int(amount * Decimal('1e18'))}"
    if cur == "trx":
        return f"tron:{address}?amount={amount}"
    return f"{cur}:{address}?amount={amount}"


# Module-level singleton; caller injects exchange_rate_service per-request
crypto_invoice_service = CryptoInvoiceService(crypto_service)

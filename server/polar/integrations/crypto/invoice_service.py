"""
CryptoInvoiceService: creates CryptoInvoice + CryptoPaymentMethod records.

Ported from bitcart/api/services/crud/invoices.py, adapted for Polar's
SQLAlchemy conventions (session managed by caller, no commit inside service).
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import structlog
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from polar.config import settings
from polar.integrations.crypto.exchange_rate import ExchangeRateService, get_precision
from polar.integrations.crypto.payment_processor import _watched_invoice_filter
from polar.integrations.crypto.service import CryptoService, crypto_service
from polar.kit.utils import utc_now
from polar.logging import Logger
from polar.models.crypto_invoice import (
    PAYMENT_DETECTED_STATUSES,
    CryptoInvoice,
    CryptoInvoiceStatus,
)
from polar.models.crypto_payment_method import CryptoPaymentMethod
from polar.postgres import AsyncSession

log: Logger = structlog.get_logger()

# How many daemon requests we are willing to burn to find an address no other
# live invoice is attached to. Electrum's unused pool is its gap limit (20 by
# default) and the SDK asks it to derive a brand-new address once that pool
# is empty, so a bounded loop always terminates on a fresh address.
_MAX_ADDRESS_ATTEMPTS = 25


class InvoiceCreationError(Exception):
    pass


class AddressUniquenessError(InvoiceCreationError):
    """The daemon kept returning addresses already attached to live invoices."""

    def __init__(self, currency: str, attempts: int) -> None:
        self.currency = currency
        self.attempts = attempts
        super().__init__(
            f"Could not obtain an unused {currency.upper()} address after "
            f"{attempts} attempts"
        )


class NoPaymentMethodAvailableError(InvoiceCreationError):
    """Every configured currency failed to produce a payment address."""

    def __init__(self, currencies: Sequence[str]) -> None:
        self.currencies = currencies
        super().__init__(
            "Could not generate a payment address for any of: "
            + ", ".join(c.upper() for c in currencies)
        )


class InvoiceNotRenewableError(Exception):
    """The invoice already has funds attached; a fresh amount makes no sense."""

    def __init__(self, invoice: CryptoInvoice) -> None:
        self.invoice = invoice
        super().__init__(
            f"Invoice {invoice.id} cannot be renewed in status {invoice.status}"
        )


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
        accepted_currencies: Sequence[str],
        expiry_minutes: int = 60,
        exchange_rate_service: ExchangeRateService,
        monitoring_window_hours: int | None = None,
    ) -> CryptoInvoice:
        """
        Create a CryptoInvoice and generate one CryptoPaymentMethod per
        accepted_currencies entry by calling the appropriate daemon.

        A currency whose daemon fails is skipped (logged) so the customer can
        still pay with the others. If *no* currency succeeds we raise
        NoPaymentMethodAvailableError instead of returning a hollow invoice
        the customer could never pay.
        """
        if not accepted_currencies:
            raise InvoiceCreationError("At least one currency must be accepted")

        now = utc_now()
        expiry = now + timedelta(minutes=expiry_minutes)
        window_hours = (
            monitoring_window_hours
            if monitoring_window_hours is not None
            else settings.CRYPTO_MONITORING_WINDOW_HOURS
        )
        invoice = CryptoInvoice(
            order_id=order_id,
            price=Decimal(amount_cents) / Decimal(100),
            currency=fiat_currency.upper(),
            status=CryptoInvoiceStatus.pending,
            exception_status="none",
            buyer_email=buyer_email,
            expiry=expiry,
            monitoring_expiry=expiry + timedelta(hours=window_hours),
        )
        session.add(invoice)
        await session.flush()  # get invoice.id

        # The daemon must keep the address reserved for as long as Polar can
        # still attribute funds on it to this invoice, i.e. the price lock
        # plus the late-payment monitoring window. Electrum frees an address
        # the moment its request expires and hands it to the next caller;
        # a 60-minute request against a 24-hour watch window is exactly how
        # one on-chain payment fanned out to dozens of unrelated checkouts.
        reservation_seconds = expiry_minutes * 60 + window_hours * 3600

        created = 0
        for crypto in accepted_currencies:
            try:
                await self._create_payment_method(
                    session,
                    invoice=invoice,
                    currency=crypto,
                    expiry_minutes=expiry_minutes,
                    reservation_seconds=reservation_seconds,
                    exchange_rate_service=exchange_rate_service,
                )
                created += 1
            except Exception as e:
                log.warning(
                    "crypto.invoice.payment_method_failed",
                    invoice_id=str(invoice.id),
                    currency=crypto,
                    error=str(e),
                )
                # Continue with other currencies even if one fails

        if created == 0:
            log.error(
                "crypto.invoice.no_payment_method",
                invoice_id=str(invoice.id),
                currencies=accepted_currencies,
            )
            raise NoPaymentMethodAvailableError(list(accepted_currencies))

        return invoice

    async def renew_invoice(
        self,
        session: AsyncSession,
        invoice: CryptoInvoice,
        *,
        accepted_currencies: Sequence[str],
        expiry_minutes: int,
        exchange_rate_service: ExchangeRateService,
    ) -> CryptoInvoice:
        """
        Replace an unpaid (pending or expired) invoice with a fresh one at the
        current exchange rate. The old invoice is marked expired but keeps its
        monitoring window, so a payment that lands late on the *old* addresses
        is still matched to the same checkout.
        """
        if invoice.status in PAYMENT_DETECTED_STATUSES:
            raise InvoiceNotRenewableError(invoice)

        if invoice.status == CryptoInvoiceStatus.pending:
            invoice.status = CryptoInvoiceStatus.expired
            session.add(invoice)

        # Preserve fiat price and buyer; only the crypto amounts change.
        return await self.create_invoice(
            session,
            order_id=invoice.order_id,
            amount_cents=int((invoice.price * 100).to_integral_value()),
            fiat_currency=invoice.currency,
            buyer_email=invoice.buyer_email,
            accepted_currencies=accepted_currencies,
            expiry_minutes=expiry_minutes,
            exchange_rate_service=exchange_rate_service,
        )

    async def _create_payment_method(
        self,
        session: AsyncSession,
        *,
        invoice: CryptoInvoice,
        currency: str,
        expiry_minutes: int,
        reservation_seconds: int,
        exchange_rate_service: ExchangeRateService,
    ) -> CryptoPaymentMethod:
        # 1. Fetch exchange rate
        rate = await exchange_rate_service.get_rate(currency, invoice.currency.lower())
        amount_crypto = (invoice.price / rate).quantize(get_precision(currency))

        # 2. Generate payment address via daemon / adapter
        payment_address, lookup_field = await self._reserve_unique_address(
            session,
            invoice=invoice,
            currency=currency,
            amount_crypto=amount_crypto,
            reservation_seconds=reservation_seconds,
        )

        # 3. Build payment URL (BIP21 / EIP681 / Solana Pay)
        payment_url = build_payment_url(
            currency, payment_address, amount_crypto, lookup_field
        )

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

        # Optionally issue a Lightning invoice alongside the on-chain BTC
        # address: seconds instead of 10-60 minutes for typical order sizes.
        # A daemon without lightning support degrades to on-chain only.
        if currency.lower() == "btc" and settings.CRYPTO_BTC_LIGHTNING:
            try:
                bolt11, rhash = await self._service.add_lightning_invoice(
                    currency,
                    amount_crypto,
                    f"Polar checkout {invoice.order_id}",
                    expiry_seconds=expiry_minutes * 60,
                )
                ln = CryptoPaymentMethod(
                    invoice_id=invoice.id,
                    currency=currency.lower(),
                    amount=amount_crypto,
                    rate=rate,
                    payment_address=bolt11,
                    lookup_field=rhash,
                    payment_url=f"lightning:{bolt11}",
                    lightning=True,
                    confirmations=0,
                    is_used=False,
                )
                session.add(ln)
                # Unified BIP21 QR: on-chain address + lightning fallback
                pm.payment_url = build_payment_url(
                    currency, payment_address, amount_crypto, lookup_field, bolt11
                )
                log.info(
                    "crypto.payment_method.lightning_created",
                    invoice_id=str(invoice.id),
                )
            except Exception as e:
                log.warning(
                    "crypto.payment_method.lightning_failed",
                    invoice_id=str(invoice.id),
                    error=str(e),
                )

        return pm

    async def _reserve_unique_address(
        self,
        session: AsyncSession,
        *,
        invoice: CryptoInvoice,
        currency: str,
        amount_crypto: Decimal,
        reservation_seconds: int,
    ) -> tuple[str, str]:
        """
        Ask the daemon for a receiving address and make sure no other live
        invoice in *our* database is attached to it.

        The long reservation already stops Electrum from recycling addresses
        we still watch, but Polar's own records are the source of truth: a
        daemon restarted on an empty wallet, or a wallet restored from the
        xpub, forgets its requests and starts handing out the lowest unfunded
        addresses again. Every request we make here stays reserved at the
        daemon, so retrying simply walks forward to the next address.
        """
        description = f"Polar checkout {invoice.order_id}"
        check_uniqueness = self._service.has_per_invoice_addresses(currency)

        for attempt in range(1, _MAX_ADDRESS_ATTEMPTS + 1):
            payment_address, lookup_field = await self._service.add_payment_request(
                currency=currency,
                amount_crypto=amount_crypto,
                description=description,
                expiry_seconds=reservation_seconds,
            )
            if not check_uniqueness:
                return payment_address, lookup_field

            conflicting = await _find_live_payment_method(
                session, currency, payment_address
            )
            if conflicting is None:
                return payment_address, lookup_field

            log.warning(
                "crypto.payment_method.address_collision_at_issuance",
                invoice_id=str(invoice.id),
                currency=currency,
                address=payment_address,
                conflicting_payment_method_id=str(conflicting),
                attempt=attempt,
            )

        raise AddressUniquenessError(currency, _MAX_ADDRESS_ATTEMPTS)

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
            .order_by(CryptoInvoice.created_at.desc())
            .limit(1)
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    async def list_by_order_id(
        self,
        session: AsyncSession,
        order_id: UUID,
    ) -> list[CryptoInvoice]:
        stmt = (
            select(CryptoInvoice)
            .where(CryptoInvoice.order_id == order_id)
            .options(selectinload(CryptoInvoice.payment_methods))
            .order_by(CryptoInvoice.created_at.desc())
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def get_relevant_invoice(
        self,
        session: AsyncSession,
        order_id: UUID,
        current_invoice_id: UUID | None,
    ) -> CryptoInvoice | None:
        """
        Pick the invoice the customer should be looking at.

        A checkout can accumulate several invoices through renewals. If money
        has been detected on *any* of them (including an old, expired one that
        was paid late), that invoice wins over the current unpaid one. The
        customer must never be shown "expired" while their funds are on-chain.
        """
        invoices = await self.list_by_order_id(session, order_id)
        if not invoices:
            return None
        for inv in invoices:
            if inv.status in PAYMENT_DETECTED_STATUSES:
                return inv
        for inv in invoices:
            if inv.id == current_invoice_id:
                return inv
        return invoices[0]


async def _find_live_payment_method(
    session: AsyncSession,
    currency: str,
    payment_address: str,
) -> UUID | None:
    """
    Id of a payment method on the same (currency, address) that could still
    claim funds arriving there: either it already holds funds (`is_used`) or
    its invoice is still inside a watched state (pending, unconfirmed, or
    expired / paid_partial within the monitoring window).
    """
    stmt = (
        select(CryptoPaymentMethod.id)
        .join(CryptoInvoice, CryptoInvoice.id == CryptoPaymentMethod.invoice_id)
        .where(
            CryptoPaymentMethod.currency == currency.lower(),
            CryptoPaymentMethod.payment_address == payment_address,
            or_(
                CryptoPaymentMethod.is_used.is_(True),
                _watched_invoice_filter(),
            ),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def format_crypto_amount(amount: Decimal) -> str:
    """
    Render amount as a plain decimal string with no trailing zeros and no
    scientific notation — required by BIP21, Litecoin URI, and Solana Pay specs.
    """
    s = f"{amount:f}"  # fixed-point, never scientific notation
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"


def build_payment_url(
    currency: str,
    address: str,
    amount: Decimal,
    lookup_field: str | None = None,
    lightning_invoice: str | None = None,
) -> str:
    cur = currency.lower()
    amt = format_crypto_amount(amount)
    if cur == "btc":
        ln = f"&lightning={lightning_invoice}" if lightning_invoice else ""
        return f"bitcoin:{address}?amount={amt}{ln}"
    if cur == "ltc":
        return f"litecoin:{address}?amount={amt}"
    if cur in ("eth", "matic", "bnb"):
        return f"ethereum:{address}?value={int(amount * Decimal('1e18'))}"
    if cur == "trx":
        return f"tron:{address}?amount={amt}"
    if cur in ("sol", "sol_usdc"):
        from polar.config import settings
        from polar.integrations.crypto.solana import USDC_MINT_DEVNET, USDC_MINT_MAINNET

        ref = f"&reference={lookup_field}" if lookup_field else ""
        if cur == "sol_usdc":
            usdc_mint = (
                USDC_MINT_DEVNET
                if settings.CRYPTO_SOL_NETWORK == "devnet"
                else USDC_MINT_MAINNET
            )
            return f"solana:{address}?amount={amt}&spl-token={usdc_mint}{ref}"
        return f"solana:{address}?amount={amt}{ref}"
    return f"{cur}:{address}?amount={amt}"


# Module-level singleton; caller injects exchange_rate_service per-request
crypto_invoice_service = CryptoInvoiceService(crypto_service)

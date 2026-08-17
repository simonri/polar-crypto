from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import UUID4
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload
from tagflow import classes, tag, text

from polar.integrations.crypto.exchange_rate import ExchangeRateService
from polar.integrations.crypto.service import CryptoServiceError, crypto_service
from polar.models import CryptoInvoice, CryptoPaymentMethod
from polar.models.crypto_invoice import CryptoInvoiceStatus
from polar.postgres import (
    AsyncSession,
    get_db_read_session,
    get_db_session,
)
from polar.redis import Redis, get_redis

from ..components import button
from ..layout import layout
from ..responses import HXRedirectResponse
from ..toast import add_toast

# Invoices where money arrived but the order did not (or may not) complete.
_EXCEPTION_STATUSES = [
    CryptoInvoiceStatus.paid_partial,
    CryptoInvoiceStatus.needs_review,
]

router = APIRouter()


@router.get("/", name="crypto_wallets:list")
async def list_crypto_wallets(
    request: Request,
    session: AsyncSession = Depends(get_db_read_session),
    redis: Redis = Depends(get_redis),
) -> None:
    currencies = crypto_service.supported_currencies()
    exchange_rate_service = ExchangeRateService(redis)

    balances: dict[str, dict[str, Decimal] | None] = {}
    rates: dict[str, Decimal | None] = {}
    for currency in currencies:
        try:
            balances[currency] = await crypto_service.get_wallet_balance(currency)
        except (CryptoServiceError, Exception):
            balances[currency] = None
        try:
            rates[currency] = await exchange_rate_service.get_rate(currency)
        except Exception:
            rates[currency] = None

    stmt = (
        select(
            CryptoPaymentMethod.currency,
            func.count(CryptoPaymentMethod.id).label("count"),
        )
        .join(CryptoInvoice, CryptoPaymentMethod.invoice_id == CryptoInvoice.id)
        .where(
            CryptoPaymentMethod.is_used.is_(False),
            CryptoInvoice.status.in_(
                [CryptoInvoiceStatus.pending, CryptoInvoiceStatus.unconfirmed]
            ),
        )
        .group_by(CryptoPaymentMethod.currency)
    )
    result = await session.execute(stmt)
    pending_counts: dict[str, int] = {row[0]: row[1] for row in result.all()}

    exceptions_stmt = (
        select(CryptoInvoice)
        .where(CryptoInvoice.status.in_(_EXCEPTION_STATUSES))
        .options(selectinload(CryptoInvoice.payment_methods))
        .order_by(CryptoInvoice.modified_at.desc().nullslast())
        .limit(100)
    )
    exceptions = list((await session.execute(exceptions_stmt)).scalars().all())

    with layout(
        request,
        [("Crypto Wallets", "/backoffice/crypto-wallets")],
        "crypto_wallets:list",
    ):
        with tag.div(classes="flex flex-col gap-6"):
            with tag.h1(classes="text-2xl font-bold"):
                text("Crypto Daemon Wallets")

            if not currencies:
                with tag.div(classes="alert alert-info"):
                    text(
                        "No crypto currencies configured. Set CRYPTO_CURRENCIES in .env."
                    )
            else:
                with tag.div(classes="overflow-x-auto"):
                    with tag.table(classes="table table-zebra"):
                        with tag.thead():
                            with tag.tr():
                                for col in [
                                    "Currency",
                                    "Status",
                                    "Confirmed Balance",
                                    "Unconfirmed",
                                    "USD Value",
                                    "Pending Addresses",
                                ]:
                                    with tag.th():
                                        text(col)
                        with tag.tbody():
                            for currency in currencies:
                                bal = balances[currency]
                                rate = rates[currency]
                                pending = pending_counts.get(currency, 0)
                                confirmed = (
                                    bal.get("confirmed", Decimal(0)) if bal else None
                                )
                                unconfirmed = (
                                    bal.get("unconfirmed", Decimal(0)) if bal else None
                                )

                                with tag.tr():
                                    with tag.td():
                                        with tag.span(classes="font-mono font-bold"):
                                            text(currency.upper())

                                    with tag.td():
                                        with tag.div(classes="badge"):
                                            if bal is not None:
                                                classes("badge-success")
                                                text("Online")
                                            else:
                                                classes("badge-error")
                                                text("Offline")

                                    with tag.td(classes="font-mono"):
                                        if confirmed is not None:
                                            text(f"{confirmed:.8f} {currency.upper()}")
                                        else:
                                            text("—")

                                    with tag.td(
                                        classes="font-mono text-base-content/50"
                                    ):
                                        if unconfirmed is not None and unconfirmed > 0:
                                            text(f"{unconfirmed:.8f}")
                                        else:
                                            text("—")

                                    with tag.td():
                                        if confirmed is not None and rate is not None:
                                            usd_value = confirmed * rate
                                            text(f"${usd_value:,.2f}")
                                        else:
                                            text("—")

                                    with tag.td():
                                        with tag.span(classes="badge badge-neutral"):
                                            text(str(pending))

            _render_exceptions(request, exceptions)


def _render_exceptions(request: Request, exceptions: list[CryptoInvoice]) -> None:
    with tag.div(classes="flex flex-col gap-3"):
        with tag.h2(classes="text-xl font-bold"):
            text("Payment exceptions")
        with tag.p(classes="text-sm text-base-content/70"):
            text(
                "Invoices where funds arrived but the order did not complete: "
                "underpaid (customer can still top up), late payments now worth "
                "less than the order, or a duplicate payment on a renewed invoice. "
                "Accepting marks the invoice paid and fulfils the checkout."
            )
        if not exceptions:
            with tag.div(classes="alert alert-success"):
                text("No exceptions: every detected payment completed.")
            return
        with tag.div(classes="overflow-x-auto"):
            with tag.table(classes="table table-zebra"):
                with tag.thead():
                    with tag.tr():
                        for col in [
                            "Checkout",
                            "Status",
                            "Reason",
                            "Order",
                            "Received",
                            "Expected",
                            "Detected",
                            "",
                        ]:
                            with tag.th():
                                text(col)
                with tag.tbody():
                    for inv in exceptions:
                        pm = next(
                            (
                                m
                                for m in inv.payment_methods
                                if m.currency == inv.paid_crypto_currency
                            ),
                            None,
                        )
                        cur = (inv.paid_crypto_currency or "").upper()
                        with tag.tr():
                            with tag.td(classes="font-mono text-xs"):
                                text(str(inv.order_id))
                            with tag.td():
                                with tag.span(classes="badge badge-warning"):
                                    text(inv.status)
                            with tag.td(classes="font-mono text-xs"):
                                text(inv.exception_status)
                            with tag.td():
                                text(f"{inv.price} {inv.currency}")
                            with tag.td(classes="font-mono"):
                                text(f"{inv.paid_crypto_amount or 0} {cur}")
                            with tag.td(classes="font-mono"):
                                text(f"{pm.amount} {cur}" if pm else "-")
                            with tag.td(classes="text-xs"):
                                text(
                                    inv.payment_detected_at.strftime("%Y-%m-%d %H:%M")
                                    if inv.payment_detected_at
                                    else "-"
                                )
                            with tag.td():
                                with button(
                                    hx_post=str(
                                        request.url_for(
                                            "crypto_wallets:accept_invoice",
                                            id=inv.id,
                                        )
                                    ),
                                    hx_confirm=(
                                        "Accept this payment as paid in full and "
                                        "fulfil the checkout?"
                                    ),
                                    variant="primary",
                                    size="sm",
                                ):
                                    text("Accept as paid")


@router.post("/invoices/{id}/accept", name="crypto_wallets:accept_invoice")
async def accept_invoice(
    request: Request,
    id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> Any:
    from polar.integrations.crypto.payment_processor import crypto_payment_processor
    from polar.kit.utils import utc_now

    stmt = (
        select(CryptoInvoice)
        .where(CryptoInvoice.id == id)
        .options(selectinload(CryptoInvoice.payment_methods))
    )
    invoice = (await session.execute(stmt)).scalar_one_or_none()
    if invoice is None:
        raise HTTPException(status_code=404)
    if invoice.status not in _EXCEPTION_STATUSES:
        await add_toast(request, "This invoice is not awaiting review.", "error")
        return HXRedirectResponse(
            request, str(request.url_for("crypto_wallets:list")), 303
        )
    pm = next(
        (
            m
            for m in invoice.payment_methods
            if m.currency == invoice.paid_crypto_currency
        ),
        None,
    )
    if pm is None:
        await add_toast(request, "No payment method recorded on invoice.", "error")
        return HXRedirectResponse(
            request, str(request.url_for("crypto_wallets:list")), 303
        )

    invoice.status = CryptoInvoiceStatus.complete
    invoice.exception_status = f"accepted_{invoice.exception_status}"
    invoice.paid_at = utc_now()
    pm.is_used = True
    session.add(invoice)
    session.add(pm)
    await session.flush()
    await crypto_payment_processor._finalize_order(session, invoice, pm)

    await add_toast(request, "Payment accepted; checkout fulfilled.", "success")
    return HXRedirectResponse(request, str(request.url_for("crypto_wallets:list")), 303)

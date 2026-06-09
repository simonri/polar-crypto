from decimal import Decimal

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from tagflow import classes, tag, text

from polar.integrations.crypto.service import CryptoServiceError, crypto_service
from polar.models import CryptoInvoice, CryptoPaymentMethod
from polar.models.crypto_invoice import CryptoInvoiceStatus
from polar.postgres import AsyncSession, get_db_read_session

from ..layout import layout

router = APIRouter()


@router.get("/", name="crypto_wallets:list")
async def list_crypto_wallets(
    request: Request,
    session: AsyncSession = Depends(get_db_read_session),
) -> None:
    currencies = crypto_service.supported_currencies()

    balances: dict[str, dict[str, Decimal] | None] = {}
    rates: dict[str, Decimal | None] = {}
    for currency in currencies:
        try:
            balances[currency] = await crypto_service.get_wallet_balance(currency)
        except (CryptoServiceError, Exception):
            balances[currency] = None
        try:
            rates[currency] = await crypto_service.get_exchange_rate(currency)
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

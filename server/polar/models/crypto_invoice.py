from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import TIMESTAMP, ForeignKey, Numeric, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from polar.kit.db.models import RecordModel

if TYPE_CHECKING:
    from .checkout import Checkout
    from .crypto_payment_method import CryptoPaymentMethod


class CryptoInvoiceStatus(StrEnum):
    pending = "pending"
    unconfirmed = "unconfirmed"  # on-chain, awaiting confirmations
    complete = "complete"
    expired = "expired"
    invalid = "invalid"


class CryptoInvoice(RecordModel):
    __tablename__ = "crypto_invoices"

    order_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("checkouts.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )

    @declared_attr
    def checkout(cls) -> Mapped[Checkout]:
        return relationship(
            "Checkout",
            lazy="raise",
            foreign_keys="[CryptoInvoice.order_id]",
        )

    price: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)
    currency: Mapped[str] = mapped_column(String(10), nullable=False)  # USD, EUR ...

    status: Mapped[CryptoInvoiceStatus] = mapped_column(
        String(20), nullable=False, default=CryptoInvoiceStatus.pending, index=True
    )
    exception_status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="none"
    )

    buyer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    expiry: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, index=True
    )

    paid_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )
    paid_crypto_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(30, 18), nullable=True, default=None
    )
    paid_crypto_currency: Mapped[str | None] = mapped_column(
        String(10), nullable=True, default=None
    )
    tx_hashes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)

    @declared_attr
    def payment_methods(cls) -> Mapped[list[CryptoPaymentMethod]]:
        return relationship(
            "CryptoPaymentMethod",
            back_populates="invoice",
            cascade="all, delete-orphan",
            lazy="raise",
        )

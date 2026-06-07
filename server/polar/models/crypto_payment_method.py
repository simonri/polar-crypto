from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, Numeric, String, Uuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from polar.kit.db.models import RecordModel

if TYPE_CHECKING:
    from .crypto_invoice import CryptoInvoice


class CryptoPaymentMethod(RecordModel):
    __tablename__ = "crypto_payment_methods"

    __table_args__ = (
        Index("ix_crypto_payment_methods_lookup", "currency", "lookup_field"),
    )

    invoice_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("crypto_invoices.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )

    @declared_attr
    def invoice(cls) -> Mapped[CryptoInvoice]:
        return relationship(
            "CryptoInvoice", back_populates="payment_methods", lazy="raise"
        )

    currency: Mapped[str] = mapped_column(String(10), nullable=False)  # "btc", "eth"
    amount: Mapped[Decimal] = mapped_column(Numeric(30, 18), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(20, 8), nullable=False)

    payment_address: Mapped[str] = mapped_column(String(500), nullable=False)
    lookup_field: Mapped[str] = mapped_column(String(500), nullable=False, index=True)
    payment_url: Mapped[str] = mapped_column(String(1000), nullable=False)

    lightning: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    confirmations: Mapped[int] = mapped_column(nullable=False, default=0)
    is_used: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, index=True
    )

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, UniqueConstraint, Uuid
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from polar.kit.db.models import RecordModel

if TYPE_CHECKING:
    from .payout_account import PayoutAccount


class CryptoPayoutWallet(RecordModel):
    __tablename__ = "crypto_payout_wallets"

    __table_args__ = (
        UniqueConstraint("account_id", "currency", name="uq_crypto_payout_wallet"),
    )

    account_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("payout_accounts.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )

    @declared_attr
    def account(cls) -> Mapped[PayoutAccount]:
        return relationship("PayoutAccount", lazy="raise")

    currency: Mapped[str] = mapped_column(String(10), nullable=False)  # "btc", "eth"
    wallet_address: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

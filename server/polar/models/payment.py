from enum import StrEnum
from typing import TYPE_CHECKING, Any
from uuid import UUID

from sqlalchemy import BigInteger, ColumnElement, ForeignKey, SmallInteger, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from polar.enums import PaymentProcessor
from polar.kit.db.models import RecordModel
from polar.kit.extensions.sqlalchemy.types import StrEnumType

if TYPE_CHECKING:
    from .checkout import Checkout
    from .order import Order
    from .organization import Organization
    from .wallet import Wallet


class PaymentTrigger(StrEnum):
    purchase = "purchase"
    subscription_cycle = "subscription_cycle"
    retry_dunning = "retry_dunning"
    retry_customer = "retry_customer"
    retry_payment_method_update = "retry_payment_method_update"
    retry_admin = "retry_admin"

    def is_renewal_payment(self) -> bool:
        """Whether this trigger drives a recurring-billing payment (cycle +
        dunning + retries of failed renewals). ``purchase`` is the only
        non-renewal trigger and is gated by ``can_accept_payments`` instead
        of ``can_renew_subscriptions``.
        """
        return self is not PaymentTrigger.purchase


# Triggers that count toward the dunning ceiling — i.e. failures from these
# attempts use up the customer's automated retry budget.
DUNNING_COUNTING_TRIGGERS: set[PaymentTrigger] = {
    PaymentTrigger.purchase,
    PaymentTrigger.subscription_cycle,
    PaymentTrigger.retry_dunning,
}

# Triggers explicitly excluded from the dunning ceiling — one-shot recovery
# attempts that shouldn't shorten the dunning window. Together with
# ``DUNNING_COUNTING_TRIGGERS`` these must cover every ``PaymentTrigger``
# value (enforced by ``test_every_payment_trigger_is_classified``).
DUNNING_NON_COUNTING_TRIGGERS: set[PaymentTrigger] = {
    PaymentTrigger.retry_customer,
    PaymentTrigger.retry_payment_method_update,
    PaymentTrigger.retry_admin,
}


class PaymentStatus(StrEnum):
    pending = "pending"
    succeeded = "succeeded"
    failed = "failed"


class Payment(RecordModel):
    __tablename__ = "payments"

    processor: Mapped[PaymentProcessor] = mapped_column(
        StrEnumType(PaymentProcessor), index=True, nullable=False
    )
    status: Mapped[PaymentStatus] = mapped_column(
        StrEnumType(PaymentStatus), index=True, nullable=False
    )
    amount: Mapped[int] = mapped_column("amount_v2", BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    method: Mapped[str] = mapped_column(String, index=True, nullable=False)
    method_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    processor_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    customer_email: Mapped[str | None] = mapped_column(
        String, nullable=True, index=True
    )

    processor_id: Mapped[str] = mapped_column(
        String, index=True, nullable=False, unique=True
    )

    decline_reason: Mapped[str | None] = mapped_column(String, nullable=True)
    decline_message: Mapped[str | None] = mapped_column(String, nullable=True)

    trigger: Mapped[PaymentTrigger | None] = mapped_column(
        StrEnumType(PaymentTrigger), nullable=True
    )

    risk_level: Mapped[str | None] = mapped_column(String, nullable=True)
    risk_score: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)

    organization_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="cascade"),
        nullable=False,
        index=True,
    )

    @declared_attr
    def organization(cls) -> Mapped["Organization"]:
        return relationship("Organization", lazy="raise")

    checkout_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("checkouts.id", ondelete="set null"),
        nullable=True,
        index=True,
    )

    @declared_attr
    def checkout(cls) -> Mapped["Checkout | None"]:
        return relationship("Checkout", lazy="raise")

    wallet_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("wallets.id", ondelete="set null"),
        nullable=True,
        index=True,
    )

    @declared_attr
    def wallet(cls) -> Mapped["Wallet | None"]:
        return relationship("Wallet", lazy="raise")

    order_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("orders.id", ondelete="set null"),
        nullable=True,
        index=True,
    )

    @declared_attr
    def order(cls) -> Mapped["Order | None"]:
        return relationship("Order", lazy="raise")

    @hybrid_property
    def is_succeeded(self) -> bool:
        return self.status == PaymentStatus.succeeded

    @is_succeeded.inplace.expression
    @classmethod
    def _is_succeeded_expression(cls) -> ColumnElement[bool]:
        return cls.status == PaymentStatus.succeeded

    @hybrid_property
    def is_failed(self) -> bool:
        return self.status == PaymentStatus.failed

    @is_failed.inplace.expression
    @classmethod
    def _is_failed_expression(cls) -> ColumnElement[bool]:
        return cls.status == PaymentStatus.failed

    @property
    def is_non_recoverable(self) -> bool:
        """All crypto payment failures are non-recoverable (no card to retry)."""
        return self.status == PaymentStatus.failed

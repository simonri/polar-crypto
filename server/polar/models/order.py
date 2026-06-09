from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    ColumnElement,
    ForeignKey,
    String,
    Uuid,
    func,
)
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from polar.custom_field.data import CustomFieldDataMixin
from polar.exceptions import PolarError
from polar.kit.address import Address, AddressType
from polar.kit.db.models import RecordModel
from polar.kit.metadata import MetadataMixin
from polar.models.order_item import OrderItem

if TYPE_CHECKING:
    from polar.models import (
        Checkout,
        CryptoInvoice,
        Customer,
        Discount,
        Organization,
        Product,
        ProductPrice,
        Subscription,
    )


class OrderBillingReasonInternal(StrEnum):
    """
    Internal billing reasons with additional granularity.
    """

    purchase = "purchase"
    subscription_create = "subscription_create"
    subscription_cycle = "subscription_cycle"
    subscription_cycle_after_trial = "subscription_cycle_after_trial"
    subscription_cancel = "subscription_cancel"
    subscription_update = "subscription_update"


class OrderBillingReason(StrEnum):
    purchase = "purchase"
    subscription_create = "subscription_create"
    subscription_cycle = "subscription_cycle"
    subscription_update = "subscription_update"


class OrderStatus(StrEnum):
    draft = "draft"
    pending = "pending"
    paid = "paid"
    refunded = "refunded"
    partially_refunded = "partially_refunded"
    void = "void"


class OrderError(PolarError): ...


class RefundAmountTooHigh(OrderError):
    def __init__(self, order: "Order") -> None:
        self.order = order
        message = (
            f"Refund amount exceeds remaining order balance: {order.refundable_amount}"
        )
        super().__init__(message)


class Order(CustomFieldDataMixin, MetadataMixin, RecordModel):
    __tablename__ = "orders"

    status: Mapped[OrderStatus] = mapped_column(
        String, nullable=False, default=OrderStatus.pending, index=True
    )
    subtotal_amount: Mapped[int] = mapped_column(
        "subtotal_amount_v2", BigInteger, nullable=False
    )
    discount_amount: Mapped[int] = mapped_column(
        "discount_amount_v2", BigInteger, nullable=False, default=0
    )
    net_amount: Mapped[int] = mapped_column("net_amount_v2", BigInteger, nullable=False)
    applied_balance_amount: Mapped[int] = mapped_column(
        "applied_balance_amount_v2", BigInteger, nullable=False, default=0
    )

    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    billing_reason: Mapped[OrderBillingReasonInternal] = mapped_column(
        String, nullable=False, index=True
    )

    refunded_amount: Mapped[int] = mapped_column(
        "refunded_amount_v2", BigInteger, nullable=False, default=0
    )

    platform_fee_amount: Mapped[int] = mapped_column(
        "platform_fee_amount_v2", BigInteger, nullable=False, default=0
    )
    platform_fee_currency: Mapped[str | None] = mapped_column(
        String(3), nullable=True, default=None
    )

    billing_name: Mapped[str | None] = mapped_column(
        String, nullable=True, default=None
    )
    billing_address: Mapped[Address | None] = mapped_column(AddressType, nullable=True)

    next_payment_attempt_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None, index=True
    )

    payment_lock_acquired_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )

    refunds_blocked_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )

    customer_id: Mapped[UUID] = mapped_column(
        Uuid, ForeignKey("customers.id"), nullable=False, index=True
    )

    @declared_attr
    def customer(cls) -> Mapped["Customer"]:
        return relationship("Customer", lazy="raise")

    organization_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="restrict"),
        nullable=False,
        index=True,
    )

    @declared_attr
    def organization(cls) -> Mapped["Organization"]:
        return relationship("Organization", lazy="raise")

    product_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("products.id"), nullable=True, index=True
    )

    @declared_attr
    def product(cls) -> Mapped["Product | None"]:
        return relationship("Product", lazy="raise")

    discount_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("discounts.id", ondelete="set null"), nullable=True, index=True
    )

    @declared_attr
    def discount(cls) -> Mapped["Discount | None"]:
        return relationship("Discount", lazy="raise")

    subscription_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("subscriptions.id"), nullable=True, index=True
    )

    @declared_attr
    def subscription(cls) -> Mapped["Subscription | None"]:
        return relationship("Subscription", lazy="raise")

    checkout_id: Mapped[UUID | None] = mapped_column(
        Uuid, ForeignKey("checkouts.id", ondelete="set null"), nullable=True, index=True
    )

    @declared_attr
    def checkout(cls) -> Mapped["Checkout | None"]:
        return relationship("Checkout", lazy="raise")

    crypto_invoice_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("crypto_invoices.id", ondelete="set null"),
        nullable=True,
        default=None,
    )

    @declared_attr
    def crypto_invoice(cls) -> Mapped["CryptoInvoice | None"]:
        return relationship(
            "CryptoInvoice", lazy="raise", foreign_keys="[Order.crypto_invoice_id]"
        )

    items: Mapped[list["OrderItem"]] = relationship(
        "OrderItem",
        back_populates="order",
        cascade="all, delete-orphan",
        # Items are almost always needed, so eager loading makes sense
        lazy="selectin",
    )

    @property
    def legacy_product_price(self) -> "ProductPrice | None":
        """
        Dummy method to keep API backward compatibility
        by fetching a product price at all costs.
        """
        if self.product is None:
            return None
        for item in self.items:
            if item.product_price:
                return item.product_price
        return None

    @property
    def legacy_product_price_id(self) -> UUID | None:
        price = self.legacy_product_price
        if price is None:
            return None
        return price.id

    @hybrid_property
    def paid(self) -> bool:
        return self.status in {
            OrderStatus.paid,
            OrderStatus.refunded,
            OrderStatus.partially_refunded,
        }

    @paid.inplace.expression
    @classmethod
    def _paid_expression(cls) -> ColumnElement[bool]:
        return cls.status.in_(
            (OrderStatus.paid, OrderStatus.refunded, OrderStatus.partially_refunded)
        )

    @hybrid_property
    def total_amount(self) -> int:
        return self.net_amount

    @total_amount.inplace.expression
    @classmethod
    def _total_amount_expression(cls) -> ColumnElement[int]:
        return func.coalesce(cls.net_amount, cls.subtotal_amount - cls.discount_amount)

    @hybrid_property
    def due_amount(self) -> int:
        return max(0, self.total_amount + self.applied_balance_amount)

    @due_amount.inplace.expression
    @classmethod
    def _due_amount_expression(cls) -> ColumnElement[int]:
        return func.greatest(0, cls.total_amount + cls.applied_balance_amount)

    @hybrid_property
    def payout_amount(self) -> int:
        return self.net_amount - self.platform_fee_amount - self.refunded_amount

    @payout_amount.inplace.expression
    @classmethod
    def _payout_amount_expression(cls) -> ColumnElement[int]:
        return (
            func.coalesce(cls.net_amount, cls.subtotal_amount - cls.discount_amount)
            - cls.platform_fee_amount
            - cls.refunded_amount
        )

    @property
    def refunded(self) -> bool:
        return self.status == OrderStatus.refunded

    @property
    def refunds_blocked(self) -> bool:
        return self.refunds_blocked_at is not None

    @property
    def refundable_amount(self) -> int:
        return max(
            0, self.net_amount + self.applied_balance_amount - self.refunded_amount
        )

    @property
    def remaining_balance(self) -> int:
        return self.refundable_amount

    def update_refunds(self, refunded_amount: int) -> None:
        new_amount = self.refunded_amount + refunded_amount

        if new_amount == 0:
            new_status = OrderStatus.paid
        elif new_amount >= (self.net_amount + self.applied_balance_amount):
            new_status = OrderStatus.refunded
        else:
            new_status = OrderStatus.partially_refunded

        self.status = new_status
        self.refunded_amount = new_amount

    @hybrid_property
    def is_void(self) -> bool:
        return self.status == OrderStatus.void

    @is_void.inplace.expression
    @classmethod
    def _is_void_expression(cls) -> ColumnElement[bool]:
        return cls.status == OrderStatus.void

    @property
    def statement_descriptor_suffix(self) -> str:
        if (
            self.billing_reason
            == OrderBillingReasonInternal.subscription_cycle_after_trial
        ):
            return self.organization.statement_descriptor(" TRIAL OVER")
        return self.organization.statement_descriptor()

    @property
    def description(self) -> str:
        if self.product is not None:
            return self.product.name
        return self.items[0].label

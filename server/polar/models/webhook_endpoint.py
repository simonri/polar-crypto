from enum import StrEnum
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, String, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from polar.kit.db.models.base import RecordModel

if TYPE_CHECKING:
    from .organization import Organization


class WebhookEventType(StrEnum):
    checkout_created = "checkout.created"
    checkout_updated = "checkout.updated"
    checkout_expired = "checkout.expired"
    customer_created = "customer.created"
    customer_updated = "customer.updated"
    customer_deleted = "customer.deleted"
    customer_state_changed = "customer.state_changed"
    member_created = "member.created"
    member_updated = "member.updated"
    member_deleted = "member.deleted"
    order_created = "order.created"
    order_updated = "order.updated"
    order_paid = "order.paid"
    order_refunded = "order.refunded"
    subscription_created = "subscription.created"
    subscription_updated = "subscription.updated"
    subscription_active = "subscription.active"
    subscription_canceled = "subscription.canceled"
    subscription_uncanceled = "subscription.uncanceled"
    subscription_revoked = "subscription.revoked"
    subscription_past_due = "subscription.past_due"
    refund_created = "refund.created"
    refund_updated = "refund.updated"
    product_created = "product.created"
    product_updated = "product.updated"
    organization_updated = "organization.updated"


CustomerWebhookEventType = Literal[
    WebhookEventType.customer_created,
    WebhookEventType.customer_updated,
    WebhookEventType.customer_deleted,
    WebhookEventType.customer_state_changed,
]


class WebhookFormat(StrEnum):
    raw = "raw"
    slack = "slack"


class WebhookEndpoint(RecordModel):
    __tablename__ = "webhook_endpoints"

    url: Mapped[str] = mapped_column(String, nullable=False)
    name: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    format: Mapped[WebhookFormat] = mapped_column(String, nullable=False)
    secret: Mapped[str] = mapped_column(String, nullable=False)
    events: Mapped[list[WebhookEventType]] = mapped_column(
        JSONB, nullable=False, default=[]
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    organization_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    @declared_attr
    def organization(cls) -> Mapped["Organization"]:
        return relationship("Organization", lazy="raise")

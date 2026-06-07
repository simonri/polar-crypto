from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Literal, NotRequired, TypedDict
from uuid import UUID

from sqlalchemy import (
    TIMESTAMP,
    BigInteger,
    ColumnElement,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    and_,
)
from sqlalchemy.dialects.postgresql import CITEXT, JSONB
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy.orm import Mapped, declared_attr, mapped_column, relationship

from polar.config import settings
from polar.exceptions import PolarError
from polar.kit.currency import PresentmentCurrency
from polar.kit.db.models import RateLimitGroupMixin, RecordModel
from polar.kit.extensions.sqlalchemy import StringEnum

from .account import Account

if TYPE_CHECKING:
    from polar.email.sender import EmailFromReply

    from .payout_account import PayoutAccount
    from .product import Product


class PayoutAccountNotReady(PolarError):
    def __init__(self, organization: "Organization") -> None:
        self.organization = organization
        message = "Your payout account is not ready yet. Complete the setup to receive payouts."
        super().__init__(message, 403)


class OrganizationSocials(TypedDict):
    platform: str
    url: str


class OrganizationDetails(TypedDict, total=False):
    about: str
    product_description: str
    selling_categories: list[str]
    pricing_models: list[str]
    intended_use: str
    customer_acquisition: list[str]
    future_annual_revenue: int
    switching: bool
    switching_from: str | None
    previous_annual_revenue: int


class OrganizationSubscriptionSettings(TypedDict):
    allow_customer_updates: bool


_default_subscription_settings: OrganizationSubscriptionSettings = {
    "allow_customer_updates": True,
}


class OrganizationCustomerEmailSettings(TypedDict):
    order_confirmation: bool
    subscription_cancellation: bool
    subscription_confirmation: bool
    subscription_cycled: bool
    subscription_cycled_after_trial: bool
    subscription_past_due: bool
    subscription_renewal_reminder: bool
    subscription_revoked: bool
    subscription_trial_conversion_reminder: bool
    subscription_uncanceled: bool
    subscription_updated: bool


_default_customer_email_settings: OrganizationCustomerEmailSettings = {
    "order_confirmation": True,
    "subscription_cancellation": True,
    "subscription_confirmation": True,
    "subscription_cycled": True,
    "subscription_cycled_after_trial": True,
    "subscription_past_due": True,
    "subscription_renewal_reminder": True,
    "subscription_revoked": True,
    "subscription_trial_conversion_reminder": True,
    "subscription_uncanceled": True,
    "subscription_updated": True,
}


class CustomerPortalUsageSettings(TypedDict):
    show: bool


class CustomerPortalSubscriptionSettings(TypedDict):
    update_plan: bool


class CustomerPortalCustomerSettings(TypedDict):
    allow_email_change: NotRequired[bool]


class OrganizationCustomerPortalSettings(TypedDict):
    usage: CustomerPortalUsageSettings
    subscription: CustomerPortalSubscriptionSettings
    customer: NotRequired[CustomerPortalCustomerSettings]


_default_customer_portal_settings: OrganizationCustomerPortalSettings = {
    "usage": {"show": True},
    "subscription": {
        "update_plan": True,
    },
    "customer": {
        "allow_email_change": False,
    },
}


class OrganizationCheckoutSettings(TypedDict):
    require_3ds: bool


_default_checkout_settings: OrganizationCheckoutSettings = {
    "require_3ds": True,
}


class OrganizationIndividualLegalEntity(TypedDict):
    type: Literal["individual"]


class OrganizationCompanyLegalEntity(TypedDict):
    type: Literal["company"]
    registered_name: str


OrganizationLegalEntity = (
    OrganizationIndividualLegalEntity | OrganizationCompanyLegalEntity
)


class OrganizationStatus(StrEnum):
    # Legacy values kept in enum so existing DB rows load without error.
    # All new organizations start as ACTIVE; the review workflow is removed.
    CREATED = "created"
    REVIEW = "review"
    SNOOZED = "snoozed"
    DENIED = "denied"
    ACTIVE = "active"
    BLOCKED = "blocked"
    OFFBOARDING = "offboarding"

    def get_display_name(self) -> str:
        return {
            OrganizationStatus.CREATED: "Created",
            OrganizationStatus.REVIEW: "Review",
            OrganizationStatus.SNOOZED: "Snoozed",
            OrganizationStatus.DENIED: "Denied",
            OrganizationStatus.ACTIVE: "Active",
            OrganizationStatus.BLOCKED: "Blocked",
            OrganizationStatus.OFFBOARDING: "Offboarding",
        }[self]


class OrganizationCapabilities(TypedDict):
    checkout_payments: bool
    subscription_renewals: bool
    payouts: bool
    refunds: bool
    api_access: bool
    dashboard_access: bool


CapabilityName = Literal[
    "checkout_payments",
    "subscription_renewals",
    "payouts",
    "refunds",
    "api_access",
    "dashboard_access",
]


class InvalidStatusTransitionError(PolarError):
    def __init__(
        self, current: "OrganizationStatus", target: "OrganizationStatus"
    ) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"Cannot transition organization status from "
            f"{current.get_display_name()} to {target.get_display_name()}.",
            400,
        )


STATUS_CAPABILITIES: dict[OrganizationStatus, OrganizationCapabilities] = {
    OrganizationStatus.CREATED: {
        "checkout_payments": False,
        "subscription_renewals": False,
        "payouts": False,
        "refunds": False,
        "api_access": True,
        "dashboard_access": True,
    },
    OrganizationStatus.REVIEW: {
        "checkout_payments": True,
        "subscription_renewals": True,
        # Allowed under review: the request is reserved and held until approval
        # (see PayoutService.create), so `payouts` now means "may request".
        "payouts": True,
        "refunds": True,
        "api_access": True,
        "dashboard_access": True,
    },
    OrganizationStatus.SNOOZED: {
        "checkout_payments": True,
        "subscription_renewals": True,
        "payouts": True,
        "refunds": True,
        "api_access": True,
        "dashboard_access": True,
    },
    OrganizationStatus.ACTIVE: {
        "checkout_payments": True,
        "subscription_renewals": True,
        "payouts": True,
        "refunds": True,
        "api_access": True,
        "dashboard_access": True,
    },
    OrganizationStatus.DENIED: {
        "checkout_payments": False,
        "subscription_renewals": False,
        "payouts": False,
        "refunds": False,
        "api_access": True,
        "dashboard_access": True,
    },
    OrganizationStatus.OFFBOARDING: {
        "checkout_payments": True,
        "subscription_renewals": True,
        "payouts": False,
        "refunds": True,
        "api_access": True,
        "dashboard_access": True,
    },
    OrganizationStatus.BLOCKED: {
        "checkout_payments": False,
        "subscription_renewals": False,
        "payouts": False,
        "refunds": False,
        "api_access": False,
        "dashboard_access": False,
    },
}


CAPABILITY_METADATA: dict[CapabilityName, tuple[str, str]] = {
    "checkout_payments": (
        "Checkout payments",
        "Allow new checkouts and subscriptions.",
    ),
    "subscription_renewals": (
        "Subscription renewals",
        "Allow recurring billing cycles and dunning retries.",
    ),
    "payouts": (
        "Payouts",
        "Allow funds to be paid out to the payout account.",
    ),
    "refunds": (
        "Refunds",
        "Allow refunds to be issued on this organization's orders.",
    ),
    "api_access": (
        "API access",
        "Allow authenticated API access for team members.",
    ),
    "dashboard_access": (
        "Dashboard access",
        "Allow team members to sign in and access the dashboard.",
    ),
}

CAPABILITY_NAMES: frozenset[str] = frozenset(CAPABILITY_METADATA.keys())


ALLOWED_STATUS_TRANSITIONS: dict[OrganizationStatus, frozenset[OrganizationStatus]] = {
    OrganizationStatus.CREATED: frozenset(
        {OrganizationStatus.ACTIVE, OrganizationStatus.BLOCKED}
    ),
    OrganizationStatus.REVIEW: frozenset(
        {OrganizationStatus.ACTIVE, OrganizationStatus.BLOCKED}
    ),
    OrganizationStatus.SNOOZED: frozenset(
        {OrganizationStatus.ACTIVE, OrganizationStatus.BLOCKED}
    ),
    OrganizationStatus.DENIED: frozenset(
        {OrganizationStatus.ACTIVE, OrganizationStatus.BLOCKED}
    ),
    OrganizationStatus.ACTIVE: frozenset(
        {OrganizationStatus.BLOCKED, OrganizationStatus.OFFBOARDING}
    ),
    OrganizationStatus.OFFBOARDING: frozenset({OrganizationStatus.BLOCKED}),
    OrganizationStatus.BLOCKED: frozenset({OrganizationStatus.ACTIVE}),
}


class Organization(RateLimitGroupMixin, RecordModel):
    __tablename__ = "organizations"
    __table_args__ = (UniqueConstraint("slug"),)

    name: Mapped[str] = mapped_column(String, nullable=False, index=True)
    slug: Mapped[str] = mapped_column(CITEXT, nullable=False, unique=True)
    slug_history: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default="[]"
    )
    email: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    website: Mapped[str | None] = mapped_column(String, nullable=True, default=None)

    socials: Mapped[list[OrganizationSocials]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    details: Mapped[OrganizationDetails] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    details_submitted_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True)
    )

    status: Mapped[OrganizationStatus] = mapped_column(
        StringEnum(OrganizationStatus),
        nullable=False,
        default=OrganizationStatus.ACTIVE,
    )
    status_updated_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True
    )

    total_balance: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True, server_default="0"
    )

    internal_notes: Mapped[str | None] = mapped_column(Text, nullable=True)

    account_id: Mapped[UUID] = mapped_column(
        Uuid,
        ForeignKey("accounts.id", ondelete="restrict"),
        nullable=False,
        unique=True,
    )

    @declared_attr
    def account(cls) -> Mapped[Account]:
        return relationship(Account, lazy="raise", back_populates="organizations")

    payout_account_id: Mapped[UUID | None] = mapped_column(
        Uuid,
        ForeignKey("payout_accounts.id", ondelete="set null"),
        default=None,
        nullable=True,
        index=True,
    )

    @declared_attr
    def payout_account(cls) -> Mapped["PayoutAccount | None"]:
        return relationship("PayoutAccount", lazy="raise")

    onboarded_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))
    ai_onboarding_completed_at: Mapped[datetime | None] = mapped_column(
        TIMESTAMP(timezone=True), nullable=True, default=None
    )

    capabilities: Mapped[OrganizationCapabilities] = mapped_column(
        JSONB,
        nullable=False,
        default=lambda: {**STATUS_CAPABILITIES[OrganizationStatus.ACTIVE]},
    )

    country: Mapped[str | None] = mapped_column(String(2), nullable=True, default=None)

    profile_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    subscription_settings: Mapped[OrganizationSubscriptionSettings] = mapped_column(
        JSONB, nullable=False, default=_default_subscription_settings
    )

    customer_email_settings: Mapped[OrganizationCustomerEmailSettings] = mapped_column(
        JSONB, nullable=False, default=_default_customer_email_settings
    )

    customer_portal_settings: Mapped[OrganizationCustomerPortalSettings] = (
        mapped_column(JSONB, nullable=False, default=_default_customer_portal_settings)
    )

    checkout_settings: Mapped[OrganizationCheckoutSettings] = mapped_column(
        JSONB, nullable=False, default=_default_checkout_settings
    )

    legal_entity: Mapped[OrganizationLegalEntity | None] = mapped_column(
        JSONB, nullable=True, default=None
    )

    @property
    def allow_customer_updates(self) -> bool:
        return self.customer_portal_settings["subscription"]["update_plan"]

    #
    # Feature Flags
    #

    feature_settings: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, default=dict
    )

    @property
    def is_member_model_enabled(self) -> bool:
        return self.feature_settings.get("member_model_enabled", False)

    #
    # Currency and tax settings
    #
    default_presentment_currency: Mapped[PresentmentCurrency] = mapped_column(
        String(3), nullable=False, default="usd"
    )
    #
    # Fields synced from GitHub
    #

    # Org description or user bio
    bio: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    company: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    blog: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    location: Mapped[str | None] = mapped_column(String, nullable=True, default=None)
    twitter_username: Mapped[str | None] = mapped_column(
        String, nullable=True, default=None
    )

    #
    # End: Fields synced from GitHub
    #

    def _capability(self, name: CapabilityName) -> bool:
        return self.capabilities[name]

    @hybrid_property
    def can_authenticate(self) -> bool:
        return not self.is_deleted and self._capability("api_access")

    @can_authenticate.inplace.expression
    @classmethod
    def _can_authenticate_expression(cls) -> ColumnElement[bool]:
        return and_(
            cls.is_deleted.is_(False),
            cls.capabilities["api_access"].as_boolean().is_(True),
        )

    @hybrid_property
    def can_access_dashboard(self) -> bool:
        return not self.is_deleted and self._capability("dashboard_access")

    @can_access_dashboard.inplace.expression
    @classmethod
    def _can_access_dashboard_expression(cls) -> ColumnElement[bool]:
        return and_(
            cls.is_deleted.is_(False),
            cls.capabilities["dashboard_access"].as_boolean().is_(True),
        )

    @hybrid_property
    def can_accept_payments(self) -> bool:
        return self._capability("checkout_payments")

    @can_accept_payments.inplace.expression
    @classmethod
    def _can_accept_payments_expression(cls) -> ColumnElement[bool]:
        return cls.capabilities["checkout_payments"].as_boolean().is_(True)

    @hybrid_property
    def can_renew_subscriptions(self) -> bool:
        return self._capability("subscription_renewals")

    @can_renew_subscriptions.inplace.expression
    @classmethod
    def _can_renew_subscriptions_expression(cls) -> ColumnElement[bool]:
        return cls.capabilities["subscription_renewals"].as_boolean().is_(True)

    @hybrid_property
    def can_payout(self) -> bool:
        return self._capability("payouts")

    @can_payout.inplace.expression
    @classmethod
    def _can_payout_expression(cls) -> ColumnElement[bool]:
        return cls.capabilities["payouts"].as_boolean().is_(True)

    @hybrid_property
    def can_refund(self) -> bool:
        return self._capability("refunds")

    @can_refund.inplace.expression
    @classmethod
    def _can_refund_expression(cls) -> ColumnElement[bool]:
        return cls.capabilities["refunds"].as_boolean().is_(True)

    def set_status(self, status: OrganizationStatus) -> None:
        if (
            status != self.status
            and status not in ALLOWED_STATUS_TRANSITIONS[self.status]
        ):
            raise InvalidStatusTransitionError(self.status, status)
        self.status = status
        self.status_updated_at = datetime.now(UTC)
        self.capabilities = {**STATUS_CAPABILITIES[status]}

    @property
    def polar_site_url(self) -> str:
        return f"{settings.FRONTEND_BASE_URL}/{self.slug}"

    @property
    def account_url(self) -> str:
        return f"{settings.FRONTEND_BASE_URL}/dashboard/{self.slug}/finance/account"

    @property
    def customer_portal_subscription_update_plan(self) -> bool:
        return self.customer_portal_settings.get("subscription", {}).get(
            "update_plan", True
        )

    @property
    def checkout_require_3ds(self) -> bool:
        return self.checkout_settings.get("require_3ds", False)

    @declared_attr
    def all_products(cls) -> Mapped[list["Product"]]:
        return relationship("Product", lazy="raise", back_populates="organization")

    @declared_attr
    def products(cls) -> Mapped[list["Product"]]:
        return relationship(
            "Product",
            lazy="raise",
            primaryjoin=(
                "and_("
                "Product.organization_id == Organization.id, "
                "Product.is_archived.is_(False)"
                ")"
            ),
            viewonly=True,
        )

    def is_blocked(self) -> bool:
        return self.status == OrganizationStatus.BLOCKED

    def is_active(self) -> bool:
        return self.status == OrganizationStatus.ACTIVE

    def statement_descriptor(self, suffix: str = "") -> str:
        max_length = 22
        if suffix:
            space_for_slug = max_length - len(suffix)
            return self.slug[:space_for_slug] + suffix
        return self.slug[:max_length]

    @property
    def email_from_reply(self) -> "EmailFromReply":
        return {
            "from_name": f"{self.name} (via {settings.EMAIL_FROM_NAME})",
            "from_email_addr": f"{self.slug}@{settings.EMAIL_FROM_DOMAIN}",
            "reply_to_name": self.name,
            "reply_to_email_addr": self.email
            or settings.EMAIL_DEFAULT_REPLY_TO_EMAIL_ADDRESS,
        }

    def get_ready_payout_account(self) -> "PayoutAccount":
        """
        Return the payout account if it's ready to receive payouts.

        Returns:
            The payout account if it exists and is ready to receive payouts.

        Raises:
            PayoutAccountNotReady: If the payout account does not exist or is not ready to receive payouts.
        """

        if self.payout_account is not None and self.payout_account.is_payout_ready:
            return self.payout_account
        raise PayoutAccountNotReady(self)

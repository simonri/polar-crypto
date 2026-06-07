from datetime import datetime
from typing import Annotated, Literal

from pydantic import UUID4, AliasChoices, Discriminator, Field
from pydantic.aliases import AliasPath
from pydantic.json_schema import SkipJsonSchema

from polar.custom_field.data import CustomFieldDataOutputMixin
from polar.enums import SubscriptionRecurringInterval
from polar.kit.metadata import MetadataOutputMixin
from polar.kit.schemas import (
    PRICE_ID_EXAMPLE,
    PRODUCT_ID_EXAMPLE,
    SUBSCRIPTION_ID_EXAMPLE,
    IDSchema,
    SetSchemaReference,
    TimestampedSchema,
)
from polar.models.subscription import SubscriptionStatus

from .customer import (
    CustomerIndividual,
    CustomerTeam,
)


class CustomerStateSubscription(
    MetadataOutputMixin, CustomFieldDataOutputMixin, TimestampedSchema, IDSchema
):
    """An active customer subscription."""

    id: UUID4 = Field(
        description="The ID of the subscription.", examples=[SUBSCRIPTION_ID_EXAMPLE]
    )
    status: Literal[SubscriptionStatus.active, SubscriptionStatus.trialing] = Field(
        examples=["active", "trialing"]
    )
    amount: int = Field(description="The amount of the subscription.", examples=[1000])
    currency: str = Field(
        description="The currency of the subscription.", examples=["usd"]
    )
    recurring_interval: SubscriptionRecurringInterval = Field(
        description="The interval at which the subscription recurs."
    )
    current_period_start: datetime = Field(
        description="The start timestamp of the current billing period.",
        examples=["2025-02-03T13:37:00Z"],
    )
    current_period_end: datetime = Field(
        description="The end timestamp of the current billing period.",
        examples=["2025-03-03T13:37:00Z"],
    )
    trial_start: datetime | None = Field(
        description="The start timestamp of the trial period, if any.",
        examples=["2025-02-03T13:37:00Z"],
    )
    trial_end: datetime | None = Field(
        description="The end timestamp of the trial period, if any.",
        examples=["2025-03-03T13:37:00Z"],
    )
    cancel_at_period_end: bool = Field(
        description=(
            "Whether the subscription will be canceled "
            "at the end of the current period."
        ),
        examples=[False],
    )
    canceled_at: datetime | None = Field(
        description=(
            "The timestamp when the subscription was canceled. "
            "The subscription might still be active if `cancel_at_period_end` is `true`."
        ),
        examples=[None],
    )
    started_at: datetime | None = Field(
        description="The timestamp when the subscription started.",
        examples=["2025-01-03T13:37:00Z"],
    )
    ends_at: datetime | None = Field(
        description="The timestamp when the subscription will end.",
        examples=[None],
    )

    product_id: UUID4 = Field(
        description="The ID of the subscribed product.", examples=[PRODUCT_ID_EXAMPLE]
    )
    discount_id: UUID4 | None = Field(
        description="The ID of the applied discount, if any.", examples=[None]
    )

    price_id: SkipJsonSchema[UUID4] = Field(
        deprecated=True,
        examples=[PRICE_ID_EXAMPLE],
        validation_alias=AliasChoices(
            # Validate from stored webhook payload
            "price_id",
            # Validate from ORM model
            AliasPath("prices", 0, "id"),
        ),
    )


class _CustomerStateFields:
    """Mixin providing shared state fields for CustomerState variants."""

    active_subscriptions: list[CustomerStateSubscription] = Field(
        description="The customer's active subscriptions."
    )


class CustomerStateIndividual(_CustomerStateFields, CustomerIndividual):
    """
    A customer along with additional state information:

    * Active subscriptions
    """


class CustomerStateTeam(_CustomerStateFields, CustomerTeam):
    """
    A team customer along with additional state information:

    * Active subscriptions
    """


CustomerState = Annotated[
    CustomerStateIndividual | CustomerStateTeam,
    Discriminator("type"),
    SetSchemaReference("CustomerState"),
]

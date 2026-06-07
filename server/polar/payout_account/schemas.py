from typing import Literal
from uuid import UUID

from pydantic import Field

from polar.enums import PayoutAccountType
from polar.kit.schemas import IDSchema, Schema, TimestampedSchema


class PayoutAccountCreate(Schema):
    type: Literal[PayoutAccountType.manual] = PayoutAccountType.manual
    organization_id: UUID = Field(
        description="Organization ID to create or get account for"
    )
    country: str = Field(
        default="US",
        min_length=2,
        max_length=2,
        description="ISO 3166-1 alpha-2 country code",
    )


class PayoutAccount(TimestampedSchema, IDSchema):
    type: PayoutAccountType
    country: str
    currency: str
    is_payout_ready: bool


class PayoutAccountLink(Schema):
    url: str

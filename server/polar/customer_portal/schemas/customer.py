from typing import Annotated

from polar.kit.address import Address, AddressInput
from polar.kit.schemas import (
    EmptyStrToNoneValidator,
    IDSchema,
    Schema,
    TimestampedSchema,
)
from polar.models.customer import CustomerType


class CustomerPortalOAuthAccount(Schema):
    account_id: str
    account_username: str | None


class CustomerPortalCustomer(IDSchema, TimestampedSchema):
    email: str | None
    email_verified: bool
    name: str | None
    billing_name: str | None
    billing_address: Address | None
    oauth_accounts: dict[str, CustomerPortalOAuthAccount]
    type: CustomerType | None = None


class CustomerPortalCustomerUpdate(Schema):
    billing_name: Annotated[str | None, EmptyStrToNoneValidator] = None
    billing_address: AddressInput | None = None

from __future__ import annotations

from pydantic import UUID4, field_validator

from polar.kit.schemas import Schema, TimestampedSchema


class CryptoPayoutWalletCreate(Schema):
    payout_account_id: UUID4
    currency: str
    wallet_address: str

    @field_validator("currency")
    @classmethod
    def normalize_currency(cls, v: str) -> str:
        return v.lower().strip()

    @field_validator("wallet_address")
    @classmethod
    def strip_address(cls, v: str) -> str:
        return v.strip()


class CryptoPayoutWalletRead(TimestampedSchema):
    id: UUID4
    account_id: UUID4
    currency: str
    wallet_address: str
    is_active: bool

import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import TIMESTAMP, BigInteger, String, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from polar.kit.db.models import Model
from polar.kit.extensions.sqlalchemy.types import StringEnum
from polar.kit.utils import generate_uuid, utc_now


class Processor(StrEnum):
    """Supported payment or payout processors."""

    crypto = "crypto"


class ProcessorTransaction(Model):
    __tablename__ = "processor_transactions"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True, default=generate_uuid)
    timestamp: Mapped[datetime.datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, default=utc_now, index=True
    )
    processor: Mapped[Processor] = mapped_column(
        StringEnum(Processor), index=True, nullable=False
    )
    processor_id: Mapped[str] = mapped_column(
        String, nullable=False, index=True, unique=True
    )
    type: Mapped[str] = mapped_column(String, nullable=False, index=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)
    fee: Mapped[int] = mapped_column(BigInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

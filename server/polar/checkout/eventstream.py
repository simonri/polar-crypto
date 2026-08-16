from enum import StrEnum
from typing import Literal, TypedDict, overload

from polar.eventstream.service import publish
from polar.models.checkout import CheckoutStatus


class CheckoutEvent(StrEnum):
    updated = "checkout.updated"
    crypto_invoice_updated = "checkout.crypto_invoice.updated"
    order_created = "checkout.order_created"
    subscription_created = "checkout.subscription_created"
    webhook_event_delivered = "checkout.webhook_event_delivered"


class CheckoutEventUpdatedPayload(TypedDict):
    status: CheckoutStatus


class CheckoutEventCryptoInvoiceUpdatedPayload(TypedDict):
    status: str


class CheckoutEventWebhookEventDeliveredPayload(TypedDict):
    status: CheckoutStatus


@overload
async def publish_checkout_event(
    client_secret: str,
    event: Literal[CheckoutEvent.updated],
    payload: CheckoutEventUpdatedPayload,
) -> None: ...


@overload
async def publish_checkout_event(
    client_secret: str,
    event: Literal[CheckoutEvent.crypto_invoice_updated],
    payload: CheckoutEventCryptoInvoiceUpdatedPayload,
) -> None: ...


@overload
async def publish_checkout_event(
    client_secret: str,
    event: Literal[CheckoutEvent.order_created],
) -> None: ...


@overload
async def publish_checkout_event(
    client_secret: str,
    event: Literal[CheckoutEvent.subscription_created],
) -> None: ...


@overload
async def publish_checkout_event(
    client_secret: str,
    event: Literal[CheckoutEvent.webhook_event_delivered],
    payload: CheckoutEventWebhookEventDeliveredPayload,
) -> None: ...


async def publish_checkout_event(
    client_secret: str,
    event: CheckoutEvent,
    payload: CheckoutEventUpdatedPayload
    | CheckoutEventCryptoInvoiceUpdatedPayload
    | CheckoutEventWebhookEventDeliveredPayload
    | None = None,
) -> None:
    return await publish(
        event, {**payload} if payload else {}, checkout_client_secret=client_secret
    )

"""
Dramatiq tasks and APScheduler cron jobs for crypto payment processing.
"""

from __future__ import annotations

import uuid

import structlog

from polar.logging import Logger
from polar.worker import AsyncSessionMaker, CronTrigger, TaskPriority, actor

log: Logger = structlog.get_logger()


@actor(actor_name="crypto.invoice.process", priority=TaskPriority.HIGH)
async def process_crypto_invoice(invoice_id: uuid.UUID) -> None:
    """
    Poll a specific invoice from the daemon and drive state transitions.
    """
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from polar.integrations.crypto.payment_processor import crypto_payment_processor
    from polar.integrations.crypto.service import crypto_service
    from polar.models.crypto_invoice import CryptoInvoice, CryptoInvoiceStatus

    if not crypto_service._initialized:
        crypto_service.initialize()

    async with AsyncSessionMaker() as session:
        stmt = (
            select(CryptoInvoice)
            .where(CryptoInvoice.id == invoice_id)
            .options(selectinload(CryptoInvoice.payment_methods))
        )
        result = await session.execute(stmt)
        invoice = result.scalar_one_or_none()

        if invoice is None or invoice.status not in (
            CryptoInvoiceStatus.pending,
            CryptoInvoiceStatus.unconfirmed,
        ):
            return

        for pm in invoice.payment_methods:
            if pm.is_used:
                continue
            try:
                status = await crypto_service.get_request_status(
                    pm.currency, pm.lookup_field
                )
                status_str = status.get("status_str") or status.get("status")
                if status_str in ("Paid", "Confirmed", "complete"):
                    await crypto_payment_processor._process_payment(
                        session,
                        invoice,
                        pm,
                        {
                            "amount": status.get("amount", float(pm.amount)),
                            "confirmations": status.get("confirmations", 1),
                            "tx_hashes": status.get("tx_hashes", []),
                        },
                    )
                    break
            except Exception as e:
                log.warning(
                    "crypto.invoice.poll_error",
                    invoice_id=str(invoice_id),
                    currency=pm.currency,
                    error=str(e),
                )


@actor(
    actor_name="crypto.poll_pending_invoices",
    priority=TaskPriority.HIGH,
    cron_trigger=CronTrigger(minute="*"),  # every minute
)
async def poll_pending_crypto_invoices() -> None:
    """
    Cron job: expire stale invoices and enqueue a processing task for each
    pending/unconfirmed invoice.
    """
    from sqlalchemy import select

    from polar.integrations.crypto.payment_processor import _expire_stale_invoices
    from polar.kit.utils import utc_now
    from polar.models.crypto_invoice import CryptoInvoice, CryptoInvoiceStatus
    from polar.worker import enqueue_job

    async with AsyncSessionMaker() as session:
        await _expire_stale_invoices(session)

        stmt = select(CryptoInvoice.id).where(
            CryptoInvoice.status.in_(
                [
                    CryptoInvoiceStatus.pending,
                    CryptoInvoiceStatus.unconfirmed,
                ]
            ),
            CryptoInvoice.expiry > utc_now(),
        )
        result = await session.execute(stmt)
        invoice_ids = result.scalars().all()

    for invoice_id in invoice_ids:
        enqueue_job("crypto.invoice.process", invoice_id=invoice_id)

    if invoice_ids:
        log.debug("crypto.poll.enqueued", count=len(invoice_ids))

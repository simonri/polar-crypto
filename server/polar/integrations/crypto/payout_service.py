"""
Crypto payout account and payout management.

Handles wallet address registration and crypto payouts for creators
who use the crypto payment processor.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from polar.exceptions import PolarError
from polar.integrations.crypto.exchange_rate import ExchangeRateService
from polar.integrations.crypto.service import CryptoServiceError, crypto_service
from polar.logging import Logger
from polar.models.crypto_payout_wallet import CryptoPayoutWallet
from polar.models.payout_account import PayoutAccount
from polar.postgres import AsyncSession
from polar.redis import create_redis

log: Logger = structlog.get_logger()


class CryptoPayoutError(PolarError):
    pass


class InvalidWalletAddressError(CryptoPayoutError):
    def __init__(self, currency: str, address: str) -> None:
        self.currency = currency
        self.address = address
        super().__init__(f"Invalid {currency} wallet address: {address}", 422)


class WalletNotFoundError(CryptoPayoutError):
    def __init__(self, account_id: UUID, currency: str) -> None:
        super().__init__(
            f"No active {currency} wallet configured for account {account_id}", 422
        )


class WalletAlreadyExistsError(CryptoPayoutError):
    def __init__(self, currency: str) -> None:
        super().__init__(f"A {currency} wallet is already registered.", 409)


class CryptoPayoutAccountService:
    async def add_wallet(
        self,
        session: AsyncSession,
        account: PayoutAccount,
        currency: str,
        wallet_address: str,
    ) -> CryptoPayoutWallet:
        """Register a crypto wallet address for payouts."""
        currency = currency.lower()

        # Initialize daemon if needed
        if not crypto_service._initialized:
            crypto_service.initialize()

        # Validate address with the daemon (best-effort)
        try:
            is_valid = await crypto_service.validate_address(currency, wallet_address)
            if not is_valid:
                raise InvalidWalletAddressError(currency, wallet_address)
        except CryptoServiceError:
            # Daemon unavailable — accept the address without validation
            log.warning(
                "crypto.payout.address_validation_skipped",
                currency=currency,
                address=wallet_address,
            )

        wallet = CryptoPayoutWallet(
            account_id=account.id,
            currency=currency,
            wallet_address=wallet_address,
            is_active=True,
        )
        session.add(wallet)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            raise WalletAlreadyExistsError(currency)

        log.info(
            "crypto.payout.wallet_added",
            account_id=str(account.id),
            currency=currency,
            address=wallet_address,
        )
        return wallet

    async def remove_wallet(
        self,
        session: AsyncSession,
        account: PayoutAccount,
        currency: str,
    ) -> None:
        """Deactivate a crypto payout wallet."""
        wallet = await self._get_wallet(session, account.id, currency)
        if wallet is None:
            raise WalletNotFoundError(account.id, currency)
        wallet.is_active = False
        session.add(wallet)

    async def list_wallets(
        self,
        session: AsyncSession,
        account_id: UUID,
    ) -> list[CryptoPayoutWallet]:
        stmt = select(CryptoPayoutWallet).where(
            CryptoPayoutWallet.account_id == account_id,
            CryptoPayoutWallet.is_active.is_(True),
        )
        result = await session.execute(stmt)
        return list(result.scalars().all())

    async def create_payout(
        self,
        session: AsyncSession,
        account: PayoutAccount,
        amount_cents: int,
        crypto_currency: str,
        fiat_currency: str = "usd",
    ) -> str:
        """
        Broadcast a crypto payout from Polar's master wallet to the creator's
        registered wallet address.  Returns the transaction hash.
        """
        crypto_currency = crypto_currency.lower()

        wallet = await self._get_wallet(session, account.id, crypto_currency)
        if wallet is None:
            raise WalletNotFoundError(account.id, crypto_currency)

        if not crypto_service._initialized:
            crypto_service.initialize()

        redis = create_redis("worker")
        rate_service = ExchangeRateService(redis)
        crypto_amount = await rate_service.convert_fiat_cents_to_crypto(
            amount_cents, crypto_currency, fiat_currency
        )

        tx_hash = await crypto_service.broadcast_transaction(
            currency=crypto_currency,
            destination=wallet.wallet_address,
            amount_crypto=crypto_amount,
        )
        log.info(
            "crypto.payout.sent",
            account_id=str(account.id),
            currency=crypto_currency,
            amount_cents=amount_cents,
            crypto_amount=str(crypto_amount),
            destination=wallet.wallet_address,
            tx_hash=tx_hash,
        )
        return tx_hash

    async def create_refund(
        self,
        session: AsyncSession,
        amount_cents: int,
        crypto_currency: str,
        return_address: str,
        fiat_currency: str = "usd",
    ) -> str:
        """
        Broadcast a crypto refund from Polar's master wallet to the customer's
        supplied return address.  Returns the transaction hash.
        """
        crypto_currency = crypto_currency.lower()

        if not crypto_service._initialized:
            crypto_service.initialize()

        redis = create_redis("worker")
        rate_service = ExchangeRateService(redis)
        crypto_amount = await rate_service.convert_fiat_cents_to_crypto(
            amount_cents, crypto_currency, fiat_currency
        )

        tx_hash = await crypto_service.broadcast_transaction(
            currency=crypto_currency,
            destination=return_address,
            amount_crypto=crypto_amount,
        )
        log.info(
            "crypto.refund.sent",
            currency=crypto_currency,
            amount_cents=amount_cents,
            crypto_amount=str(crypto_amount),
            destination=return_address,
            tx_hash=tx_hash,
        )
        return tx_hash

    async def _get_wallet(
        self,
        session: AsyncSession,
        account_id: UUID,
        currency: str,
    ) -> CryptoPayoutWallet | None:
        stmt = select(CryptoPayoutWallet).where(
            CryptoPayoutWallet.account_id == account_id,
            CryptoPayoutWallet.currency == currency.lower(),
            CryptoPayoutWallet.is_active.is_(True),
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()


crypto_payout_account_service = CryptoPayoutAccountService()

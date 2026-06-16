"""
CryptoService: thin wrapper over the bitcart SDK.

Each supported currency maps to a daemon process (Electrum for BTC/LTC,
Web3.py for ETH) that the SDK talks to via JSON-RPC.  Polar never holds
wallet private keys—daemons are configured with xpub keys only and
generate deterministic receiving addresses on the fly.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from decimal import Decimal
from typing import Any

import structlog

from polar.config import settings
from polar.logging import Logger

log: Logger = structlog.get_logger()

# Confirmation thresholds per currency (blocks before "complete")
CONFIRMATION_THRESHOLDS: dict[str, int] = {
    "btc": 1,
    "ltc": 6,
    "eth": 12,
    "matic": 12,
    "bnb": 12,
    "trx": 19,
    # Solana "confirmed" commitment settles in ~0.4 s; 1 is sufficient
    "sol": 1,
    "sol_usdc": 1,
}


class CryptoServiceError(Exception):
    pass


class DaemonUnavailableError(CryptoServiceError):
    def __init__(self, currency: str) -> None:
        super().__init__(f"Daemon for {currency} is unavailable")
        self.currency = currency


class InvalidAddressError(CryptoServiceError):
    def __init__(self, currency: str, address: str) -> None:
        super().__init__(f"Invalid {currency} address: {address}")
        self.currency = currency
        self.address = address


class CryptoService:
    """Manages per-currency bitcart SDK coin instances."""

    def __init__(self) -> None:
        self._coins: dict[str, Any] = {}
        self._initialized = False

    def _get_coin_class(self, currency: str) -> Any:
        try:
            from bitcart import COINS  # type: ignore[attr-defined]

            coin_cls = COINS.get(currency.upper())
            if coin_cls is None:
                raise CryptoServiceError(
                    f"Currency {currency} not supported by bitcart SDK"
                )
            return coin_cls
        except ImportError as e:
            raise CryptoServiceError("bitcart SDK not installed") from e

    def initialize(self) -> None:
        """
        Initialize coin instances from config.  Called lazily on first use.
        Each currency maps to a daemon URL + xpub configured via env vars.
        """
        if self._initialized:
            return

        daemon_configs = settings.get_crypto_daemon_configs()
        for currency, (url, xpub) in daemon_configs.items():
            try:
                coin_cls = self._get_coin_class(currency)
                coin = coin_cls(
                    rpc_url=url,
                    rpc_user=settings.CRYPTO_DAEMON_RPC_USER,
                    rpc_pass=settings.CRYPTO_DAEMON_RPC_PASS,
                    xpub=xpub,
                )
                # The Bitcart SDK sends expiry= but the daemon expects expiration=.
                # Patch the key name to match what the daemon actually accepts.
                coin.EXPIRATION_KEY = "expiration"
                self._coins[currency] = coin
                log.info("crypto.daemon.initialized", currency=currency, url=url)
            except Exception as e:
                log.warning(
                    "crypto.daemon.init_failed", currency=currency, error=str(e)
                )

        # Solana uses direct RPC — no Bitcart daemon needed
        enabled = [
            c.strip().lower()
            for c in settings.CRYPTO_CURRENCIES.split(",")
            if c.strip()
        ]
        sol_currencies = [c for c in enabled if c in ("sol", "sol_usdc")]
        if sol_currencies and settings.CRYPTO_SOL_MERCHANT_PUBKEY:
            from polar.integrations.crypto.solana import SolanaAdapter

            for cur in sol_currencies:
                try:
                    self._coins[cur] = SolanaAdapter(
                        currency=cur,
                        merchant_pubkey=settings.CRYPTO_SOL_MERCHANT_PUBKEY,
                        rpc_url=settings.CRYPTO_SOL_RPC_URL,
                        network=settings.CRYPTO_SOL_NETWORK,
                    )
                    log.info(
                        "crypto.solana.initialized",
                        currency=cur,
                        rpc_url=settings.CRYPTO_SOL_RPC_URL,
                    )
                except Exception as e:
                    log.warning("crypto.solana.init_failed", currency=cur, error=str(e))
        elif sol_currencies:
            log.warning(
                "crypto.solana.skipped",
                reason="CRYPTO_SOL_MERCHANT_PUBKEY not set",
                currencies=sol_currencies,
            )

        self._initialized = True

    def supported_currencies(self) -> list[str]:
        if not self._initialized:
            self.initialize()
        return list(self._coins.keys())

    def _coin(self, currency: str) -> Any:
        if not self._initialized:
            self.initialize()
        coin = self._coins.get(currency.lower())
        if coin is None:
            raise DaemonUnavailableError(currency)
        return coin

    async def add_payment_request(
        self,
        currency: str,
        amount_crypto: Decimal,
        description: str,
        expiry_seconds: int = 3600,
    ) -> tuple[str, str]:
        """
        Ask the daemon to generate a new receiving address for this amount.
        Returns (payment_address, lookup_field/request_id).
        """
        coin = self._coin(currency)
        try:
            # SDK add_request() expects expire in *minutes*; convert from seconds.
            expire_minutes = expiry_seconds / 60
            result = await coin.add_request(
                float(amount_crypto), description, expire_minutes
            )
            address: str = (
                result.get("address")
                or result.get("URI", "").split(":")[-1].split("?")[0]
            )
            lookup_field: str = str(
                result.get("request_id")
                or result.get("ID")
                or result.get("id")
                or address
            )
            log.info(
                "crypto.payment_request.created",
                currency=currency,
                address=address,
                amount=str(amount_crypto),
            )
            return address, lookup_field
        except Exception as e:
            raise CryptoServiceError(
                f"Failed to create payment request for {currency}: {e}"
            ) from e

    async def get_request_status(
        self, currency: str, lookup_field: str
    ) -> dict[str, Any]:
        """Fetch the current status of a payment request from the daemon."""
        coin = self._coin(currency)
        try:
            return await coin.get_request(lookup_field)
        except Exception:
            # After daemon restart, the in-memory address index may not be rebuilt
            # from disk. Fall back to scanning list_requests to find by address or
            # request_id.
            try:
                all_requests: list[dict[str, Any]] = await coin.server.list_requests()
                for req in all_requests or []:
                    if (
                        req.get("address") == lookup_field
                        or req.get("request_id") == lookup_field
                    ):
                        return req
            except Exception:
                pass
            raise CryptoServiceError(
                f"Failed to get request status for {currency}/{lookup_field}"
            )

    async def broadcast_transaction(
        self,
        currency: str,
        destination: str,
        amount_crypto: Decimal,
    ) -> str:
        """
        Send crypto from Polar's master wallet to a destination address.
        Returns the transaction hash.
        """
        coin = self._coin(currency)
        try:
            result = await coin.pay_to(destination, float(amount_crypto))
            tx_hash: str = result if isinstance(result, str) else str(result)
            log.info(
                "crypto.transaction.broadcast",
                currency=currency,
                destination=destination,
                amount=str(amount_crypto),
                tx_hash=tx_hash,
            )
            return tx_hash
        except Exception as e:
            raise CryptoServiceError(
                f"Failed to broadcast {currency} transaction: {e}"
            ) from e

    async def validate_address(self, currency: str, address: str) -> bool:
        """Ask the daemon whether an address is valid for this currency."""
        coin = self._coin(currency)
        try:
            result = await coin.validate_address(address)
            return bool(result)
        except Exception:
            return False

    async def get_exchange_rate(self, crypto: str, fiat: str = "usd") -> Decimal:
        """
        Get fiat/crypto exchange rate.
        Falls back to CoinGecko HTTP fetch if the daemon doesn't expose rate().
        """
        coin = self._coin(crypto)
        try:
            rate = await coin.rate(fiat.upper())
            return Decimal(str(rate))
        except Exception:
            # Fallback: fetch directly from CoinGecko
            return await _fetch_coingecko_rate(crypto, fiat)

    async def get_wallet_balance(self, currency: str) -> dict[str, Decimal]:
        """Fetch master wallet balance from the daemon."""
        coin = self._coin(currency)
        try:
            return await coin.balance()
        except Exception as e:
            raise CryptoServiceError(f"Failed to get {currency} balance: {e}") from e

    def subscribe_to_payments(
        self,
        currency: str,
        callback: Callable[[str, dict[str, Any]], Coroutine[Any, Any, None]],
    ) -> None:
        """
        Subscribe to real-time payment events from the daemon WebSocket.
        The callback receives (currency, event_dict).
        """
        coin = self._coin(currency)

        async def _on_event(event: dict[str, Any]) -> None:
            await callback(currency, event)

        try:
            coin.add_event_listener("new_payment", _on_event)
        except AttributeError:
            log.warning(
                "crypto.daemon.no_websocket_support",
                currency=currency,
                note="Will rely on polling instead",
            )


# ─── CoinGecko fallback ──────────────────────────────────────────────────────

_COINGECKO_IDS: dict[str, str] = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "ltc": "litecoin",
    "matic": "matic-network",
    "bnb": "binancecoin",
    "trx": "tron",
    "sol": "solana",
    # sol_usdc intentionally omitted — stablecoin, rate is always 1
}


async def _fetch_coingecko_rate(crypto: str, fiat: str) -> Decimal:
    import httpx

    coin_id = _COINGECKO_IDS.get(crypto.lower())
    if coin_id is None:
        raise CryptoServiceError(
            f"No CoinGecko ID mapping for {crypto}; cannot fetch rate"
        )
    url = "https://api.coingecko.com/api/v3/simple/price"
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.get(url, params={"ids": coin_id, "vs_currencies": fiat})
        resp.raise_for_status()
        data = resp.json()
    rate_value = data[coin_id][fiat.lower()]
    return Decimal(str(rate_value))


# Singleton — initialized at app startup via crypto_service.initialize()
crypto_service = CryptoService()

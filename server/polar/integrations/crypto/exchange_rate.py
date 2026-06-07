"""
ExchangeRateService: converts fiat amounts to crypto amounts.

Rates are cached in Redis for 5 minutes to avoid hammering CoinGecko.
"""

from __future__ import annotations

from decimal import ROUND_UP, Decimal

import httpx
import structlog
from redis.asyncio import Redis

from polar.logging import Logger

log: Logger = structlog.get_logger()

# Precision: 8 decimal places for UTXO coins, 18 for EVM — we use 8 as the
# common denominator displayed to users.
CRYPTO_PRECISION = Decimal("0.00000001")

_COINGECKO_IDS: dict[str, str] = {
    "btc": "bitcoin",
    "eth": "ethereum",
    "ltc": "litecoin",
    "matic": "matic-network",
    "bnb": "binancecoin",
    "trx": "tron",
}

_RATE_CACHE_TTL = 300  # 5 minutes


class ExchangeRateError(Exception):
    pass


class ExchangeRateService:
    def __init__(self, redis: Redis) -> None:  # type: ignore[type-arg]
        self._redis = redis

    async def get_rate(self, crypto: str, fiat: str = "usd") -> Decimal:
        """Return the exchange rate: 1 unit of fiat costs N units of crypto."""
        cache_key = f"polar:crypto:rate:{crypto.lower()}:{fiat.lower()}"
        cached = await self._redis.get(cache_key)
        if cached:
            return Decimal(cached.decode() if isinstance(cached, bytes) else cached)

        rate = await self._fetch_from_coingecko(crypto, fiat)
        await self._redis.setex(cache_key, _RATE_CACHE_TTL, str(rate))
        return rate

    async def convert_fiat_cents_to_crypto(
        self,
        amount_cents: int,
        crypto: str,
        fiat: str = "usd",
    ) -> Decimal:
        """Convert an amount in fiat cents to the equivalent crypto amount."""
        rate = await self.get_rate(crypto, fiat)
        # rate = crypto_per_fiat_unit (e.g. 0.000025 BTC per 1 USD)
        fiat_amount = Decimal(amount_cents) / Decimal(100)
        # We want: fiat_amount / (fiat_per_crypto)
        # rate from CoinGecko is fiat_per_crypto (e.g. 40000 USD per BTC)
        crypto_amount = (fiat_amount / rate).quantize(
            CRYPTO_PRECISION, rounding=ROUND_UP
        )
        log.debug(
            "exchange_rate.convert",
            amount_cents=amount_cents,
            fiat=fiat,
            crypto=crypto,
            rate=str(rate),
            result=str(crypto_amount),
        )
        return crypto_amount

    async def _fetch_from_coingecko(self, crypto: str, fiat: str) -> Decimal:
        coin_id = _COINGECKO_IDS.get(crypto.lower())
        if coin_id is None:
            raise ExchangeRateError(
                f"No CoinGecko mapping for {crypto}; cannot fetch rate"
            )
        url = "https://api.coingecko.com/api/v3/simple/price"
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(
                url, params={"ids": coin_id, "vs_currencies": fiat.lower()}
            )
            resp.raise_for_status()
            data = resp.json()
        rate_value = data[coin_id][fiat.lower()]
        log.debug(
            "exchange_rate.fetched",
            crypto=crypto,
            fiat=fiat,
            rate=rate_value,
        )
        return Decimal(str(rate_value))

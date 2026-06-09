"""
SolanaAdapter: accepts SOL and USDC payments using the Solana Pay reference-key pattern.

Architecture
------------
One static merchant wallet holds all received funds.  For each invoice Polar
generates a fresh throwaway *reference keypair* and stores only its public key
as the payment method's ``lookup_field``.  The reference pubkey is attached as a
read-only, non-signer account key in the customer's transfer instruction.
Solana validators index transactions by every account key they touch, so

    getSignaturesForAddress(reference_pubkey)

finds exactly that invoice's payment — no address-gap tracking, no sweeping.

For USDC: the customer sends to the merchant's Associated Token Account (ATA)
for the USDC mint.  The ATA must be initialised once before going live.

RPC transport is plain httpx (already a project dependency); solders provides
keypair generation and the PDA derivation used to compute the merchant ATA.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import httpx
import structlog

from polar.integrations.crypto.service import CryptoServiceError
from polar.logging import Logger

log: Logger = structlog.get_logger()

# USDC mint addresses
USDC_MINT_MAINNET = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
USDC_MINT_DEVNET = "4zMMC9srt5Ri5X14GAgXhaHii3GnPAEERYPJgZJDncDU"

# Well-known Solana program IDs
_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
_ASSOC_TOKEN_PROG_ID = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJe1bsS"


def _get_associated_token_address(owner_b58: str, mint_b58: str) -> str:
    """
    Derive the Associated Token Account address for (owner, mint).
    Uses SLIP-0010 / Solana's findProgramAddress via solders.
    """
    from solders.pubkey import Pubkey

    owner = Pubkey.from_string(owner_b58)
    mint = Pubkey.from_string(mint_b58)
    token_prog = Pubkey.from_string(_TOKEN_PROGRAM_ID)
    assoc_prog = Pubkey.from_string(_ASSOC_TOKEN_PROG_ID)

    seeds = [bytes(owner), bytes(token_prog), bytes(mint)]
    ata, _ = Pubkey.find_program_address(seeds, assoc_prog)
    return str(ata)


class _ServerProxy:
    """
    Provides coin.server.list_requests() expected by CryptoService's fallback
    path.  For Solana the polling loop uses get_request(lookup_field) directly,
    so this just returns an empty list.
    """

    async def list_requests(self) -> list[dict[str, Any]]:
        return []


class SolanaAdapter:
    """
    Implements the same duck-type interface as Bitcart coin objects so that
    CryptoService treats Solana identically to BTC/LTC/ETH.

    currency must be "sol" or "sol_usdc".
    """

    def __init__(
        self,
        currency: str,
        merchant_pubkey: str,
        rpc_url: str,
        network: str = "mainnet-beta",
    ) -> None:
        self.currency = currency.lower()
        self._merchant_pubkey_str = merchant_pubkey
        self._rpc_url = rpc_url
        self._network = network
        self.server = _ServerProxy()

        if self.currency == "sol_usdc":
            usdc_mint = USDC_MINT_DEVNET if network == "devnet" else USDC_MINT_MAINNET
            self._usdc_mint_str = usdc_mint
            self._merchant_ata_str = _get_associated_token_address(
                merchant_pubkey, usdc_mint
            )
            log.info(
                "solana.adapter.init",
                currency=currency,
                merchant=merchant_pubkey,
                ata=self._merchant_ata_str,
            )
        else:
            self._usdc_mint_str = ""
            self._merchant_ata_str = ""
            log.info(
                "solana.adapter.init",
                currency=currency,
                merchant=merchant_pubkey,
            )

    # ------------------------------------------------------------------ RPC --

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(self._rpc_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
        if "error" in data:
            raise CryptoServiceError(
                f"Solana RPC {method} error: {data['error'].get('message', data['error'])}"
            )
        return data["result"]

    # ------------------------------------------------ Bitcart coin interface --

    async def add_request(
        self,
        amount: float,
        description: str,
        expire_minutes: float = 15,
    ) -> dict[str, Any]:
        """
        Generate a fresh reference keypair per invoice.  Only the pubkey is
        kept — the private key is discarded immediately since we only need to
        watch for incoming transactions, not sign anything.
        """
        from solders.keypair import Keypair

        reference = str(Keypair().pubkey())

        # For SOL the customer sends to the merchant's native address;
        # for USDC they send to the merchant's USDC token account (ATA).
        receive_address = (
            self._merchant_ata_str
            if self.currency == "sol_usdc"
            else self._merchant_pubkey_str
        )

        log.info(
            "solana.payment_request.created",
            currency=self.currency,
            reference=reference,
            address=receive_address,
            amount=amount,
        )
        return {"address": receive_address, "request_id": reference}

    async def get_request(self, lookup_field: str) -> dict[str, Any]:
        """
        Poll for a transaction that includes the reference pubkey as an account
        key.  Returns a status dict compatible with CryptoPaymentProcessor.
        """
        try:
            sigs = await self._rpc(
                "getSignaturesForAddress",
                [lookup_field, {"limit": 10, "commitment": "confirmed"}],
            )
        except CryptoServiceError:
            return {"status": "pending", "confirmations": 0}

        if not sigs:
            return {"status": "pending", "confirmations": 0}

        for sig_info in sigs:
            sig = sig_info.get("signature", "")
            if sig_info.get("err"):
                continue  # skip failed transactions

            validated = await self._validate_transaction(sig)
            if validated:
                log.info(
                    "solana.payment.detected",
                    currency=self.currency,
                    reference=lookup_field,
                    signature=sig,
                    amount=str(validated["amount"]),
                )
                return {
                    "status": "complete",
                    "confirmations": 1,
                    "amount": float(validated["amount"]),
                    "tx_hashes": [sig],
                }

        return {"status": "pending", "confirmations": 0}

    async def validate_address(self, address: str) -> bool:
        try:
            from solders.pubkey import Pubkey

            Pubkey.from_string(address)
            return True
        except Exception:
            return False

    async def balance(self) -> dict[str, Decimal]:
        if self.currency == "sol_usdc":
            result = await self._rpc(
                "getTokenAccountBalance",
                [self._merchant_ata_str, {"commitment": "confirmed"}],
            )
            value = (result or {}).get("value") or {}
            raw = int(value.get("amount", "0"))
            amount = Decimal(raw) / Decimal(10**6)
        else:
            result = await self._rpc(
                "getBalance",
                [self._merchant_pubkey_str, {"commitment": "confirmed"}],
            )
            lamports = (result or {}).get("value", 0)
            amount = Decimal(lamports) / Decimal(10**9)

        return {
            "confirmed": amount,
            "unconfirmed": Decimal(0),
            "unmatured": Decimal(0),
            "lightning": Decimal(0),
        }

    async def pay_to(self, destination: str, amount: float) -> str:
        raise CryptoServiceError(
            "Solana payouts are not yet implemented. "
            "Fund payouts manually from the merchant wallet."
        )

    async def rate(self, fiat_upper: str) -> Decimal:
        if self.currency == "sol_usdc":
            return Decimal("1")
        # Raise to let CryptoService fall back to CoinGecko
        raise CryptoServiceError(
            f"No on-chain rate for {self.currency}; use CoinGecko fallback"
        )

    # ----------------------------------------------- Transaction validation --

    async def _validate_transaction(self, signature: str) -> dict[str, Any] | None:
        """
        Fetch the full transaction and verify that the expected recipient
        received a positive amount.  Returns {amount: Decimal} on success.
        """
        try:
            result = await self._rpc(
                "getTransaction",
                [
                    signature,
                    {
                        "encoding": "jsonParsed",
                        "maxSupportedTransactionVersion": 0,
                        "commitment": "confirmed",
                    },
                ],
            )
        except CryptoServiceError:
            return None

        if not result:
            return None

        meta = result.get("meta") or {}
        if meta.get("err"):
            return None  # transaction failed on-chain

        tx = result.get("transaction") or {}
        account_keys = (tx.get("message") or {}).get("accountKeys") or []

        if self.currency == "sol":
            return self._validate_sol_transfer(meta, account_keys)
        return self._validate_usdc_transfer(meta)

    def _validate_sol_transfer(
        self,
        meta: dict[str, Any],
        account_keys: list[Any],
    ) -> dict[str, Any] | None:
        """
        Look for a positive lamport delta at the merchant's native address.
        account_keys entries are either plain strings or {"pubkey": "...", ...}
        depending on the encoding.
        """
        pre: list[int] = meta.get("preBalances", [])
        post: list[int] = meta.get("postBalances", [])

        for i, key_entry in enumerate(account_keys):
            pubkey = (
                key_entry
                if isinstance(key_entry, str)
                else (key_entry.get("pubkey") or "")
            )
            if pubkey != self._merchant_pubkey_str:
                continue
            if i >= len(pre) or i >= len(post):
                continue
            delta = post[i] - pre[i]
            if delta > 0:
                return {"amount": Decimal(delta) / Decimal(10**9)}

        return None

    def _validate_usdc_transfer(
        self,
        meta: dict[str, Any],
    ) -> dict[str, Any] | None:
        """
        Look for a positive token balance delta owned by the merchant with the
        expected USDC mint.  preTokenBalances may omit accounts that didn't
        exist before (new ATAs), so we default pre-amount to 0.
        """
        pre_by_index: dict[int, int] = {
            b["accountIndex"]: int(b["uiTokenAmount"].get("amount", "0"))
            for b in (meta.get("preTokenBalances") or [])
        }

        for post_bal in meta.get("postTokenBalances") or []:
            owner = post_bal.get("owner", "")
            mint = post_bal.get("mint", "")
            if owner != self._merchant_pubkey_str or mint != self._usdc_mint_str:
                continue
            idx = post_bal["accountIndex"]
            post_raw = int(post_bal["uiTokenAmount"].get("amount", "0"))
            pre_raw = pre_by_index.get(idx, 0)
            delta = post_raw - pre_raw
            if delta > 0:
                return {"amount": Decimal(delta) / Decimal(10**6)}

        return None

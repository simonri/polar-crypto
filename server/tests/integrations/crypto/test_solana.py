from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from polar.integrations.crypto.solana import USDC_MINT_MAINNET, SolanaAdapter

MERCHANT_PUBKEY = "BfSYDaBxeiXoApEdxbyaceKVYJMMNULkqCXNHZvohk6E"


def _token_account(amount_raw: int) -> dict[str, object]:
    return {
        "account": {
            "data": {
                "parsed": {
                    "info": {"tokenAmount": {"amount": str(amount_raw)}},
                }
            }
        }
    }


@pytest.fixture
def adapter() -> SolanaAdapter:
    return SolanaAdapter(
        currency="sol_usdc",
        merchant_pubkey=MERCHANT_PUBKEY,
        rpc_url="https://api.mainnet-beta.solana.com",
        network="mainnet-beta",
    )


@pytest.mark.asyncio
class TestSolanaAdapterBalance:
    async def test_sums_every_token_account_for_the_mint(
        self, adapter: SolanaAdapter, mocker: MockerFixture
    ) -> None:
        """
        A wallet can hold the same mint in more than one token account (the
        canonical Associated Token Account is only the *default* address a
        compliant wallet creates, not the only one that can exist). Checking
        a single hardcoded address under-reports real balance when funds
        landed somewhere else, so balance() must sum across every token
        account owned by the merchant for this mint.
        """
        rpc = mocker.patch.object(
            adapter,
            "_rpc",
            AsyncMock(
                return_value={
                    "value": [
                        _token_account(4_375_151),  # 4.375151 USDC, non-ATA
                        _token_account(1_000_000),  # 1.000000 USDC, elsewhere
                    ]
                }
            ),
        )

        result = await adapter.balance()

        assert result["confirmed"] == Decimal("5.375151")
        rpc.assert_awaited_once_with(
            "getTokenAccountsByOwner",
            [
                MERCHANT_PUBKEY,
                {"mint": USDC_MINT_MAINNET},
                {"encoding": "jsonParsed", "commitment": "confirmed"},
            ],
        )

    async def test_no_token_accounts_is_zero_not_an_error(
        self, adapter: SolanaAdapter, mocker: MockerFixture
    ) -> None:
        mocker.patch.object(adapter, "_rpc", AsyncMock(return_value={"value": []}))

        result = await adapter.balance()

        assert result["confirmed"] == Decimal(0)

    async def test_sol_native_balance_unaffected(
        self, adapter: SolanaAdapter, mocker: MockerFixture
    ) -> None:
        sol_adapter = SolanaAdapter(
            currency="sol",
            merchant_pubkey=MERCHANT_PUBKEY,
            rpc_url="https://api.mainnet-beta.solana.com",
        )
        rpc = mocker.patch.object(
            sol_adapter, "_rpc", AsyncMock(return_value={"value": 58_394_074})
        )

        result = await sol_adapter.balance()

        assert result["confirmed"] == Decimal("0.058394074")
        rpc.assert_awaited_once_with(
            "getBalance", [MERCHANT_PUBKEY, {"commitment": "confirmed"}]
        )

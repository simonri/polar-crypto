"""
REST endpoints for crypto payout wallet management.

Mounted at: /v1/integrations/crypto/payout-wallets
"""

from __future__ import annotations

from typing import Annotated

import structlog
from fastapi import Depends
from pydantic import UUID4

from polar.auth.dependencies import Authenticator
from polar.auth.models import AuthSubject
from polar.auth.scope import Scope
from polar.exceptions import ResourceNotFound
from polar.logging import Logger
from polar.models import User
from polar.models.crypto_payout_wallet import CryptoPayoutWallet
from polar.postgres import AsyncSession, get_db_session
from polar.routing import APIRouter

from .payout_service import (
    crypto_payout_account_service,
)
from .schemas import (
    CryptoPayoutWalletCreate,
    CryptoPayoutWalletRead,
)

log: Logger = structlog.get_logger()

PayoutWalletWrite = Annotated[
    AuthSubject[User],
    Depends(
        Authenticator(
            required_scopes={Scope.user_read},
            allowed_subjects={User},
        )
    ),
]

router = APIRouter(
    prefix="/integrations/crypto",
    tags=["integrations_crypto"],
    include_in_schema=False,
)


@router.get("/payout-wallets", response_model=list[CryptoPayoutWalletRead])
async def list_payout_wallets(
    auth_subject: PayoutWalletWrite,
    payout_account_id: UUID4,
    session: AsyncSession = Depends(get_db_session),
) -> list[CryptoPayoutWallet]:
    return await crypto_payout_account_service.list_wallets(session, payout_account_id)


@router.post(
    "/payout-wallets",
    response_model=CryptoPayoutWalletRead,
    status_code=201,
)
async def add_payout_wallet(
    auth_subject: PayoutWalletWrite,
    create: CryptoPayoutWalletCreate,
    session: AsyncSession = Depends(get_db_session),
) -> CryptoPayoutWallet:
    from polar.payout_account.repository import PayoutAccountRepository

    repo = PayoutAccountRepository.from_session(session)
    account = await repo.get_by_id(create.payout_account_id)
    if account is None or account.admin_id != auth_subject.subject.id:
        raise ResourceNotFound()
    return await crypto_payout_account_service.add_wallet(
        session, account, create.currency, create.wallet_address
    )


@router.delete("/payout-wallets/{currency}", status_code=204)
async def remove_payout_wallet(
    auth_subject: PayoutWalletWrite,
    payout_account_id: UUID4,
    currency: str,
    session: AsyncSession = Depends(get_db_session),
) -> None:
    from polar.payout_account.repository import PayoutAccountRepository

    repo = PayoutAccountRepository.from_session(session)
    account = await repo.get_by_id(payout_account_id)
    if account is None or account.admin_id != auth_subject.subject.id:
        raise ResourceNotFound()
    await crypto_payout_account_service.remove_wallet(session, account, currency)

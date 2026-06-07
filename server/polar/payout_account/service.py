from __future__ import annotations

import uuid
from collections.abc import Sequence

import structlog

from polar.auth.models import AuthSubject
from polar.enums import PayoutAccountType
from polar.exceptions import PolarError
from polar.kit.db.postgres import AsyncReadSession
from polar.logging import Logger
from polar.models import Organization, PayoutAccount, User
from polar.organization.repository import OrganizationRepository
from polar.organization.resolver import get_payload_organization
from polar.payout.repository import PayoutRepository
from polar.postgres import AsyncSession

from .repository import PayoutAccountRepository
from .schemas import PayoutAccountCreate, PayoutAccountLink

log: Logger = structlog.get_logger()


class PayoutAccountServiceError(PolarError):
    pass


class PayoutAccountExternalIdDoesNotExist(PayoutAccountServiceError):
    def __init__(self, external_id: str) -> None:
        self.external_id = external_id
        message = f"Payout account with external ID {external_id} does not exist"
        super().__init__(message)


class PayoutAccountExternalLinkUnsupported(PayoutAccountServiceError):
    def __init__(self, account_type: PayoutAccountType) -> None:
        self.account_type = account_type
        message = f"Unsupported payout account type for external link: {account_type}"
        super().__init__(message, 404)


class PayoutAccountLinkedToOrganization(PayoutAccountServiceError):
    def __init__(self, payout_account_id: uuid.UUID) -> None:
        self.payout_account_id = payout_account_id
        message = (
            f"Payout account {payout_account_id} is still linked to one or more "
            "organizations. Please unlink it before deleting."
        )
        super().__init__(message, 422)


class PayoutAccountHasPendingPayouts(PayoutAccountServiceError):
    def __init__(self, payout_account_id: uuid.UUID) -> None:
        self.payout_account_id = payout_account_id
        message = (
            f"Payout account {payout_account_id} has pending payouts. "
            "Please wait for them to complete before deleting."
        )
        super().__init__(message, 422)


class PayoutAccountService:
    async def list(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User],
    ) -> Sequence[PayoutAccount]:
        repository = PayoutAccountRepository.from_session(session)
        statement = repository.get_statement_by_user(auth_subject.subject)
        return await repository.get_all(statement)

    async def get(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User],
        payout_account_id: uuid.UUID,
    ) -> PayoutAccount | None:
        repository = PayoutAccountRepository.from_session(session)
        statement = repository.get_statement_by_user(auth_subject.subject).where(
            PayoutAccount.id == payout_account_id
        )
        return await repository.get_one_or_none(statement)

    async def create(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        payout_account_create: PayoutAccountCreate,
    ) -> PayoutAccount:
        organization = await get_payload_organization(
            session, auth_subject, payout_account_create
        )

        payout_account = await self.create_manual_account(
            session,
            organization,
            auth_subject.subject,
            country=payout_account_create.country,
            currency="usd",
        )
        return payout_account

    async def onboarding_link(
        self, payout_account: PayoutAccount, return_path: str
    ) -> PayoutAccountLink:
        raise PayoutAccountExternalLinkUnsupported(payout_account.type)

    async def dashboard_link(self, payout_account: PayoutAccount) -> PayoutAccountLink:
        raise PayoutAccountExternalLinkUnsupported(payout_account.type)

    async def delete(
        self, session: AsyncSession, payout_account: PayoutAccount
    ) -> None:
        organization_repository = OrganizationRepository.from_session(session)
        linked_organizations = await organization_repository.get_all_by_payout_account(
            payout_account.id
        )
        if linked_organizations:
            raise PayoutAccountLinkedToOrganization(payout_account.id)

        payout_repository = PayoutRepository.from_session(session)
        pending_payouts_count = await payout_repository.count_pending_by_payout_account(
            payout_account.id
        )
        if pending_payouts_count > 0:
            raise PayoutAccountHasPendingPayouts(payout_account.id)

        repository = PayoutAccountRepository.from_session(session)
        await repository.soft_delete(payout_account)

    async def create_manual_account(
        self,
        session: AsyncSession,
        organization: Organization,
        admin: User,
        *,
        country: str,
        currency: str,
    ) -> PayoutAccount:
        repository = PayoutAccountRepository.from_session(session)
        payout_account = await repository.create(
            PayoutAccount(
                type=PayoutAccountType.manual,
                admin=admin,
                country=country,
                currency=currency,
                is_details_submitted=True,
                is_charges_enabled=True,
                is_payouts_enabled=True,
            )
        )

        organization_repository = OrganizationRepository.from_session(session)
        organization.payout_account = payout_account
        await organization_repository.update(organization)

        return payout_account


payout_account = PayoutAccountService()

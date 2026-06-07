import uuid

import structlog
from sqlalchemy import select

from polar.exceptions import PolarTaskError
from polar.integrations.plain.service import plain as plain_service
from polar.member.service import member_service
from polar.models import Customer, Organization
from polar.models.member import Member, MemberRole
from polar.postgres import AsyncSession
from polar.user.repository import UserRepository
from polar.worker import (
    AsyncSessionMaker,
    TaskPriority,
    actor,
)

from .repository import OrganizationRepository
from .service import organization as organization_service

log = structlog.get_logger()

_BACKFILL_BATCH_SIZE = 100


class OrganizationTaskError(PolarTaskError): ...


class OrganizationDoesNotExist(OrganizationTaskError):
    def __init__(self, organization_id: uuid.UUID) -> None:
        self.organization_id = organization_id
        message = f"The organization with id {organization_id} does not exist."
        super().__init__(message)


class AccountDoesNotExist(OrganizationTaskError):
    def __init__(self, account_id: uuid.UUID) -> None:
        self.account_id = account_id
        message = f"The account with id {account_id} does not exist."
        super().__init__(message)


class UserDoesNotExist(OrganizationTaskError):
    def __init__(self, user_id: uuid.UUID) -> None:
        self.user_id = user_id
        message = f"The user with id {user_id} does not exist."
        super().__init__(message)


@actor(actor_name="organization.created", priority=TaskPriority.LOW)
async def organization_created(organization_id: uuid.UUID) -> None:
    async with AsyncSessionMaker() as session:
        repository = OrganizationRepository.from_session(session)
        organization = await repository.get_by_id(organization_id)
        if organization is None:
            raise OrganizationDoesNotExist(organization_id)


def _check_threshold_debounce_key(account_id: uuid.UUID) -> str:
    return f"organization.check_threshold:{account_id}"


@actor(
    actor_name="organization.check_threshold",
    priority=TaskPriority.LOW,
    debounce_key=_check_threshold_debounce_key,
)
async def organization_check_threshold(account_id: uuid.UUID) -> None:
    """Refresh the cached ``total_balance`` for the organization."""
    async with AsyncSessionMaker() as session:
        repository = OrganizationRepository.from_session(session)
        organization = await repository.get_by_account(account_id)
        if organization is None:
            return
        await organization_service.update_total_balance(session, organization)


@actor(actor_name="organization.deletion_requested", priority=TaskPriority.HIGH)
async def organization_deletion_requested(
    organization_id: uuid.UUID,
    user_id: uuid.UUID,
    blocked_reasons: list[str],
) -> None:
    """Handle organization deletion request that requires support review."""
    async with AsyncSessionMaker() as session:
        repository = OrganizationRepository.from_session(session)
        organization = await repository.get_by_id(organization_id)
        if organization is None:
            raise OrganizationDoesNotExist(organization_id)

        user_repository = UserRepository.from_session(session)
        user = await user_repository.get_by_id(user_id)
        if user is None:
            raise UserDoesNotExist(user_id)

        # Create Plain ticket for support handling
        await plain_service.create_organization_deletion_thread(
            session, organization, user, blocked_reasons
        )


@actor(
    actor_name="organization.backfill_members",
    priority=TaskPriority.LOW,
    time_limit=600_000,  # 10 min timeout
    max_retries=0,
)
async def backfill_members(organization_id: uuid.UUID) -> None:
    """
    Backfill members when member_model_enabled is turned on for an organization.

    Two steps:
    A. Create owner members for all customers without one
    B. Migrate active (non-revoked) seats to member model format
    """
    # Validate organization and feature flag
    async with AsyncSessionMaker() as session:
        repository = OrganizationRepository.from_session(session)
        organization = await repository.get_by_id(organization_id)
        if organization is None:
            raise OrganizationDoesNotExist(organization_id)

        if not organization.feature_settings.get("member_model_enabled", False):
            log.warning(
                "organization.backfill_members.skipped",
                reason="member_model_not_enabled",
                organization_id=str(organization_id),
            )
            return

    log.info(
        "organization.backfill_members.start",
        organization_id=str(organization_id),
    )

    # Create owner members for all customers without one
    async with AsyncSessionMaker() as session:
        organization = await OrganizationRepository.from_session(session).get_by_id(
            organization_id
        )
        assert organization is not None
        owner_members_created = await _backfill_owner_members(session, organization)

    log.info(
        "organization.backfill_members.complete",
        organization_id=str(organization_id),
        owner_members_created=owner_members_created,
    )


async def _backfill_owner_members(
    session: AsyncSession,
    organization: Organization,
) -> int:
    """Step A: Create owner members for all customers that don't have one."""
    # Find customers without an owner member
    statement = (
        select(Customer)
        .outerjoin(
            Member,
            (Customer.id == Member.customer_id)
            & (Member.role == MemberRole.owner)
            & (Member.is_deleted.is_(False)),
        )
        .where(
            Customer.organization_id == organization.id,
            Customer.is_deleted.is_(False),
            Member.id.is_(None),
        )
    )
    results = await session.stream_scalars(
        statement,
        execution_options={"yield_per": _BACKFILL_BATCH_SIZE},
    )

    customers_found = 0
    count = 0
    try:
        async for customer in results:
            customers_found += 1
            member = await member_service.create_owner_member(
                session, customer, organization, send_webhook=False
            )
            if member is not None:
                if customer._oauth_accounts:
                    member._oauth_accounts = {**customer._oauth_accounts}
                count += 1
            if count > 0 and count % _BACKFILL_BATCH_SIZE == 0:
                await session.flush()
    finally:
        await results.close()

    await session.flush()

    log.info(
        "organization.backfill_members.step_a_complete",
        organization_id=str(organization.id),
        customers_found=customers_found,
        members_created=count,
    )
    return count



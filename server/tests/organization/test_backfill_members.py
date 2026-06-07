import uuid

import pytest
from sqlalchemy import select

from polar.kit.db.postgres import AsyncSession
from polar.models import Account, Customer, User
from polar.models.customer import (
    CustomerOAuthAccount,
    CustomerOAuthPlatform,
    CustomerType,
)
from polar.models.member import Member, MemberRole
from polar.organization.tasks import (
    OrganizationDoesNotExist,
    backfill_members,
)
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_account,
    create_customer,
    create_organization,
)


@pytest.mark.asyncio
class TestBackfillMembers:
    async def test_not_existing_organization(self, session: AsyncSession) -> None:
        session.expunge_all()
        with pytest.raises(OrganizationDoesNotExist):
            await backfill_members(uuid.uuid4())

    async def test_skips_when_flag_disabled(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        account: Account,
    ) -> None:
        organization = await create_organization(
            save_fixture, account, feature_settings={"member_model_enabled": False}
        )
        customer = await create_customer(
            save_fixture, organization=organization, email="c@test.com"
        )

        session.expunge_all()
        await backfill_members(organization.id)

        # No owner members should be created
        stmt = select(Member).where(
            Member.organization_id == organization.id,
            Member.role == MemberRole.owner,
        )
        result = await session.execute(stmt)
        assert len(result.scalars().all()) == 0

    async def test_creates_owner_members_for_all_customers(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        account: Account,
    ) -> None:
        organization = await create_organization(
            save_fixture, account, feature_settings={"member_model_enabled": True}
        )
        c1 = await create_customer(
            save_fixture,
            organization=organization,
            email="alice@test.com",
        )
        c2 = await create_customer(
            save_fixture,
            organization=organization,
            email="bob@test.com",
        )

        session.expunge_all()
        await backfill_members(organization.id)

        # Both customers should now have owner members
        stmt = select(Member).where(
            Member.organization_id == organization.id,
            Member.role == MemberRole.owner,
            Member.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        members = result.scalars().all()
        assert len(members) == 2

        member_customer_ids = {m.customer_id for m in members}
        assert c1.id in member_customer_ids
        assert c2.id in member_customer_ids

    async def test_skips_customers_with_existing_owner_member(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        account: Account,
    ) -> None:
        organization = await create_organization(
            save_fixture, account, feature_settings={"member_model_enabled": True}
        )
        customer = await create_customer(
            save_fixture, organization=organization, email="has-owner@test.com"
        )

        # Pre-create an owner member
        existing_member = Member(
            customer_id=customer.id,
            organization_id=organization.id,
            email=customer.email,
            role=MemberRole.owner,
        )
        await save_fixture(existing_member)

        session.expunge_all()
        await backfill_members(organization.id)

        # Should still be exactly 1 owner member, not 2
        stmt = select(Member).where(
            Member.customer_id == customer.id,
            Member.role == MemberRole.owner,
            Member.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        members = result.scalars().all()
        assert len(members) == 1
        assert members[0].id == existing_member.id

    async def test_idempotent_run_twice(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        account: Account,
    ) -> None:
        organization = await create_organization(
            save_fixture, account, feature_settings={"member_model_enabled": True}
        )
        await create_customer(
            save_fixture,
            organization=organization,
            email="idem@test.com",
        )

        session.expunge_all()
        await backfill_members(organization.id)
        await backfill_members(organization.id)

        # Should still be exactly 1 owner member
        stmt = select(Member).where(
            Member.organization_id == organization.id,
            Member.role == MemberRole.owner,
            Member.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        members = result.scalars().all()
        assert len(members) == 1

    async def test_does_not_affect_other_organizations(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        user: User,
    ) -> None:
        org1 = await create_organization(
            save_fixture,
            await create_account(save_fixture, user),
            feature_settings={"member_model_enabled": True},
            name_prefix="org1",
        )
        org2 = await create_organization(
            save_fixture,
            await create_account(save_fixture, user),
            feature_settings={"member_model_enabled": True},
            name_prefix="org2",
        )
        c1 = await create_customer(
            save_fixture,
            organization=org1,
            email="org1-customer@test.com",
        )
        c2 = await create_customer(
            save_fixture,
            organization=org2,
            email="org2-customer@test.com",
        )

        session.expunge_all()
        # Only backfill org1
        await backfill_members(org1.id)

        # org1 customer should have an owner member
        stmt = select(Member).where(
            Member.customer_id == c1.id,
            Member.role == MemberRole.owner,
            Member.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        assert len(result.scalars().all()) == 1

        # org2 customer should NOT have an owner member
        stmt = select(Member).where(
            Member.customer_id == c2.id,
            Member.role == MemberRole.owner,
            Member.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        assert len(result.scalars().all()) == 0

    async def test_copies_oauth_accounts_to_owner_member(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        account: Account,
    ) -> None:
        """Customer OAuth accounts should be copied to the owner member."""
        organization = await create_organization(
            save_fixture,
            account,
            feature_settings={
                "member_model_enabled": True,
                "seat_based_pricing_enabled": True,
            },
        )
        customer = await create_customer(
            save_fixture,
            organization=organization,
            email="oauth-owner@test.com",
        )

        # Set OAuth account on customer
        oauth_account = CustomerOAuthAccount(
            access_token="gh_token_123",
            account_id="12345",
            account_username="ghuser",
        )
        customer.set_oauth_account(oauth_account, CustomerOAuthPlatform.github)
        await save_fixture(customer)

        session.expunge_all()
        await backfill_members(organization.id)

        # Find the owner member
        stmt = select(Member).where(
            Member.customer_id == customer.id,
            Member.role == MemberRole.owner,
            Member.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        owner_member = result.scalar_one()

        # OAuth account should be copied to the owner member
        member_oauth = owner_member.get_oauth_account(
            "12345", CustomerOAuthPlatform.github
        )
        assert member_oauth is not None
        assert member_oauth.access_token == "gh_token_123"
        assert member_oauth.account_username == "ghuser"


@pytest.mark.asyncio
class TestBackfillMembersB2C:
    """B2C scenario: individual customers with direct (non-seat-based) subscriptions."""

    async def test_creates_owner_member_per_customer(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        account: Account,
    ) -> None:
        """Each B2C customer gets an owner member."""
        organization = await create_organization(
            save_fixture, account, feature_settings={"member_model_enabled": True}
        )
        c1 = await create_customer(
            save_fixture,
            organization=organization,
            email="alice@b2c.com",
        )
        c2 = await create_customer(
            save_fixture,
            organization=organization,
            email="bob@b2c.com",
        )

        session.expunge_all()
        await backfill_members(organization.id)

        for cid in [c1.id, c2.id]:
            stmt = select(Member).where(
                Member.customer_id == cid,
                Member.role == MemberRole.owner,
                Member.deleted_at.is_(None),
            )
            result = await session.execute(stmt)
            members = result.scalars().all()
            assert len(members) == 1

    async def test_customer_type_is_individual(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        account: Account,
    ) -> None:
        """B2C customers should have type=individual after backfill."""
        organization = await create_organization(
            save_fixture, account, feature_settings={"member_model_enabled": True}
        )
        customer = await create_customer(
            save_fixture,
            organization=organization,
            email="b2c-type@test.com",
        )

        session.expunge_all()
        await backfill_members(organization.id)

        refreshed = await session.get(Customer, customer.id)
        assert refreshed is not None
        assert refreshed.type == CustomerType.individual

    async def test_oauth_copied_to_owner_member(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        account: Account,
    ) -> None:
        """OAuth accounts on customer are copied to the owner member."""
        organization = await create_organization(
            save_fixture, account, feature_settings={"member_model_enabled": True}
        )
        customer = await create_customer(
            save_fixture,
            organization=organization,
            email="b2c-oauth@test.com",
        )
        oauth = CustomerOAuthAccount(
            access_token="gh_b2c_token",
            account_id="b2c_123",
            account_username="b2c_ghuser",
        )
        customer.set_oauth_account(oauth, CustomerOAuthPlatform.github)
        await save_fixture(customer)

        session.expunge_all()
        await backfill_members(organization.id)

        stmt = select(Member).where(
            Member.customer_id == customer.id,
            Member.role == MemberRole.owner,
            Member.deleted_at.is_(None),
        )
        result = await session.execute(stmt)
        owner = result.scalar_one()

        member_oauth = owner.get_oauth_account("b2c_123", CustomerOAuthPlatform.github)
        assert member_oauth is not None
        assert member_oauth.access_token == "gh_b2c_token"
        assert member_oauth.account_username == "b2c_ghuser"

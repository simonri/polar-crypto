import uuid
from unittest.mock import AsyncMock

import pytest
from pytest_mock import MockerFixture

from polar.auth.models import AuthSubject
from polar.enums import SubscriptionRecurringInterval
from polar.exceptions import PolarRequestValidationError
from polar.kit.currency import PresentmentCurrency
from polar.kit.pagination import PaginationParams
from polar.kit.trial import TrialInterval
from polar.models import (
    Organization,
    Product,
    User,
    UserOrganization,
)
from polar.models.product_price import (
    ProductPriceAmountType,
    ProductPriceFixed,
)
from polar.postgres import AsyncSession
from polar.product.guard import is_fixed_price, is_static_price
from polar.product.schemas import (
    ExistingProductPrice,
    ProductCreate,
    ProductCreateOneTime,
    ProductCreateRecurring,
    ProductPriceCustomCreate,
    ProductPriceFixedCreate,
    ProductPriceFreeCreate,
    ProductUpdate,
)
from polar.product.service import product as product_service
from polar.product.sorting import ProductSortProperty
from tests.fixtures.auth import AuthSubjectFixture
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import (
    create_checkout_link,
    create_product,
)


@pytest.fixture
def enqueue_job_mock(mocker: MockerFixture) -> AsyncMock:
    return mocker.patch("polar.product.service.enqueue_job")


@pytest.mark.asyncio
class TestList:
    @pytest.mark.auth
    async def test_user(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        products: list[Product],
        user: User,
    ) -> None:
        # then
        session.expunge_all()

        results, count = await product_service.list(
            session,
            auth_subject,
            pagination=PaginationParams(1, 10),
        )

        assert count == 0
        assert len(results) == 0

    @pytest.mark.auth
    async def test_user_organization(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        products: list[Product],
        user_organization: UserOrganization,
    ) -> None:
        # then
        session.expunge_all()

        results, count = await product_service.list(
            session,
            auth_subject,
            pagination=PaginationParams(1, 10),
        )

        assert count == 2
        assert len(results) == 2

    @pytest.mark.auth(AuthSubjectFixture(subject="organization"))
    async def test_organization(
        self,
        auth_subject: AuthSubject[Organization],
        session: AsyncSession,
        products: list[Product],
    ) -> None:
        # then
        session.expunge_all()

        results, count = await product_service.list(
            session, auth_subject, pagination=PaginationParams(1, 10)
        )

        assert count == 2
        assert len(results) == 2

    @pytest.mark.auth
    async def test_filter_is_recurring(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        save_fixture: SaveFixture,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        recurring_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=SubscriptionRecurringInterval.month,
        )
        one_time_product = await create_product(
            save_fixture, organization=organization, recurring_interval=None
        )

        # then
        session.expunge_all()

        results, count = await product_service.list(
            session,
            auth_subject,
            organization_id=[recurring_product.organization_id],
            is_recurring=True,
            pagination=PaginationParams(1, 10),
        )

        assert count == 1
        assert len(results) == 1
        assert results[0].id == recurring_product.id

        results, count = await product_service.list(
            session,
            auth_subject,
            organization_id=[recurring_product.organization_id],
            is_recurring=False,
            pagination=PaginationParams(1, 10),
        )

        assert count == 1
        assert len(results) == 1
        assert results[0].id == one_time_product.id

    @pytest.mark.auth
    async def test_filter_organization(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
        products: list[Product],
        product: Product,
        product_second: Product,
        user_organization: UserOrganization,
    ) -> None:
        # then
        session.expunge_all()

        results, count = await product_service.list(
            session,
            auth_subject,
            organization_id=[organization.id],
            pagination=PaginationParams(1, 10),
            sorting=[(ProductSortProperty.created_at, False)],
        )

        assert count == 2
        assert len(results) == 2
        assert results[0].id == product.id
        assert results[1].id == product_second.id

    @pytest.mark.auth
    async def test_filter_is_archived(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        save_fixture: SaveFixture,
        user: User,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        archived_product = await create_product(
            save_fixture,
            organization=organization,
            recurring_interval=None,
            is_archived=True,
        )

        # then
        session.expunge_all()

        results, count = await product_service.list(
            session,
            auth_subject,
            organization_id=[archived_product.organization_id],
            is_archived=False,
            pagination=PaginationParams(1, 10),
        )
        assert count == 0
        assert len(results) == 0
        results, count = await product_service.list(
            session,
            auth_subject,
            organization_id=[archived_product.organization_id],
            pagination=PaginationParams(1, 10),
        )
        assert count == 1
        assert len(results) == 1
        assert results[0].id == archived_product.id


@pytest.mark.asyncio
class TestGet:
    @pytest.mark.auth
    async def test_user(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        product: Product,
    ) -> None:
        # then
        session.expunge_all()

        retrieved_product = await product_service.get(session, auth_subject, product.id)
        assert retrieved_product is None

    @pytest.mark.auth
    async def test_user_organization(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        product: Product,
        user: User,
        user_organization: UserOrganization,
    ) -> None:
        # then
        session.expunge_all()

        not_existing_product = await product_service.get(
            session, auth_subject, uuid.uuid4()
        )
        assert not_existing_product is None

        accessible_product = await product_service.get(
            session, auth_subject, product.id
        )
        assert accessible_product is not None
        assert accessible_product.id == product.id

    @pytest.mark.auth(AuthSubjectFixture(subject="organization"))
    async def test_organization(
        self,
        auth_subject: AuthSubject[Organization],
        session: AsyncSession,
        product: Product,
    ) -> None:
        # then
        session.expunge_all()

        not_existing_product = await product_service.get(
            session, auth_subject, uuid.uuid4()
        )
        assert not_existing_product is None

        accessible_product = await product_service.get(
            session, auth_subject, product.id
        )
        assert accessible_product is not None
        assert accessible_product.id == product.id


@pytest.mark.asyncio
class TestCreate:
    @pytest.mark.auth
    async def test_user_not_existing_organization(
        self, auth_subject: AuthSubject[User], session: AsyncSession
    ) -> None:
        create_schema = ProductCreateRecurring(
            name="Product",
            organization_id=uuid.uuid4(),
            recurring_interval=SubscriptionRecurringInterval.month,
            prices=[
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=1000,
                    price_currency=PresentmentCurrency.usd,
                )
            ],
        )

        with pytest.raises(PolarRequestValidationError):
            await product_service.create(session, create_schema, auth_subject)

    @pytest.mark.auth
    async def test_user_not_writable_organization(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        create_schema = ProductCreateRecurring(
            name="Product",
            organization_id=organization.id,
            recurring_interval=SubscriptionRecurringInterval.month,
            prices=[
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=1000,
                    price_currency=PresentmentCurrency.usd,
                )
            ],
        )

        with pytest.raises(PolarRequestValidationError):
            await product_service.create(session, create_schema, auth_subject)

    @pytest.mark.auth
    async def test_user_valid_organization(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        create_schema = ProductCreateRecurring(
            name="Product",
            organization_id=organization.id,
            recurring_interval=SubscriptionRecurringInterval.month,
            prices=[
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=1000,
                    price_currency=PresentmentCurrency.usd,
                )
            ],
        )

        product = await product_service.create(session, create_schema, auth_subject)
        assert product.organization_id == organization.id

        assert len(product.prices) == 1
        price = product.prices[0]
        assert is_static_price(price)

    @pytest.mark.auth
    async def test_user_empty_description(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        create_schema = ProductCreateRecurring(
            name="Product",
            description="",
            organization_id=organization.id,
            recurring_interval=SubscriptionRecurringInterval.month,
            prices=[
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=1000,
                    price_currency=PresentmentCurrency.usd,
                )
            ],
        )

        product = await product_service.create(session, create_schema, auth_subject)
        assert product.description is None

    @pytest.mark.auth(AuthSubjectFixture(subject="organization"))
    async def test_organization_set_organization_id(
        self,
        auth_subject: AuthSubject[Organization],
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        create_schema = ProductCreateRecurring(
            name="Product",
            organization_id=organization.id,
            recurring_interval=SubscriptionRecurringInterval.month,
            prices=[
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=1000,
                    price_currency=PresentmentCurrency.usd,
                )
            ],
        )

        with pytest.raises(PolarRequestValidationError):
            await product_service.create(session, create_schema, auth_subject)

    @pytest.mark.auth(AuthSubjectFixture(subject="organization"))
    async def test_organization_valid(
        self,
        auth_subject: AuthSubject[Organization],
        session: AsyncSession,
        organization: Organization,
    ) -> None:
        create_schema = ProductCreateRecurring(
            name="Product",
            recurring_interval=SubscriptionRecurringInterval.month,
            prices=[
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=1000,
                    price_currency=PresentmentCurrency.usd,
                )
            ],
        )

        product = await product_service.create(session, create_schema, auth_subject)
        assert product.organization_id == organization.id

    @pytest.mark.parametrize(
        "create_schema",
        [
            ProductCreateOneTime(
                name="One-time fixed",
                prices=[
                    ProductPriceFixedCreate(
                        amount_type=ProductPriceAmountType.fixed,
                        price_amount=1000,
                        price_currency=PresentmentCurrency.usd,
                    )
                ],
            ),
            ProductCreateOneTime(
                name="One-time custom",
                prices=[
                    ProductPriceCustomCreate(
                        amount_type=ProductPriceAmountType.custom,
                        minimum_amount=1000,
                        maximum_amount=2000,
                        preset_amount=1500,
                        price_currency=PresentmentCurrency.usd,
                    ),
                ],
            ),
            ProductCreateOneTime(
                name="One-time free",
                prices=[
                    ProductPriceFreeCreate(
                        amount_type=ProductPriceAmountType.free,
                    ),
                ],
            ),
            ProductCreateRecurring(
                name="Recurring free",
                recurring_interval=SubscriptionRecurringInterval.month,
                prices=[
                    ProductPriceFreeCreate(
                        amount_type=ProductPriceAmountType.free,
                    )
                ],
            ),
        ],
    )
    @pytest.mark.auth
    async def test_valid_prices(
        self,
        create_schema: ProductCreate,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        create_schema.organization_id = organization.id
        product = await product_service.create(session, create_schema, auth_subject)
        assert product.organization_id == organization.id

        assert len(product.prices) == len(create_schema.prices)

    @pytest.mark.auth
    async def test_invalid_several_static_prices(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        with pytest.raises(PolarRequestValidationError):
            await product_service.create(
                session,
                ProductCreateOneTime(
                    name="Product",
                    prices=[
                        ProductPriceFixedCreate(
                            amount_type=ProductPriceAmountType.fixed,
                            price_amount=1000,
                            price_currency=PresentmentCurrency.usd,
                        ),
                        ProductPriceFixedCreate(
                            amount_type=ProductPriceAmountType.fixed,
                            price_amount=2000,
                            price_currency=PresentmentCurrency.usd,
                        ),
                    ],
                    organization_id=organization.id,
                ),
                auth_subject,
            )

    @pytest.mark.auth
    async def test_invalid_multiple_static_prices_same_currency(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        """Test that multiple static prices in the same currency are not allowed"""
        with pytest.raises(PolarRequestValidationError):
            await product_service.create(
                session,
                ProductCreateOneTime(
                    name="Product",
                    prices=[
                        ProductPriceFixedCreate(
                            amount_type=ProductPriceAmountType.fixed,
                            price_amount=1000,
                            price_currency=PresentmentCurrency.usd,
                        ),
                        ProductPriceFixedCreate(
                            amount_type=ProductPriceAmountType.fixed,
                            price_amount=2000,
                            price_currency=PresentmentCurrency.usd,
                        ),
                    ],
                    organization_id=organization.id,
                ),
                auth_subject,
            )

    @pytest.mark.auth
    async def test_invalid_different_price_sets_across_currencies(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        """Test that each currency must have the same set of prices"""
        with pytest.raises(PolarRequestValidationError):
            await product_service.create(
                session,
                ProductCreateRecurring(
                    name="Product",
                    recurring_interval=SubscriptionRecurringInterval.month,
                    prices=[
                        ProductPriceFixedCreate(
                            amount_type=ProductPriceAmountType.fixed,
                            price_amount=1000,
                            price_currency=PresentmentCurrency.usd,
                        ),
                        ProductPriceCustomCreate(
                            amount_type=ProductPriceAmountType.custom,
                            price_currency=PresentmentCurrency.usd,
                            minimum_amount=100,
                        ),
                        ProductPriceFixedCreate(
                            amount_type=ProductPriceAmountType.fixed,
                            price_amount=900,
                            price_currency=PresentmentCurrency.eur,
                        ),
                        # Missing custom price for EUR - should fail
                    ],
                    organization_id=organization.id,
                ),
                auth_subject,
            )

    @pytest.mark.auth
    async def test_missing_default_presentment_currency(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        """Test that the default presentment currency is included in the product prices"""
        with pytest.raises(PolarRequestValidationError):
            await product_service.create(
                session,
                ProductCreateRecurring(
                    name="Product",
                    recurring_interval=SubscriptionRecurringInterval.month,
                    prices=[
                        ProductPriceFixedCreate(
                            amount_type=ProductPriceAmountType.fixed,
                            price_amount=900,
                            price_currency=PresentmentCurrency.eur,
                        ),
                    ],
                    organization_id=organization.id,
                ),
                auth_subject,
            )

    @pytest.mark.auth
    async def test_valid_multi_currency_product(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        """Test that a product with multiple currencies is valid when each currency has the same price structure"""
        product = await product_service.create(
            session,
            ProductCreateRecurring(
                name="Product",
                recurring_interval=SubscriptionRecurringInterval.month,
                prices=[
                    # USD prices
                    ProductPriceFixedCreate(
                        amount_type=ProductPriceAmountType.fixed,
                        price_amount=1000,
                        price_currency=PresentmentCurrency.usd,
                    ),
                    ProductPriceCustomCreate(
                        amount_type=ProductPriceAmountType.custom,
                        price_currency=PresentmentCurrency.usd,
                        minimum_amount=100,
                    ),
                    # EUR prices (same structure as USD)
                    ProductPriceFixedCreate(
                        amount_type=ProductPriceAmountType.fixed,
                        price_amount=900,
                        price_currency=PresentmentCurrency.eur,
                    ),
                    ProductPriceCustomCreate(
                        amount_type=ProductPriceAmountType.custom,
                        price_currency=PresentmentCurrency.eur,
                        minimum_amount=100,
                    ),
                    # GBP prices (same structure as USD and EUR)
                    ProductPriceFixedCreate(
                        amount_type=ProductPriceAmountType.fixed,
                        price_amount=800,
                        price_currency=PresentmentCurrency.gbp,
                    ),
                    ProductPriceCustomCreate(
                        amount_type=ProductPriceAmountType.custom,
                        price_currency=PresentmentCurrency.gbp,
                        minimum_amount=100,
                    ),
                ],
                organization_id=organization.id,
            ),
            auth_subject,
        )

        assert product.organization_id == organization.id
        assert len(product.prices) == 6  # 2 price types × 3 currencies

        # Verify each currency has both fixed and custom prices
        for currency in ["usd", "eur", "gbp"]:
            currency_prices = [
                p for p in product.prices if p.price_currency == currency
            ]
            assert len(currency_prices) == 2

            fixed_prices = [p for p in currency_prices if is_fixed_price(p)]
            custom_prices = [
                p
                for p in currency_prices
                if p.amount_type == ProductPriceAmountType.custom
            ]

            assert len(fixed_prices) == 1
            assert len(custom_prices) == 1


@pytest.mark.asyncio
class TestUpdate:
    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_no_price(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        product: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(prices=[])
        with pytest.raises(PolarRequestValidationError):
            await product_service.update(
                session,
                product,
                update_schema,
                auth_subject,
            )

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_valid_name_change(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product: Product,
        organization: Organization,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(name="Product Update")
        updated_product = await product_service.update(
            session,
            product,
            update_schema,
            auth_subject,
        )
        assert updated_product.name == "Product Update"

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_valid_description_change(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(description="Description update")
        updated_product = await product_service.update(
            session,
            product,
            update_schema,
            auth_subject,
        )
        assert updated_product.description == "Description update"

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_empty_description_update(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(description="")
        updated_product = await product_service.update(
            session,
            product,
            update_schema,
            auth_subject,
        )
        assert updated_product.description == product.description

        assert len(updated_product.prices) == 1
        assert len(updated_product.all_prices) == 1

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_valid_price_kept(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(
            prices=[
                ExistingProductPrice(id=product.prices[0].id),
            ]
        )
        updated_product = await product_service.update(
            session,
            product,
            update_schema,
            auth_subject,
        )

        assert len(updated_product.prices) == 1
        assert updated_product.prices[0].id == product.prices[0].id

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_valid_price_replaced(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(
            prices=[
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=12000,
                    price_currency=PresentmentCurrency.usd,
                ),
            ]
        )
        deleted_price = product.prices[0]
        assert is_static_price(deleted_price)

        updated_product = await product_service.update(
            session,
            product,
            update_schema,
            auth_subject,
        )

        await session.flush()

        assert len(updated_product.prices) == 1

        new_price = updated_product.prices[0]
        assert isinstance(new_price, ProductPriceFixed)
        assert new_price.price_amount == 12000

        assert len(updated_product.all_prices) == 2
        assert deleted_price in updated_product.all_prices

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_valid_archive(
        self,
        save_fixture: SaveFixture,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product: Product,
        product_second: Product,
        user_organization: UserOrganization,
    ) -> None:
        checkout_link_one_product = await create_checkout_link(
            save_fixture, products=[product]
        )
        checkout_link_two_products = await create_checkout_link(
            save_fixture, products=[product, product_second]
        )

        update_schema = ProductUpdate(is_archived=True)
        updated_product = await product_service.update(
            session,
            product,
            update_schema,
            auth_subject,
        )

        assert updated_product.is_archived

        # Ensure we remove archived product from related checkout links
        await session.refresh(
            checkout_link_one_product, {"deleted_at", "checkout_link_products"}
        )
        assert checkout_link_one_product.deleted_at is not None
        assert checkout_link_one_product.checkout_link_products == []

        await session.refresh(
            checkout_link_two_products, {"deleted_at", "checkout_link_products"}
        )
        assert checkout_link_two_products.deleted_at is None
        assert len(checkout_link_two_products.checkout_link_products) == 1

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_valid_unarchive(
        self,
        session: AsyncSession,
        save_fixture: SaveFixture,
        auth_subject: AuthSubject[User | Organization],
        product: Product,
        user_organization: UserOrganization,
    ) -> None:
        product.is_archived = True
        await save_fixture(product)

        update_schema = ProductUpdate(is_archived=False)
        updated_product = await product_service.update(
            session,
            product,
            update_schema,
            auth_subject,
        )

        assert not updated_product.is_archived

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_invalid_change_recurring_interval_on_non_legacy_product(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(
            recurring_interval=SubscriptionRecurringInterval.year
        )

        with pytest.raises(PolarRequestValidationError):
            await product_service.update(
                session,
                product,
                update_schema,
                auth_subject,
            )

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_invalid_legacy_product_price_with_new_price(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product_recurring_monthly_and_yearly: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(
            prices=[
                ExistingProductPrice(
                    id=product_recurring_monthly_and_yearly.prices[0].id
                ),
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=12000,
                    price_currency=PresentmentCurrency.usd,
                ),
            ]
        )

        with pytest.raises(PolarRequestValidationError):
            await product_service.update(
                session,
                product_recurring_monthly_and_yearly,
                update_schema,
                auth_subject,
            )

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_valid_legacy_product_price_kept(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product_recurring_monthly_and_yearly: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(
            prices=[
                ExistingProductPrice(
                    id=product_recurring_monthly_and_yearly.prices[0].id
                ),
                ExistingProductPrice(
                    id=product_recurring_monthly_and_yearly.prices[1].id
                ),
            ]
        )
        updated_product = await product_service.update(
            session,
            product_recurring_monthly_and_yearly,
            update_schema,
            auth_subject,
        )

        assert len(updated_product.prices) == 2
        assert (
            updated_product.prices[0].id
            == product_recurring_monthly_and_yearly.prices[0].id
        )
        assert (
            updated_product.prices[1].id
            == product_recurring_monthly_and_yearly.prices[1].id
        )

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_valid_legacy_product_price_replaced(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product_recurring_monthly_and_yearly: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(
            recurring_interval=SubscriptionRecurringInterval.month,
            prices=[
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=12000,
                    price_currency=PresentmentCurrency.usd,
                ),
            ],
        )
        updated_product = await product_service.update(
            session,
            product_recurring_monthly_and_yearly,
            update_schema,
            auth_subject,
        )

        assert len(updated_product.prices) == 1

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_invalid_several_static_prices(
        self,
        auth_subject: AuthSubject[User],
        session: AsyncSession,
        product: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(
            prices=[
                ExistingProductPrice(id=product.prices[0].id),
                ProductPriceFixedCreate(
                    amount_type=ProductPriceAmountType.fixed,
                    price_amount=2000,
                    price_currency=PresentmentCurrency.usd,
                ),
            ]
        )
        with pytest.raises(PolarRequestValidationError):
            await product_service.update(
                session,
                product,
                update_schema,
                auth_subject,
            )

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_invalid_trial_configuration_on_non_recurring(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product_one_time: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(
            trial_interval=TrialInterval.month, trial_interval_count=1
        )

        with pytest.raises(PolarRequestValidationError):
            await product_service.update(
                session,
                product_one_time,
                update_schema,
                auth_subject,
            )

    @pytest.mark.auth(
        AuthSubjectFixture(subject="user"),
        AuthSubjectFixture(subject="organization"),
    )
    async def test_valid_unset_trial_configuration(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product_recurring_trial: Product,
        user_organization: UserOrganization,
    ) -> None:
        update_schema = ProductUpdate(trial_interval=None, trial_interval_count=None)

        product = await product_service.update(
            session,
            product_recurring_trial,
            update_schema,
            auth_subject,
        )

        assert product.trial_interval is None
        assert product.trial_interval_count is None

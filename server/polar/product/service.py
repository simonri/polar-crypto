import builtins
import uuid
from collections import defaultdict
from collections.abc import Sequence
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from polar.auth.models import AuthSubject
from polar.auth.permission import OrganizationPermission
from polar.authz.service import (
    assert_organization_permission,
    assert_resource_permission,
    get_accessible_org_ids,
)
from polar.checkout_link.repository import CheckoutLinkRepository
from polar.custom_field.service import custom_field as custom_field_service
from polar.enums import SubscriptionRecurringInterval
from polar.exceptions import (
    PolarRequestValidationError,
    ValidationError,
)
from polar.kit.db.postgres import AsyncReadSession, AsyncSession
from polar.kit.metadata import MetadataQuery, apply_metadata_clause
from polar.kit.pagination import PaginationParams
from polar.kit.sorting import Sorting
from polar.models import (
    Organization,
    Product,
    ProductPrice,
    ProductVisibility,
    User,
)
from polar.models.product_custom_field import ProductCustomField
from polar.models.product_price import ProductPriceSource
from polar.models.webhook_endpoint import WebhookEventType
from polar.organization.repository import OrganizationRepository
from polar.organization.resolver import get_payload_organization
from polar.product.guard import (
    is_legacy_price,
    is_static_price,
)
from polar.product.repository import ProductRepository
from polar.webhook.service import webhook as webhook_service

from .schemas import (
    ExistingProductPrice,
    ProductCreate,
    ProductPriceCreate,
    ProductUpdate,
)
from .sorting import ProductSortProperty


class ProductService:
    async def list(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User | Organization],
        *,
        id: Sequence[uuid.UUID] | None = None,
        organization_id: Sequence[uuid.UUID] | None = None,
        query: str | None = None,
        is_archived: bool | None = None,
        is_recurring: bool | None = None,
        visibility: Sequence[ProductVisibility] | None = None,
        metadata: MetadataQuery | None = None,
        pagination: PaginationParams,
        sorting: list[Sorting[ProductSortProperty]] = [
            (ProductSortProperty.created_at, True)
        ],
    ) -> tuple[Sequence[Product], int]:
        repository = ProductRepository.from_session(session)
        org_ids = await get_accessible_org_ids(
            session, auth_subject, permission=OrganizationPermission.products_read
        )
        statement = repository.get_statement_by_org_ids(org_ids).join(
            ProductPrice,
            onclause=(
                ProductPrice.id
                == select(ProductPrice)
                .correlate(Product)
                .with_only_columns(ProductPrice.id)
                .where(
                    ProductPrice.product_id == Product.id,
                    ProductPrice.is_archived.is_(False),
                    ProductPrice.is_deleted.is_(False),
                )
                .order_by(ProductPrice.created_at.asc())
                .limit(1)
                .scalar_subquery()
            ),
            isouter=True,
        )

        if id is not None:
            statement = statement.where(Product.id.in_(id))

        if organization_id is not None:
            statement = statement.where(Product.organization_id.in_(organization_id))

        if query is not None:
            statement = statement.where(Product.name.ilike(f"%{query}%"))

        if is_archived is not None:
            statement = statement.where(Product.is_archived.is_(is_archived))

        if is_recurring is not None:
            statement = statement.where(Product.is_recurring.is_(is_recurring))

        if visibility is not None:
            statement = statement.where(Product.visibility.in_(visibility))

        if metadata is not None:
            statement = apply_metadata_clause(Product, statement, metadata)

        statement = repository.apply_sorting(statement, sorting)

        statement = statement.options(
            selectinload(Product.attached_custom_fields),
        )

        return await repository.paginate(
            statement, limit=pagination.limit, page=pagination.page
        )

    async def get(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User | Organization],
        id: uuid.UUID,
    ) -> Product | None:
        repository = ProductRepository.from_session(session)
        org_ids = await get_accessible_org_ids(
            session, auth_subject, permission=OrganizationPermission.products_read
        )
        statement = (
            repository.get_statement_by_org_ids(org_ids)
            .where(Product.id == id)
            .options(*repository.get_eager_options())
        )
        return await repository.get_one_or_none(statement)

    async def create(
        self,
        session: AsyncSession,
        create_schema: ProductCreate,
        auth_subject: AuthSubject[User | Organization],
    ) -> Product:
        repository = ProductRepository.from_session(session)
        organization = await get_payload_organization(
            session, auth_subject, create_schema
        )
        await assert_organization_permission(
            session,
            auth_subject,
            organization.id,
            OrganizationPermission.products_manage,
        )

        errors: list[ValidationError] = []
        prices, _, _, prices_errors = await self.get_validated_prices(
            session,
            organization,
            create_schema.prices,
            create_schema.recurring_interval,
            None,
            auth_subject,
        )
        errors.extend(prices_errors)

        product = await repository.create(
            Product(
                organization=organization,
                prices=prices,
                all_prices=prices,
                attached_custom_fields=[],
                **create_schema.model_dump(
                    exclude={
                        "organization_id",
                        "prices",
                        "attached_custom_fields",
                    },
                    by_alias=True,
                ),
            ),
            flush=True,
        )
        assert product.id is not None

        for order, attached_custom_field in enumerate(
            create_schema.attached_custom_fields
        ):
            custom_field = await custom_field_service.get_by_organization_and_id(
                session,
                attached_custom_field.custom_field_id,
                organization.id,
            )
            if custom_field is None:
                errors.append(
                    {
                        "type": "value_error",
                        "loc": ("body", "attached_custom_fields", order),
                        "msg": "Custom field does not exist.",
                        "input": attached_custom_field.custom_field_id,
                    }
                )
            product.attached_custom_fields.append(
                ProductCustomField(
                    custom_field=custom_field,
                    order=order,
                    required=attached_custom_field.required,
                )
            )

        if errors:
            raise PolarRequestValidationError(errors)

        await session.flush()

        await self._after_product_created(session, auth_subject, product)

        return product

    async def update(
        self,
        session: AsyncSession,
        product: Product,
        update_schema: ProductUpdate,
        auth_subject: AuthSubject[User | Organization],
    ) -> Product:
        await assert_resource_permission(
            session, auth_subject, product, OrganizationPermission.products_manage
        )

        errors: list[ValidationError] = []

        # Validate prices
        existing_prices = set(product.prices)
        added_prices: list[ProductPrice] = []
        if update_schema.prices is not None:
            (
                _,
                existing_prices,
                added_prices,
                prices_errors,
            ) = await self.get_validated_prices(
                session,
                product.organization,
                update_schema.prices,
                product.recurring_interval,
                product,
                auth_subject,
            )
            errors.extend(prices_errors)

        # Prevent non-legacy products from changing their recurring interval
        if (
            update_schema.recurring_interval is not None
            and (
                update_schema.recurring_interval != product.recurring_interval
                or update_schema.recurring_interval_count
                != product.recurring_interval_count
            )
            and not all(is_legacy_price(price) for price in product.prices)
        ):
            errors.append(
                {
                    "type": "value_error",
                    "loc": ("body", "recurring_interval"),
                    "msg": "Recurring interval cannot be changed.",
                    "input": update_schema.recurring_interval,
                }
            )

        # Prevent trying to add trial configuration to non-recurring products
        if (
            update_schema.trial_interval is not None
            or update_schema.trial_interval_count is not None
        ) and product.recurring_interval is None:
            errors.extend(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "trial_interval"),
                        "msg": "Trial configuration is only supported on recurring products.",
                        "input": update_schema.trial_interval,
                    },
                    {
                        "type": "value_error",
                        "loc": ("body", "trial_interval_count"),
                        "msg": "Trial configuration is only supported on recurring products.",
                        "input": update_schema.trial_interval_count,
                    },
                ]
            )

        if update_schema.attached_custom_fields is not None:
            attached_custom_fields_errors: list[ValidationError] = []
            nested = await session.begin_nested()
            product.attached_custom_fields = []
            await session.flush()

            for order, attached_custom_field in enumerate(
                update_schema.attached_custom_fields
            ):
                custom_field = await custom_field_service.get_by_organization_and_id(
                    session,
                    attached_custom_field.custom_field_id,
                    product.organization_id,
                )
                if custom_field is None:
                    attached_custom_fields_errors.append(
                        {
                            "type": "value_error",
                            "loc": ("body", "attached_custom_fields", order),
                            "msg": "Custom field does not exist.",
                            "input": attached_custom_field.custom_field_id,
                        }
                    )
                    continue
                product.attached_custom_fields.append(
                    ProductCustomField(
                        custom_field=custom_field,
                        order=order,
                        required=attached_custom_field.required,
                    )
                )

            if attached_custom_fields_errors:
                await nested.rollback()
                errors.extend(attached_custom_fields_errors)

        if errors:
            raise PolarRequestValidationError(errors)

        if product.is_archived and update_schema.is_archived is False:
            product = await self._unarchive(product)

        if update_schema.name is not None and update_schema.name != product.name:
            product.name = update_schema.name
        if (
            update_schema.description is not None
            and update_schema.description != product.description
        ):
            product.description = update_schema.description

        if update_schema.recurring_interval is not None:
            product.recurring_interval = update_schema.recurring_interval

        deleted_prices = set(product.prices) - existing_prices
        for deleted_price in deleted_prices:
            deleted_price.is_archived = True

        if update_schema.is_archived:
            product = await self._archive(session, product)

        for attr, value in update_schema.model_dump(
            exclude_unset=True,
            exclude={"prices", "attached_custom_fields"},
            by_alias=True,
        ).items():
            setattr(product, attr, value)

        session.add(product)
        await session.flush()

        await session.refresh(product, {"prices", "all_prices"})

        await self._after_product_updated(session, product)

        return product

    async def get_validated_prices(
        self,
        session: AsyncSession,
        organization: Organization,
        prices_schema: Sequence[ExistingProductPrice | ProductPriceCreate],
        recurring_interval: SubscriptionRecurringInterval | None,
        product: Product | None,
        auth_subject: AuthSubject[User | Organization],
        source: ProductPriceSource = ProductPriceSource.catalog,
        error_prefix: tuple[str, ...] = ("body", "prices"),
    ) -> tuple[
        builtins.list[ProductPrice],
        builtins.set[ProductPrice],
        builtins.list[ProductPrice],
        builtins.list[ValidationError],
    ]:
        prices: list[ProductPrice] = []
        prices_per_currency = defaultdict[str, list[tuple[ProductPrice, int]]](list)
        existing_prices: set[ProductPrice] = set()
        added_prices: list[ProductPrice] = []
        errors: list[ValidationError] = []

        for index, price_schema in enumerate(prices_schema):
            if isinstance(price_schema, ExistingProductPrice):
                assert product is not None
                price = product.get_price(price_schema.id)
                if price is None:
                    errors.append(
                        {
                            "type": "value_error",
                            "loc": (*error_prefix, index),
                            "msg": "Price does not exist.",
                            "input": price_schema.id,
                        }
                    )
                    continue
                existing_prices.add(price)
            else:
                model_class = price_schema.get_model_class()
                price = model_class(
                    product=product, source=source, **price_schema.model_dump()
                )
                added_prices.append(price)
            prices.append(price)
            prices_per_currency[price.price_currency].append((price, index))

        if len(prices) < 1:
            errors.append(
                {
                    "type": "too_short",
                    "loc": error_prefix,
                    "msg": "At least one price is required.",
                    "input": prices_schema,
                }
            )

        # Track price structure per currency for cross-currency validation
        price_structure_per_currency: dict[str, int] = {}

        for currency, currency_prices in prices_per_currency.items():
            # Check that only one static price exists per currency
            static_prices = [p for p, _ in currency_prices if is_static_price(p)]
            if len(static_prices) > 1:
                # Bypass that rule for legacy recurring products
                if not all(is_legacy_price(p) for p in static_prices):
                    errors.append(
                        {
                            "type": "value_error",
                            "loc": error_prefix,
                            "msg": "Only one static price is allowed.",
                            "input": prices_schema,
                        }
                    )

            price_structure_per_currency[currency] = len(static_prices)

        # Check that all currencies have the same price structure
        unique_structures = set(price_structure_per_currency.values())
        if len(unique_structures) > 1:
            errors.append(
                {
                    "type": "value_error",
                    "loc": error_prefix,
                    "msg": (
                        "All price currencies must define the same set of price types."
                    ),
                    "input": prices_schema,
                }
            )

        # Check that the default presentment currency is present
        if (
            organization.default_presentment_currency
            not in price_structure_per_currency
        ):
            errors.append(
                {
                    "type": "value_error",
                    "loc": error_prefix,
                    "msg": "The organization's default presentment currency must be present in the prices.",
                    "input": prices_schema,
                }
            )

        return prices, existing_prices, added_prices, errors

    async def _archive(self, session: AsyncSession, product: Product) -> Product:
        product.is_archived = True

        checkout_link_repository = CheckoutLinkRepository.from_session(session)
        await checkout_link_repository.archive_product(product.id)

        return product

    async def _unarchive(self, product: Product) -> Product:
        product.is_archived = False
        return product

    async def _after_product_created(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product: Product,
    ) -> None:
        await self._send_webhook(session, product, WebhookEventType.product_created)

    async def _after_product_updated(
        self, session: AsyncSession, product: Product
    ) -> None:
        await self._send_webhook(session, product, WebhookEventType.product_updated)

    async def _send_webhook(
        self,
        session: AsyncSession,
        product: Product,
        event_type: Literal[
            WebhookEventType.product_created, WebhookEventType.product_updated
        ],
    ) -> None:
        organization_repository = OrganizationRepository.from_session(session)
        organization = await organization_repository.get_by_id(product.organization_id)
        if organization is not None:
            await webhook_service.send(session, organization, event_type, product)


product = ProductService()

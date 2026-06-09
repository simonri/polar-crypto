import contextlib
import typing
import uuid
from collections.abc import AsyncIterator, Sequence

import structlog
from pydantic import UUID4
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import contains_eager, joinedload, selectinload

from polar.auth.models import Anonymous, AuthSubject
from polar.auth.permission import OrganizationPermission
from polar.authz.service import assert_resource_permission, get_accessible_org_ids
from polar.checkout.guard import has_product_checkout
from polar.checkout.schemas import (
    CheckoutConfirm,
    CheckoutCreate,
    CheckoutPriceCreate,
    CheckoutProductCreate,
    CheckoutUpdate,
    CheckoutUpdatePublic,
)
from polar.config import settings
from polar.custom_field.data import validate_custom_field_data
from polar.customer.repository import CustomerRepository
from polar.discount.service import DiscountNotRedeemableError
from polar.discount.service import discount as discount_service
from polar.enums import PaymentProcessor
from polar.event.service import event as event_service
from polar.event.system import (
    CheckoutCreatedMetadata,
    SystemEvent,
    build_checkout_event,
)
from polar.exceptions import (
    NotPermitted,
    PaymentNotReady,
    PolarError,
    PolarRequestValidationError,
    ResourceNotFound,
    ValidationError,
)
from polar.kit.crypto import generate_token
from polar.kit.db.locking import is_lock_not_available_error
from polar.kit.operator import attrgetter
from polar.kit.pagination import PaginationParams
from polar.kit.sorting import Sorting
from polar.kit.utils import utc_now
from polar.kit.visibility import Visibility
from polar.logging import Logger
from polar.member.repository import MemberRepository
from polar.member.service import member_service
from polar.models import (
    Checkout,
    CheckoutLink,
    Customer,
    Discount,
    Order,
    Organization,
    Payment,
    PaymentMethod,
    Product,
    ProductPrice,
    Subscription,
    User,
)
from polar.models.checkout import CheckoutStatus
from polar.models.checkout_product import CheckoutProduct
from polar.models.customer import CustomerType
from polar.models.order import OrderBillingReasonInternal
from polar.models.product_price import ProductPriceSource
from polar.models.webhook_endpoint import WebhookEventType
from polar.observability.checkout_metrics import (
    CHECKOUT_CREATED_TOTAL,
    CHECKOUT_SUCCEEDED_TOTAL,
)
from polar.order.service import order as order_service
from polar.postgres import AsyncReadSession, AsyncSession
from polar.product.custom_price import validate_custom_price_amount
from polar.product.guard import (
    CustomPrice,
    is_custom_price,
    is_discount_applicable,
    is_fixed_price,
)
from polar.product.price_set import (
    NoPricesForCurrencies,
    PriceSet,
)
from polar.product.repository import ProductPriceRepository, ProductRepository
from polar.product.schemas import ProductPriceCreateList
from polar.product.service import product as product_service
from polar.subscription.repository import SubscriptionRepository
from polar.subscription.service import subscription as subscription_service
from polar.trial_redemption.service import trial_redemption as trial_redemption_service
from polar.webhook.service import webhook as webhook_service
from polar.worker import enqueue_job

from .eventstream import CheckoutEvent, publish_checkout_event
from .repository import CheckoutRepository
from .sorting import CheckoutSortProperty

log: Logger = structlog.get_logger()


class CheckoutError(PolarError): ...


class ExpiredCheckoutError(CheckoutError):
    def __init__(self) -> None:
        message = "This checkout session has expired."
        super().__init__(message, 410)


class AlreadyActiveSubscriptionError(CheckoutError):
    def __init__(self) -> None:
        message = "You already have an active subscription."
        super().__init__(message, 403)


class PaymentError(CheckoutError):
    def __init__(
        self, checkout: Checkout, error_type: str | None, error: str | None
    ) -> None:
        self.checkout = checkout
        self.error_type = error_type
        self.error = error
        message = (
            f"The payment failed{f': {error}' if error else '.'} "
            "Please try again with a different payment method."
        )
        super().__init__(message, 400)


class CheckoutDoesNotExist(CheckoutError):
    def __init__(self, checkout_id: uuid.UUID) -> None:
        self.checkout_id = checkout_id
        message = f"Checkout {checkout_id} does not exist."
        super().__init__(message)


class NotOpenCheckout(CheckoutError):
    def __init__(self, checkout: Checkout) -> None:
        self.checkout = checkout
        self.status = checkout.status
        message = f"Checkout {checkout.id} is not open: {checkout.status}"
        super().__init__(message, 403)


class NotConfirmedCheckout(CheckoutError):
    def __init__(self, checkout: Checkout) -> None:
        self.checkout = checkout
        self.status = checkout.status
        message = f"Checkout {checkout.id} is not confirmed: {checkout.status}"
        super().__init__(message)


class ArchivedPriceCheckout(CheckoutError):
    def __init__(self, checkout: Checkout) -> None:
        self.checkout = checkout
        self.price = checkout.product_price
        message = (
            f"Checkout {checkout.id} has an archived price: {checkout.product_price_id}"
        )
        super().__init__(message)


class NoPaymentMethodOnIntent(CheckoutError):
    def __init__(self, checkout: Checkout, intent_id: str) -> None:
        self.checkout = checkout
        self.intent_id = intent_id
        message = (
            f"Intent {intent_id} for {checkout.id} has no payment method associated."
        )
        super().__init__(message)


class TrialAlreadyRedeemed(CheckoutError):
    def __init__(self, checkout: Checkout) -> None:
        self.checkout = checkout
        message = (
            "You have already used a trial for this product. "
            "Trials can only be used once per customer."
        )
        super().__init__(message, 403)


class CheckoutLocked(CheckoutError):
    """Raised when checkout is locked by another transaction."""

    def __init__(self, checkout_id: uuid.UUID) -> None:
        self.checkout_id = checkout_id
        message = "Checkout is currently being processed. Please try again."
        super().__init__(message, 409)


class CheckoutCustomerDeleted(CheckoutError):
    def __init__(self, checkout: Checkout) -> None:
        self.checkout = checkout
        message = "The customer associated with this checkout has been deleted."
        super().__init__(message, 409)


class CheckoutCustomerExternalIdMismatch(CheckoutError):
    def __init__(self) -> None:
        message = (
            "A customer with this external ID already exists "
            "but with a different email address."
        )
        super().__init__(message, 422)


CHECKOUT_CLIENT_SECRET_PREFIX = "polar_c_"


class CheckoutService:
    async def list(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User | Organization],
        *,
        organization_id: Sequence[uuid.UUID] | None = None,
        product_id: Sequence[uuid.UUID] | None = None,
        customer_id: Sequence[uuid.UUID] | None = None,
        external_customer_id: Sequence[str] | None = None,
        status: Sequence[CheckoutStatus] | None = None,
        query: str | None = None,
        pagination: PaginationParams,
        sorting: list[Sorting[CheckoutSortProperty]] = [
            (CheckoutSortProperty.created_at, True)
        ],
    ) -> tuple[Sequence[Checkout], int]:
        repository = CheckoutRepository.from_session(session)
        org_ids = await get_accessible_org_ids(
            session, auth_subject, permission=OrganizationPermission.sales_read
        )
        statement = repository.get_statement_by_org_ids(org_ids).options(
            *repository.get_eager_options()
        )

        if organization_id is not None:
            statement = statement.where(Checkout.organization_id.in_(organization_id))

        if product_id is not None:
            statement = statement.where(Checkout.product_id.in_(product_id))

        if customer_id is not None:
            statement = statement.where(Checkout.customer_id.in_(customer_id))

        if external_customer_id is not None:
            statement = statement.join(Customer).where(
                Customer.external_id.in_(external_customer_id)
            )

        if status is not None:
            statement = statement.where(Checkout.status.in_(status))

        if query is not None:
            statement = statement.where(Checkout.customer_email.ilike(f"%{query}%"))

        statement = repository.apply_sorting(statement, sorting)

        return await repository.paginate(
            statement, limit=pagination.limit, page=pagination.page
        )

    async def get_by_id(
        self,
        session: AsyncReadSession,
        auth_subject: AuthSubject[User | Organization],
        id: uuid.UUID,
    ) -> Checkout | None:
        repository = CheckoutRepository.from_session(session)
        org_ids = await get_accessible_org_ids(
            session, auth_subject, permission=OrganizationPermission.sales_read
        )
        statement = (
            repository.get_statement_by_org_ids(org_ids)
            .where(Checkout.id == id)
            .options(*repository.get_eager_options())
        )
        checkout = await repository.get_one_or_none(statement)

        if checkout is None:
            return None

        if not checkout.organization.can_authenticate:
            raise NotPermitted()

        return checkout

    async def create(
        self,
        session: AsyncSession,
        checkout_create: CheckoutCreate,
        auth_subject: AuthSubject[User | Organization],
    ) -> Checkout:
        ad_hoc_prices: dict[Product, Sequence[ProductPrice]] = {}
        if isinstance(checkout_create, CheckoutPriceCreate):
            products, product, price_set, currency = await self._get_validated_price(
                session, auth_subject, checkout_create.product_price_id
            )
        elif isinstance(checkout_create, CheckoutProductCreate):
            products, product, price_set, currency = await self._get_validated_product(
                session,
                auth_subject,
                checkout_create.product_id,
                checkout_create.currency,
            )
        else:
            products = await self._get_validated_products(
                session, auth_subject, checkout_create.products
            )
            product = products[0]
            if checkout_create.prices:
                ad_hoc_prices = await self._get_validated_prices(
                    session,
                    auth_subject,
                    product.organization,
                    products,
                    checkout_create.prices,
                )

            currencies = self._get_currencies(
                checkout_create.currency, product, product.organization
            )

            try:
                prices = ad_hoc_prices[product]
            except KeyError:
                prices = product.prices

            try:
                price_set = PriceSet.from_prices(prices, *currencies)
                currency = price_set.currency
            except NoPricesForCurrencies as e:
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "products", 0),
                            "msg": "Product is not available in the specified currency.",
                            "input": checkout_create.products[0],
                        }
                    ]
                ) from e

        price = price_set.get_default_price()

        if not product.organization.can_authenticate:
            raise NotPermitted()

        await assert_resource_permission(
            session, auth_subject, product, OrganizationPermission.sales_manage
        )

        if checkout_create.amount is not None and is_custom_price(price):
            validate_custom_price_amount(price, checkout_create.amount, currency)

        discount: Discount | None = None
        if checkout_create.discount_id is not None:
            discount = await self._get_validated_discount(
                session,
                product.organization,
                product,
                price,
                currency,
                discount_id=checkout_create.discount_id,
            )

        product = await self._eager_load_product(session, product)

        subscription: Subscription | None = None
        customer: Customer | None = None
        customer_repository = CustomerRepository.from_session(session)
        if checkout_create.subscription_id is not None:
            subscription, customer = await self._get_validated_subscription(
                session, checkout_create.subscription_id, product.organization_id
            )
        elif checkout_create.customer_id is not None:
            customer = await customer_repository.get_by_id_and_organization(
                checkout_create.customer_id, product.organization_id
            )
            if customer is None:
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "customer_id"),
                            "msg": "Customer does not exist.",
                            "input": checkout_create.customer_id,
                        }
                    ]
                )
        elif checkout_create.external_customer_id is not None:
            # Link customer by external ID, if it exists.
            # It not, that's fine': we'll create a new customer on confirm.
            customer = await customer_repository.get_by_external_id_and_organization(
                checkout_create.external_customer_id, product.organization_id
            )

        amount = checkout_create.amount
        if is_fixed_price(price):
            amount = price.price_amount
        elif is_custom_price(price):
            if amount is None:
                amount = price.preset_amount or price.minimum_amount
        else:
            amount = 0

        custom_field_data = validate_custom_field_data(
            product.attached_custom_fields,
            checkout_create.custom_field_data,
            validate_required=False,
        )

        checkout_products = [
            CheckoutProduct(product=product, order=i, ad_hoc_prices=[])
            for i, product in enumerate(products)
        ]

        customer_billing_address = checkout_create.customer_billing_address

        discount_amount = (
            discount.get_discount_amount(amount, currency) if discount else 0
        )
        checkout = Checkout(
            payment_processor=PaymentProcessor.crypto,
            client_secret=generate_token(prefix=CHECKOUT_CLIENT_SECRET_PREFIX),
            amount=amount,
            net_amount=amount - discount_amount,
            currency=currency,
            organization=product.organization,
            checkout_products=checkout_products,
            product=product,
            product_price=price,
            discount=discount,
            customer_billing_address=customer_billing_address,
            subscription=subscription,
            customer=customer,
            custom_field_data=custom_field_data,
            **checkout_create.model_dump(
                exclude={
                    "product_price_id",
                    "product_id",
                    "products",
                    "prices",
                    "amount",
                    "currency",
                    "customer_billing_address",
                    "subscription_id",
                    "custom_field_data",
                },
                by_alias=True,
            ),
        )

        if checkout.customer is not None:
            prefill_attributes: tuple[str, ...] = (
                "email",
                "billing_name",
                "billing_address",
            )
            # A team customer's name refers to the team, not the purchaser.
            if checkout.customer.type != CustomerType.team:
                prefill_attributes += ("name",)
            for attribute in prefill_attributes:
                checkout_attribute = f"customer_{attribute}"
                if getattr(checkout, checkout_attribute) is None:
                    setattr(
                        checkout,
                        checkout_attribute,
                        getattr(checkout.customer, attribute),
                    )

            # For team customers without email, use the owner member email
            if (
                checkout.customer_email is None
                and checkout.customer.email is None
                and checkout.customer.type == CustomerType.team
            ):
                member_repository = MemberRepository.from_session(session)
                owner_member = await member_repository.get_owner_by_customer_id(
                    checkout.customer.id
                )
                if owner_member is not None and owner_member.email is not None:
                    checkout.customer_email = owner_member.email

            if checkout.locale is None and checkout.customer.locale is not None:
                checkout.locale = checkout.customer.locale

            # Auto-select business customer if they have both a billing name (without the fallback to customer.name)
            # and a billing address since that means they've previously checked the is_business_customer checkbox
            # Only auto-select if is_business_customer wasn't explicitly set in the request
            if (
                "is_business_customer" not in checkout_create.model_fields_set
                and checkout.customer.actual_billing_name is not None
                and checkout.customer.billing_address is not None
                and checkout.customer.billing_address.has_address()
            ):
                checkout.is_business_customer = True

        if checkout.payment_processor == PaymentProcessor.crypto:
            # Crypto checkouts: store enabled currencies in metadata for the UI
            checkout.payment_processor_metadata = {
                **(checkout.payment_processor_metadata or {}),
                "accepted_currencies": settings.CRYPTO_CURRENCIES,
                "invoice_expiry_minutes": str(settings.CRYPTO_INVOICE_EXPIRY_MINUTES),
            }

        # `None` locale would opt in to browser-based language detection.
        # If people haven't opted in to this yet, we hardcode the default locale
        # to `en-US` to keep the current behavior
        if not product.organization.feature_settings.get(
            "checkout_localization_enabled", False
        ):
            checkout.locale = "en"

        session.add(checkout)

        checkout = await self._update_trial_end(checkout)

        await session.flush()

        if ad_hoc_prices:
            for checkout_product in checkout.checkout_products:
                checkout_product.ad_hoc_prices = ad_hoc_prices.get(
                    checkout_product.product, []
                )
                session.add(checkout_product)
            await session.flush()

        await self._after_checkout_created(session, checkout)

        return checkout

    async def checkout_link_create(
        self,
        session: AsyncSession,
        checkout_link: CheckoutLink,
        embed_origin: str | None = None,
        ip_address: str | None = None,
        query_prefill: dict[str, str | UUID4 | dict[str, str] | None] | None = None,
        **query_metadata: str | None,
    ) -> Checkout:
        products: list[Product] = []
        for product in checkout_link.products:
            if not product.is_archived and product.visibility != Visibility.draft:
                products.append(product)

        if len(products) == 0:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "products"),
                        "msg": "No valid products.",
                        "input": checkout_link.products,
                    }
                ]
            )

        # Pre-select product if product_id is provided and matches a configured product
        product = products[0]
        query_product_id = query_prefill.get("product_id") if query_prefill else None
        product_id = (
            query_product_id if isinstance(query_product_id, uuid.UUID) else None
        )

        if product_id is not None:
            for p in products:
                if p.id == product_id:
                    product = p
                    break

        currencies = self._get_currencies(None, product, product.organization)

        try:
            currency_prices = PriceSet.from_product(product, *currencies)
        except NoPricesForCurrencies as e:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "products"),
                        "msg": "Product is not available in the specified currency.",
                        "input": str(product.id),
                    }
                ]
            ) from e

        price = currency_prices.get_default_price()
        currency = currency_prices.currency

        amount = 0
        if is_fixed_price(price):
            amount = price.price_amount
        elif is_custom_price(price):
            query_amount_str = query_prefill.get("amount") if query_prefill else None

            valid_query_amount: int | None = None
            if query_amount_str is not None and isinstance(query_amount_str, str):
                try:
                    query_amount_int = int(float(query_amount_str))
                    validate_custom_price_amount(price, query_amount_int, currency)
                    valid_query_amount = query_amount_int
                except (ValueError, TypeError, PolarRequestValidationError):
                    pass

            amount = (
                valid_query_amount
                if valid_query_amount is not None
                else (price.preset_amount or price.minimum_amount or 0)
            )

        discount: Discount | None = None
        if checkout_link.discount_id is not None:
            try:
                discount = await self._get_validated_discount(
                    session,
                    product.organization,
                    product,
                    price,
                    currency,
                    discount_id=checkout_link.discount_id,
                )
            # If the discount is not valid, just ignore it
            except PolarRequestValidationError:
                pass

        link_discount_amount = (
            discount.get_discount_amount(amount, currency) if discount else 0
        )
        checkout = Checkout(
            client_secret=generate_token(prefix=CHECKOUT_CLIENT_SECRET_PREFIX),
            amount=amount,
            net_amount=amount - link_discount_amount,
            currency=currency,
            trial_interval=checkout_link.trial_interval,
            trial_interval_count=checkout_link.trial_interval_count,
            allow_discount_codes=checkout_link.allow_discount_codes,
            allow_trial=True,
            organization=checkout_link.organization,
            checkout_products=[
                CheckoutProduct(product=p, order=i, ad_hoc_prices=[])
                for i, p in enumerate(products)
            ],
            product=product,
            product_price=price,
            discount=discount,
            embed_origin=embed_origin,
            customer_ip_address=ip_address,
            payment_processor=checkout_link.payment_processor,
            success_url=checkout_link.success_url,
            return_url=checkout_link.return_url,
            user_metadata=checkout_link.user_metadata,
        )

        # Handle query parameter prefill
        if query_prefill:
            customer_email = query_prefill.get("customer_email")
            if customer_email is not None and isinstance(customer_email, str):
                checkout.customer_email = customer_email

            customer_name = query_prefill.get("customer_name")
            if customer_name is not None and isinstance(customer_name, str):
                checkout.customer_name = customer_name

            discount_code = query_prefill.get("discount_code")
            if discount_code is not None and isinstance(discount_code, str):
                try:
                    discount = await self._get_validated_discount(
                        session,
                        product.organization,
                        product,
                        price,
                        currency,
                        discount_code=discount_code,
                    )
                    checkout.discount = discount
                except PolarRequestValidationError:
                    pass

            custom_field_data_value = query_prefill.get("custom_field_data")
            if custom_field_data_value is not None and isinstance(
                custom_field_data_value, dict
            ):
                valid_slugs = {
                    cf.custom_field.slug for cf in product.attached_custom_fields
                }

                filtered_data = {
                    slug: value
                    for slug, value in custom_field_data_value.items()
                    if slug in valid_slugs
                }

                if filtered_data:
                    try:
                        validated_data = validate_custom_field_data(
                            product.attached_custom_fields,
                            filtered_data,
                            validate_required=False,
                        )
                        checkout.custom_field_data = {
                            **(checkout.custom_field_data or {}),
                            **validated_data,
                        }
                    except PolarRequestValidationError:
                        # If validation fails, just ignore the custom field data
                        pass

        for key, value in query_metadata.items():
            if value is not None and key not in checkout.user_metadata:
                checkout.user_metadata = {
                    **(checkout.user_metadata or {}),
                    key: value,
                }

        # Allow people setting locale on checkout links
        #
        # `None` locale would opt in to browser-based language detection.
        # If people haven't opted in to this yet, we hardcode the default locale
        # to `en-US` to keep the current behavior
        if product.organization.feature_settings.get(
            "checkout_localization_enabled", False
        ):
            if query_prefill:
                locale = query_prefill.get("locale")
                if locale is not None and isinstance(locale, str):
                    checkout.locale = locale
        else:
            checkout.locale = "en"

        session.add(checkout)

        checkout = await self._update_trial_end(checkout)

        await session.flush()
        await self._after_checkout_created(session, checkout)

        return checkout

    async def update(
        self,
        session: AsyncSession,
        checkout: Checkout,
        checkout_update: CheckoutUpdate | CheckoutUpdatePublic,
    ) -> Checkout:
        async with self._lock_checkout_update(session, checkout) as checkout:
            checkout = await self._update_checkout(session, checkout, checkout_update)
            # Reset is_business_customer if payment form is no longer required
            # This handles the case where a 100% discount is applied and the
            # billing address section disappears from the frontend
            if not checkout.is_payment_form_required:
                checkout.is_business_customer = False

            await self._after_checkout_updated(session, checkout)
            return checkout

    async def confirm(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Anonymous],
        checkout: Checkout,
        checkout_confirm: CheckoutConfirm,
    ) -> Checkout:
        async with self._lock_checkout_update(session, checkout) as checkout:
            checkout = await self._update_checkout(session, checkout, checkout_confirm)
            # When redeeming a discount, we need to lock the discount to prevent concurrent redemptions
            if checkout.discount is not None:
                try:
                    async with discount_service.redeem_discount(
                        session, checkout.discount
                    ) as discount_redemption:
                        discount_redemption.checkout = checkout
                        return await self._confirm_inner(
                            session, auth_subject, checkout, checkout_confirm
                        )
                except DiscountNotRedeemableError as e:
                    raise PolarRequestValidationError(
                        [
                            {
                                "type": "value_error",
                                "loc": ("body", "discount_id"),
                                "msg": "Discount is no longer redeemable.",
                                "input": checkout.discount.id,
                            }
                        ]
                    ) from e

            return await self._confirm_inner(
                session, auth_subject, checkout, checkout_confirm
            )

    async def _confirm_inner(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Anonymous],
        checkout: Checkout,
        checkout_confirm: CheckoutConfirm,
    ) -> Checkout:
        errors: list[ValidationError] = []

        # Case where the price was archived after the checkout was created
        if has_product_checkout(checkout) and checkout.product_price.is_archived:
            errors.append(
                {
                    "type": "value_error",
                    "loc": ("body", "product_price_id"),
                    "msg": "Price is archived.",
                    "input": checkout.product_price_id,
                }
            )

        if not checkout.organization.can_accept_payments:
            if checkout.is_payment_required:
                raise PaymentNotReady()

            if checkout.is_payment_setup_required:
                raise PaymentNotReady()

        required_fields = self._get_required_confirm_fields(checkout)
        for required_field in required_fields:
            if (
                attrgetter(checkout, required_field) is None
                and attrgetter(checkout_confirm, required_field) is None
            ):
                errors.append(
                    {
                        "type": "missing",
                        "loc": ("body", *required_field),
                        "msg": "Field is required.",
                        "input": None,
                    }
                )

        if len(errors) > 0:
            raise PolarRequestValidationError(errors)

        if checkout.payment_processor == PaymentProcessor.crypto:
            await self._create_or_update_customer_simple(
                session, auth_subject, checkout
            )
            await self._confirm_crypto(session, checkout)
        else:
            raise NotImplementedError(
                f"Unsupported payment processor: {checkout.payment_processor}"
            )

        if not checkout.is_payment_required:
            # Free products: immediately fulfil (crypto invoice will have 0 amount — skip)
            if checkout.total_amount == 0:
                enqueue_job("checkout.handle_free_success", checkout_id=checkout.id)

        checkout.status = CheckoutStatus.confirmed
        session.add(checkout)

        await self._after_checkout_updated(session, checkout)

        return checkout

    async def _confirm_crypto(
        self,
        session: AsyncSession,
        checkout: Checkout,
    ) -> None:
        """
        Create a Bitcart crypto invoice for this checkout.
        The invoice addresses are stored in payment_processor_metadata;
        the frontend polls get_crypto_invoice_status() until payment confirms.
        """
        from polar.integrations.crypto.exchange_rate import ExchangeRateService
        from polar.integrations.crypto.invoice_service import crypto_invoice_service
        from polar.redis import create_redis

        redis = create_redis("app")
        rate_service = ExchangeRateService(redis)

        accepted = [
            c.strip().lower()
            for c in settings.CRYPTO_CURRENCIES.split(",")
            if c.strip()
        ]

        invoice = await crypto_invoice_service.create_invoice(
            session,
            order_id=checkout.id,  # use checkout.id as order_id placeholder
            amount_cents=checkout.total_amount,
            fiat_currency=checkout.currency,
            buyer_email=checkout.customer_email,
            accepted_currencies=accepted,
            expiry_minutes=settings.CRYPTO_INVOICE_EXPIRY_MINUTES,
            exchange_rate_service=rate_service,
        )

        checkout.crypto_invoice_id = invoice.id
        checkout.payment_processor_metadata = {
            **checkout.payment_processor_metadata,
            "crypto_invoice_id": str(invoice.id),
            "invoice_expiry": invoice.expiry.isoformat(),
        }
        log.info(
            "checkout.crypto.invoice_created",
            checkout_id=str(checkout.id),
            invoice_id=str(invoice.id),
        )

    async def get_crypto_invoice_status(
        self,
        session: AsyncSession,
        checkout: Checkout,
    ) -> dict:  # type: ignore[type-arg]
        """
        Return the current state of the Bitcart invoice for this checkout.
        Used by the frontend to poll payment status.
        """
        from polar.integrations.crypto.invoice_service import crypto_invoice_service

        if checkout.crypto_invoice_id is None:
            return {"status": "no_invoice"}

        invoice = await crypto_invoice_service.get_invoice_with_methods(
            session, checkout.crypto_invoice_id
        )
        if invoice is None:
            return {"status": "not_found"}

        return {
            "status": invoice.status,
            "exception_status": invoice.exception_status,
            "expiry": invoice.expiry.isoformat(),
            "paid_at": invoice.paid_at.isoformat() if invoice.paid_at else None,
            "payment_methods": [
                {
                    "currency": pm.currency,
                    "amount": str(pm.amount),
                    "payment_address": pm.payment_address,
                    "payment_url": pm.payment_url,
                    "lightning": pm.lightning,
                    "confirmations": pm.confirmations,
                }
                for pm in invoice.payment_methods
            ],
        }

    async def handle_success(
        self,
        session: AsyncSession,
        checkout: Checkout,
        payment: Payment | None = None,
        payment_method: PaymentMethod | None = None,
    ) -> Checkout:
        if checkout.status != CheckoutStatus.confirmed:
            raise NotConfirmedCheckout(checkout)

        if not has_product_checkout(checkout):
            raise NotImplementedError()

        product_price = checkout.product_price
        if product_price.is_archived:
            raise ArchivedPriceCheckout(checkout)

        product = checkout.product
        subscription: Subscription | None = None
        order: Order | None = None
        if product.is_recurring:
            (
                subscription,
                created,
            ) = await subscription_service.create_or_update_from_checkout(
                session, checkout, payment_method
            )
            order = await order_service.create_from_checkout_subscription(
                session,
                checkout,
                subscription,
                OrderBillingReasonInternal.subscription_create
                if created
                else OrderBillingReasonInternal.subscription_update,
                payment,
            )
        else:
            order = await order_service.create_from_checkout_one_time(
                session, checkout, payment
            )

        await self._maybe_auto_claim_buyer_seat(session, checkout, subscription, order)

        # Create trial redemption record if this checkout had a trial period
        if checkout.trial_end is not None:
            assert checkout.customer is not None
            await trial_redemption_service.create_trial_redemption(
                session,
                customer=checkout.customer,
                product=product,
                payment_method_fingerprint=payment_method.fingerprint
                if payment_method
                else None,
            )

        repository = CheckoutRepository.from_session(session)
        checkout = await repository.update(
            checkout,
            update_dict={
                "status": CheckoutStatus.succeeded,
                "payment_processor_metadata": {
                    **checkout.payment_processor_metadata,
                    "intent_status": "succeeded",
                },
            },
        )

        await self._after_checkout_updated(session, checkout)

        CHECKOUT_SUCCEEDED_TOTAL.inc()

        return checkout

    async def _maybe_auto_claim_buyer_seat(
        self,
        session: AsyncSession,
        checkout: Checkout,
        subscription: Subscription | None,
        order: Order | None,
    ) -> None:
        return

    async def handle_failure(
        self, session: AsyncSession, checkout: Checkout, payment: Payment | None = None
    ) -> Checkout:
        # Checkout is in an unrecoverable status: do nothing
        if checkout.status in {
            CheckoutStatus.expired,
            CheckoutStatus.succeeded,
            CheckoutStatus.failed,
        }:
            return checkout

        # Put back checkout in open state so the customer can try another payment method
        checkout.status = CheckoutStatus.open
        checkout.payment_processor_metadata = {
            k: v
            for k, v in checkout.payment_processor_metadata.items()
            if k not in {"intent_status", "intent_client_secret"}
        }
        session.add(checkout)

        # Make sure to remove the Discount Redemptions
        # To avoid race conditions, we save the Discount Redemption when *confirming*
        # the Checkout.
        # However, if it ultimately fails, we need to free up the Discount Redemption.
        await discount_service.remove_checkout_redemption(session, checkout)

        await self._after_checkout_updated(session, checkout)

        return checkout

    async def get_by_client_secret(
        self, session: AsyncSession, client_secret: str
    ) -> Checkout:
        repository = CheckoutRepository.from_session(session)
        checkout = await repository.get_by_client_secret(
            client_secret, options=repository.get_eager_options()
        )
        if checkout is None:
            raise ResourceNotFound()

        if not checkout.organization.can_authenticate:
            raise NotPermitted()

        if checkout.is_expired:
            raise ExpiredCheckoutError()
        return checkout

    async def mark_opened(
        self, session: AsyncSession, checkout: Checkout, distinct_id: str | None = None
    ) -> Checkout:
        """
        Mark a checkout as opened. This is called when the checkout page is first viewed.
        """
        # Already opened - no-op
        if checkout.analytics_metadata and checkout.analytics_metadata.get("opened_at"):
            return checkout

        analytics_metadata = {
            "opened_at": utc_now().isoformat(),
        }

        repository = CheckoutRepository.from_session(session)
        checkout = await repository.update(
            checkout,
            update_dict={"analytics_metadata": analytics_metadata},
        )

        return checkout

    async def _get_validated_price(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product_price_id: uuid.UUID,
    ) -> tuple[Sequence[Product], Product, PriceSet, str]:
        product_price_repository = ProductPriceRepository.from_session(session)
        org_ids = await get_accessible_org_ids(session, auth_subject)
        price = await product_price_repository.get_readable_by_id(
            product_price_id,
            org_ids,
            options=(
                contains_eager(ProductPrice.product).options(
                    joinedload(Product.organization).joinedload(Organization.account),
                    selectinload(Product.prices),
                ),
            ),
        )

        if price is None:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_price_id"),
                        "msg": "Price does not exist.",
                        "input": product_price_id,
                    }
                ]
            )

        if price.is_archived:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_price_id"),
                        "msg": "Price is archived.",
                        "input": product_price_id,
                    }
                ]
            )

        product = price.product
        if product.is_archived:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_price_id"),
                        "msg": "Product is archived.",
                        "input": product_price_id,
                    }
                ]
            )

        currency = price.price_currency

        # Legacy explicit-price checkout: the caller picked one price by ID, so
        # the set is exactly that price. Keeps the amount/seat math identical to
        # selecting it directly (e.g. a metered price still contributes nothing).
        price_set = PriceSet.from_prices([price], currency)

        return [product], product, price_set, currency

    async def _get_validated_product(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product_id: uuid.UUID,
        currency: str | None,
    ) -> tuple[Sequence[Product], Product, PriceSet, str]:
        product = await product_service.get(session, auth_subject, product_id)

        if product is None:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_id"),
                        "msg": "Product does not exist.",
                        "input": product_id,
                    }
                ]
            )

        if product.is_archived:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_id"),
                        "msg": "Product is archived.",
                        "input": product_id,
                    }
                ]
            )

        if product.visibility == Visibility.draft:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_id"),
                        "msg": "Product is a draft.",
                        "input": product_id,
                    }
                ]
            )

        currencies = self._get_currencies(currency, product, product.organization)
        try:
            currency_prices = PriceSet.from_product(product, *currencies)
        except NoPricesForCurrencies as e:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "product_id"),
                        "msg": "Product is not available in the specified currency.",
                        "input": product_id,
                    }
                ]
            ) from e

        currency = currency_prices.currency

        return [product], product, currency_prices, currency

    async def _get_validated_products(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        product_ids: Sequence[uuid.UUID],
    ) -> Sequence[Product]:
        products: list[Product] = []
        errors: list[ValidationError] = []

        for index, product_id in enumerate(product_ids):
            product = await product_service.get(session, auth_subject, product_id)

            if product is None:
                errors.append(
                    {
                        "type": "value_error",
                        "loc": ("body", "products", index),
                        "msg": "Product does not exist.",
                        "input": product_id,
                    }
                )
                continue

            if product.is_archived:
                errors.append(
                    {
                        "type": "value_error",
                        "loc": ("body", "products", index),
                        "msg": "Product is archived.",
                        "input": product_id,
                    }
                )
                continue

            if product.visibility == Visibility.draft:
                errors.append(
                    {
                        "type": "value_error",
                        "loc": ("body", "products", index),
                        "msg": "Product is a draft.",
                        "input": product_id,
                    }
                )
                continue

            products.append(product)

        organization_ids = {product.organization_id for product in products}
        if len(organization_ids) > 1:
            errors.append(
                {
                    "type": "value_error",
                    "loc": ("body", "products"),
                    "msg": "Products must all belong to the same organization.",
                    "input": products,
                }
            )

        if len(errors) > 0:
            raise PolarRequestValidationError(errors)

        return products

    async def _get_validated_prices(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Organization],
        organization: Organization,
        products: Sequence[Product],
        prices: dict[uuid.UUID, ProductPriceCreateList],
    ) -> dict[Product, Sequence[ProductPrice]]:
        validated_prices: dict[Product, Sequence[ProductPrice]] = {}
        errors: list[ValidationError] = []
        for product_id, product_prices in prices.items():
            try:
                product = next(p for p in products if p.id == product_id)
            except StopIteration:
                errors.append(
                    {
                        "type": "value_error",
                        "loc": ("body", "prices", str(product_id)),
                        "msg": "Product is not set on that checkout.",
                        "input": product_id,
                    }
                )
                continue

            (
                validated_product_prices,
                _,
                _,
                price_errors,
            ) = await product_service.get_validated_prices(
                session,
                organization,
                product_prices,
                product.recurring_interval,
                product,
                auth_subject,
                source=ProductPriceSource.ad_hoc,
                error_prefix=(
                    "body",
                    "prices",
                    str(product_id),
                ),
            )
            errors = [*errors, *price_errors]
            validated_prices[product] = validated_product_prices

        if len(errors) > 0:
            raise PolarRequestValidationError(errors)

        return validated_prices

    @typing.overload
    async def _get_validated_discount(
        self,
        session: AsyncSession,
        organization: Organization,
        product: Product,
        price: ProductPrice,
        currency: str,
        *,
        discount_id: uuid.UUID,
    ) -> Discount: ...

    @typing.overload
    async def _get_validated_discount(
        self,
        session: AsyncSession,
        organization: Organization,
        product: Product,
        price: ProductPrice,
        currency: str,
        *,
        discount_code: str,
    ) -> Discount: ...

    async def _get_validated_discount(
        self,
        session: AsyncSession,
        organization: Organization,
        product: Product,
        price: ProductPrice,
        currency: str,
        *,
        discount_id: uuid.UUID | None = None,
        discount_code: str | None = None,
    ) -> Discount:
        loc_field = "discount_id" if discount_id is not None else "discount_code"

        if not any(is_discount_applicable(price) for price in product.prices):
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", loc_field),
                        "msg": "Discounts are not applicable to this product.",
                        "input": discount_id,
                    }
                ]
            )

        discount: Discount | None = None
        if discount_id is not None:
            discount = await discount_service.get_by_id_and_organization(
                session,
                discount_id,
                organization,
                currency=currency,
                products=[product],
            )
        elif discount_code is not None:
            discount = await discount_service.get_by_code_and_product(
                session, discount_code, organization, product, currency
            )

        if discount is None:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", loc_field),
                        "msg": "Discount does not exist.",
                        "input": discount_id,
                    }
                ]
            )

        return discount

    async def _get_validated_subscription(
        self,
        session: AsyncSession,
        subscription_id: uuid.UUID,
        organization_id: uuid.UUID,
    ) -> tuple[Subscription, Customer]:
        subscription_repository = SubscriptionRepository.from_session(session)
        subscription = await subscription_repository.get_by_id_and_organization(
            subscription_id,
            organization_id,
            options=(joinedload(Subscription.customer),),
        )

        if subscription is None:
            raise PolarRequestValidationError(
                [
                    {
                        "type": "value_error",
                        "loc": ("body", "subscription_id"),
                        "msg": "Subscription does not exist.",
                        "input": subscription_id,
                    }
                ]
            )

        for price in subscription.prices:
            if not price.is_free:
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "subscription_id"),
                            "msg": "Only free subscriptions can be upgraded.",
                            "input": subscription_id,
                        }
                    ]
                )

        return subscription, subscription.customer

    @contextlib.asynccontextmanager
    async def _lock_checkout_update(
        self, session: AsyncSession, checkout: Checkout
    ) -> AsyncIterator[Checkout]:
        """
        Lock checkout with FOR UPDATE NOWAIT and reload fresh from database.

        Uses PostgreSQL row-level locking instead of Redis distributed locks.
        If another transaction holds the lock, immediately raises CheckoutLocked
        instead of waiting (NOWAIT behavior).

        Uses FOR UPDATE OF checkouts to lock only the checkout row while still
        allowing eager loading of relationships via LEFT OUTER JOINs.

        See: https://www.postgresql.org/docs/current/explicit-locking.html
        """
        repository = CheckoutRepository.from_session(session)
        checkout_id = checkout.id

        try:
            locked_checkout = await repository.get_by_id_for_update(
                checkout_id,
                nowait=True,
                options=repository.get_eager_options(),
            )
        except DBAPIError as e:
            if is_lock_not_available_error(e):
                raise CheckoutLocked(checkout_id) from e
            raise

        if locked_checkout is None:
            raise ResourceNotFound()

        yield locked_checkout

    async def _update_checkout(
        self,
        session: AsyncSession,
        checkout: Checkout,
        checkout_update: CheckoutUpdate | CheckoutUpdatePublic | CheckoutConfirm,
    ) -> Checkout:
        if checkout.status != CheckoutStatus.open:
            raise NotOpenCheckout(checkout)

        updated_currency = (
            checkout_update.currency
            if isinstance(checkout_update, CheckoutUpdate)
            else None
        )

        # Currency is updated, but not the product, make sure the product supports it
        if updated_currency and checkout_update.product_id is None:
            assert checkout.product is not None
            checkout.currency = updated_currency
            try:
                currency_prices = PriceSet.from_product(
                    checkout.product, updated_currency
                )
            except NoPricesForCurrencies as e:
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "currency"),
                            "msg": "Product is not available in the specified currency.",
                            "input": updated_currency,
                        }
                    ]
                ) from e
            checkout = await self._update_price(
                checkout, checkout_update, currency_prices.get_default_price()
            )
        # Product is updated
        elif checkout_update.product_id is not None:
            product_repository = ProductRepository.from_session(session)
            product = await product_repository.get_by_id_and_checkout(
                checkout_update.product_id,
                checkout.id,
                options=product_repository.get_eager_options(),
            )

            if product is None:
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "product_id"),
                            "msg": "Product is not available in this checkout.",
                            "input": checkout_update.product_id,
                        }
                    ]
                )

            if product.is_archived:
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "value_error",
                            "loc": ("body", "product_id"),
                            "msg": "Product is archived.",
                            "input": checkout_update.product_id,
                        }
                    ]
                )

            checkout.product = product

            if checkout_update.product_price_id is not None:
                try:
                    price = next(
                        p
                        for p in checkout.prices[product.id]
                        if p.id == checkout_update.product_price_id
                    )
                except StopIteration as e:
                    raise PolarRequestValidationError(
                        [
                            {
                                "type": "value_error",
                                "loc": ("body", "product_price_id"),
                                "msg": "Price is not available in this checkout.",
                                "input": checkout_update.product_price_id,
                            }
                        ]
                    ) from e
                # Explicit price selection: the set is exactly that price.
                currency_prices = PriceSet.from_prices([price], checkout.currency)
            else:
                # Product and currency are both updated, make sure the product supports it
                if updated_currency is not None:
                    try:
                        currency_prices = PriceSet.from_product(
                            product, updated_currency
                        )
                        checkout.currency = updated_currency
                    except NoPricesForCurrencies:
                        raise PolarRequestValidationError(
                            [
                                {
                                    "type": "value_error",
                                    "loc": ("body", "currency"),
                                    "msg": "Product is not available in the specified currency.",
                                    "input": updated_currency,
                                }
                            ]
                        )
                # Only product is updated, try to use the existing currency
                # or fallback to default currency if existing currency is not supported
                else:
                    currency_prices = PriceSet.from_product(
                        product,
                        checkout.currency,
                        product.organization.default_presentment_currency,
                    )
                    checkout.currency = currency_prices.currency

            checkout = await self._update_price(
                checkout, checkout_update, currency_prices.get_default_price()
            )

        # When changing product, remove the discount if it's not applicable
        if (
            has_product_checkout(checkout)
            and checkout.discount is not None
            and not checkout.discount.is_applicable(checkout.product, checkout.currency)
        ):
            checkout.discount = None

        # Resolve the checkout's current price set once, then drive the amount /
        # seat logic off it rather than the single `product_price` FK.
        custom_price: CustomPrice | None = None
        static_prices: list[ProductPrice] = []
        if has_product_checkout(checkout):
            price_set = PriceSet.from_prices(
                checkout.prices[checkout.product.id], checkout.currency
            )
            custom_price = price_set.get_custom_price()
            static_prices = price_set.get_static_prices()

        if custom_price is not None and checkout_update.amount is not None:
            validate_custom_price_amount(
                custom_price, checkout_update.amount, checkout.currency
            )
            checkout.amount = checkout_update.amount

        if isinstance(checkout_update, CheckoutUpdate):
            if (
                has_product_checkout(checkout)
                and checkout_update.discount_id is not None
            ):
                checkout.discount = await self._get_validated_discount(
                    session,
                    checkout.organization,
                    checkout.product,
                    checkout.product_price,
                    checkout.currency,
                    discount_id=checkout_update.discount_id,
                )
            # User explicitly removed the discount
            elif "discount_id" in checkout_update.model_fields_set:
                checkout.discount = None
        elif (
            isinstance(checkout_update, CheckoutUpdatePublic)
            and checkout.allow_discount_codes
        ):
            if (
                has_product_checkout(checkout)
                and checkout_update.discount_code is not None
            ):
                discount = await self._get_validated_discount(
                    session,
                    checkout.organization,
                    checkout.product,
                    checkout.product_price,
                    checkout.currency,
                    discount_code=checkout_update.discount_code,
                )
                checkout.discount = discount
            # User explicitly removed the discount
            elif "discount_code" in checkout_update.model_fields_set:
                checkout.discount = None

        checkout.net_amount = checkout.amount - checkout.discount_amount

        if checkout_update.customer_billing_address:
            checkout.customer_billing_address = checkout_update.customer_billing_address

        if (
            has_product_checkout(checkout)
            and checkout_update.custom_field_data is not None
        ):
            custom_field_data = validate_custom_field_data(
                checkout.product.attached_custom_fields,
                checkout_update.custom_field_data,
                validate_required=isinstance(checkout_update, CheckoutConfirm),
            )
            checkout.custom_field_data = custom_field_data

        exclude = {
            "product_id",
            "product_price_id",
            "amount",
            "currency",
            "customer_billing_address",
            "custom_field_data",
        }

        if checkout.customer_id is not None:
            exclude.add("customer_email")

        for attr, value in checkout_update.model_dump(
            exclude_unset=True, exclude=exclude, by_alias=True
        ).items():
            setattr(checkout, attr, value)

        # `None` locale would opt in to browser-based language detection.
        # If people haven't opted in to this yet, we hardcode the default locale
        # to `en-US` to keep the current behavior
        if not checkout.organization.feature_settings.get(
            "checkout_localization_enabled", False
        ):
            checkout.locale = "en"

        checkout = await self._update_trial_end(checkout)

        session.add(checkout)

        await self._validate_subscription_uniqueness(session, checkout)

        return checkout

    async def _update_price(
        self,
        checkout: Checkout,
        checkout_update: CheckoutUpdate | CheckoutUpdatePublic,
        price: ProductPrice,
    ) -> Checkout:
        checkout.product_price = price
        checkout.amount = 0
        if is_fixed_price(price):
            checkout.amount = price.price_amount
        elif is_custom_price(price):
            checkout.amount = price.preset_amount or price.minimum_amount

        return checkout

    def _get_currencies(
        self,
        currency_request: str | None,
        product: Product,
        organization: Organization,
    ) -> Sequence[str]:
        if currency_request is not None:
            return [currency_request]
        return [organization.default_presentment_currency]

    async def _update_trial_end(self, checkout: Checkout) -> Checkout:
        if not has_product_checkout(checkout):
            checkout.trial_end = None
            return checkout

        if not checkout.product.is_recurring:
            checkout.trial_end = None
            return checkout

        trial_interval = checkout.active_trial_interval
        trial_interval_count = checkout.active_trial_interval_count

        if trial_interval is not None and trial_interval_count is not None:
            checkout.trial_end = trial_interval.get_end(utc_now(), trial_interval_count)
        else:
            checkout.trial_end = None

        return checkout

    async def _validate_subscription_uniqueness(
        self, session: AsyncSession, checkout: Checkout
    ) -> None:
        organization = checkout.organization

        # No product checkout
        if not has_product_checkout(checkout):
            return

        # One-time purchase
        if not checkout.product.is_recurring:
            return

        # Subscription upgrade
        if checkout.subscription is not None:
            return

        # No information yet to check customer subscription uniqueness
        if checkout.customer_id is None and checkout.customer_email is None:
            return

        statement = (
            select(Subscription)
            .join(Product, onclause=Product.id == Subscription.product_id)
            .where(
                Product.organization_id == organization.id,
                Subscription.billable.is_(True),
            )
        )
        if checkout.customer is not None:
            statement = statement.where(
                Subscription.customer_id == checkout.customer_id
            )
        elif checkout.customer_email is not None:
            statement = statement.join(
                Customer, onclause=Customer.id == Subscription.customer_id
            ).where(
                func.lower(Customer.email) == checkout.customer_email.lower(),
                Customer.is_deleted.is_(False),
            )

        result = await session.execute(statement)
        existing_subscriptions = result.scalars().all()

        if len(existing_subscriptions) > 0:
            raise AlreadyActiveSubscriptionError()

    def _get_required_confirm_fields(self, checkout: Checkout) -> set[tuple[str, ...]]:
        fields: set[tuple[str, ...]] = set()
        # Email is not required when the customer is already identified
        if checkout.customer_id is None:
            fields.add(("customer_email",))
        if checkout.is_business_customer:
            fields.update({("customer_billing_name",)})
        return fields

    async def _create_or_update_customer_simple(
        self,
        session: AsyncSession,
        auth_subject: AuthSubject[User | Anonymous],
        checkout: Checkout,
    ) -> None:
        """Create or find customer record (no Stripe account required)."""
        repository = CustomerRepository.from_session(session)

        customer = checkout.customer

        if customer is not None and customer.is_deleted:
            raise CheckoutCustomerDeleted(checkout)

        created = False
        if customer is None:
            assert checkout.customer_email is not None
            customer = await repository.get_by_email_and_organization(
                checkout.customer_email, checkout.organization.id
            )
            if customer is None and checkout.external_customer_id is not None:
                existing = await repository.get_by_external_id_and_organization(
                    checkout.external_customer_id,
                    checkout.organization.id,
                )
                if existing is not None:
                    raise CheckoutCustomerExternalIdMismatch()
            if customer is None:
                customer = Customer(
                    external_id=checkout.external_customer_id,
                    email=checkout.customer_email,
                    email_verified=False,
                    organization=checkout.organization,
                    user_metadata={},
                )
                created = True

        customer_name = checkout.customer_billing_name or checkout.customer_name
        if created and customer_name is not None:
            customer.name = customer_name
        if checkout.customer_billing_name is not None:
            customer.billing_name = checkout.customer_billing_name
        if checkout.customer_billing_address is not None:
            customer.billing_address = checkout.customer_billing_address
        if checkout.locale is not None:
            customer.locale = checkout.locale

        customer.user_metadata = {
            **customer.user_metadata,
            **checkout.customer_metadata,
        }

        if created:
            async with repository.create_context(customer, flush=False) as customer:
                await member_service.create_owner_member(
                    session, customer, checkout.organization
                )
                checkout.customer = customer
        else:
            checkout.customer = await repository.update(customer, flush=True)

    async def _after_checkout_created(
        self, session: AsyncSession, checkout: Checkout
    ) -> None:
        CHECKOUT_CREATED_TOTAL.inc()

        metadata = CheckoutCreatedMetadata(
            checkout_id=str(checkout.id),
            checkout_status=checkout.status,
        )
        if checkout.product_id:
            metadata["product_id"] = str(checkout.product_id)
        await event_service.create_event(
            session,
            build_checkout_event(
                SystemEvent.checkout_created,
                checkout.organization,
                metadata,
            ),
        )
        await webhook_service.send(
            session, checkout.organization, WebhookEventType.checkout_created, checkout
        )

    async def _after_checkout_updated(
        self, session: AsyncSession, checkout: Checkout
    ) -> None:
        await publish_checkout_event(
            checkout.client_secret, CheckoutEvent.updated, {"status": checkout.status}
        )
        events = await webhook_service.send(
            session, checkout.organization, WebhookEventType.checkout_updated, checkout
        )
        # No webhook to send, publish the webhook_event immediately
        if len(events) == 0:
            await publish_checkout_event(
                checkout.client_secret,
                CheckoutEvent.webhook_event_delivered,
                {"status": checkout.status},
            )

    async def send_expiration_events(
        self, session: AsyncSession, checkout: Checkout
    ) -> None:
        await publish_checkout_event(
            checkout.client_secret, CheckoutEvent.updated, {"status": checkout.status}
        )
        await webhook_service.send(
            session, checkout.organization, WebhookEventType.checkout_expired, checkout
        )

    async def _eager_load_product(
        self, session: AsyncSession, product: Product
    ) -> Product:
        await session.refresh(
            product,
            {"organization", "prices", "attached_custom_fields"},
        )
        return product


checkout = CheckoutService()

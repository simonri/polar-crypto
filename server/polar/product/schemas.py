import builtins
from typing import Annotated, Any, Literal

from pydantic import (
    UUID4,
    Discriminator,
    Field,
    Tag,
    ValidationInfo,
    computed_field,
    field_validator,
)
from pydantic.aliases import AliasChoices
from pydantic.json_schema import SkipJsonSchema
from pydantic_core import PydanticCustomError

from polar.custom_field.schemas import (
    AttachedCustomField,
    AttachedCustomFieldListCreate,
)
from polar.enums import SubscriptionRecurringInterval
from polar.kit.currency import (
    MAXIMUM_PRICE_PER_CURRENCY_DOCSTRING,
    MINIMUM_PRICE_PER_CURRENCY_DOCSTRING,
    PresentmentCurrency,
    format_currency,
    get_maximum_currency_amount,
    get_minimum_currency_amount,
)
from polar.kit.db.models import Model
from polar.kit.metadata import (
    MetadataInputMixin,
    MetadataOutputMixin,
)
from polar.kit.schemas import (
    EmptyStrToNoneValidator,
    IDSchema,
    MergeJSONSchema,
    Schema,
    SelectorWidget,
    SetSchemaReference,
    StripValidator,
    TimestampedSchema,
)
from polar.kit.trial import TrialConfigurationInputMixin, TrialConfigurationOutputMixin
from polar.kit.visibility import Visibility
from polar.models.product import ProductVisibility
from polar.models.product_price import (
    ProductPriceAmountType,
    ProductPriceSource,
    ProductPriceType,
)
from polar.models.product_price import (
    ProductPriceCustom as ProductPriceCustomModel,
)
from polar.models.product_price import (
    ProductPriceFixed as ProductPriceFixedModel,
)
from polar.models.product_price import (
    ProductPriceFree as ProductPriceFreeModel,
)
from polar.organization.schemas import OrganizationID

PRODUCT_NAME_MIN_LENGTH = 3
PRODUCT_NAME_MAX_LENGTH = 64

# Product

ProductID = Annotated[
    UUID4,
    MergeJSONSchema({"description": "The product ID."}),
    SelectorWidget("/v1/products", "Product", "name"),
]


def validate_price_amount(
    currency: str, amount: int, *, allow_zero: bool = False
) -> int:
    minimum = get_minimum_currency_amount(currency)
    if amount < minimum and not (allow_zero and amount == 0):
        if allow_zero:
            message = f"Amount must be at least {format_currency(minimum, currency)} or 0 for free pricing"
        else:
            message = f"Amount must be at least {format_currency(minimum, currency)}"
        raise PydanticCustomError("minimum_price", message)  # pyright: ignore
    maximum = get_maximum_currency_amount(currency)
    if amount > maximum:
        message = f"Amount must be at most {format_currency(maximum, currency)}"
        raise PydanticCustomError("maximum_price", message)  # pyright: ignore
    return amount


PriceAmount = Annotated[
    int,
    Field(
        ...,
        ge=1,
        description=f"The price in cents.\nMinimum amounts per currency:\n{MINIMUM_PRICE_PER_CURRENCY_DOCSTRING}",
    ),
]
PriceCurrency = Annotated[
    PresentmentCurrency,
    Field(description="The currency in which the customer will be charged."),
]
ProductName = Annotated[
    str,
    StripValidator,
    Field(
        min_length=PRODUCT_NAME_MIN_LENGTH,
        max_length=PRODUCT_NAME_MAX_LENGTH,
        description="The name of the product.",
    ),
]
ProductDescription = Annotated[
    str | None,
    Field(description="The description of the product."),
    EmptyStrToNoneValidator,
]


class ProductPriceCreateBase(Schema):
    amount_type: ProductPriceAmountType
    price_currency: PriceCurrency = PresentmentCurrency.usd

    def get_model_class(self) -> builtins.type[Model]:
        raise NotImplementedError()


class ProductPriceFixedCreate(ProductPriceCreateBase):
    """
    Schema to create a fixed price.
    """

    amount_type: Literal[ProductPriceAmountType.fixed]
    price_amount: PriceAmount

    @field_validator("price_amount")
    @classmethod
    def validate_price_amount(cls, v: int, info: ValidationInfo) -> int:
        currency = info.data.get("price_currency")
        if currency is None:
            # price_currency failed its own validation; skip currency-specific check
            # (Pydantic will already report the currency error)
            return v
        return validate_price_amount(currency, v)

    def get_model_class(self) -> builtins.type[ProductPriceFixedModel]:
        return ProductPriceFixedModel


class ProductPriceCustomCreate(ProductPriceCreateBase):
    """
    Schema to create a pay-what-you-want price.
    """

    amount_type: Literal[ProductPriceAmountType.custom]
    minimum_amount: int = Field(
        default=50,
        ge=0,
        description=(
            "The minimum amount the customer can pay. "
            "If set to 0, the price is 'free or pay what you want' and $0 is accepted. "
            "If set to a value below the minimum price amount for the currency, it will be rejected. "
            "Defaults to the minimum price amount for the currency. "
            f"Minimum per currency:\n{MINIMUM_PRICE_PER_CURRENCY_DOCSTRING}"
        ),
    )
    maximum_amount: PriceAmount | None = Field(
        default=None,
        description=(
            "The maximum amount the customer can pay. "
            f"Maximum per currency:\n{MAXIMUM_PRICE_PER_CURRENCY_DOCSTRING}"
        ),
    )
    preset_amount: PriceAmount | None = Field(
        default=None,
        ge=0,
        description=(
            "The initial amount shown to the customer. "
            "If 0, the customer will see $0 as the default. "
            "If set to a value below the minimum price amount for the currency, it will be rejected."
            f"Minimum per currency:\n{MINIMUM_PRICE_PER_CURRENCY_DOCSTRING}"
        ),
    )

    @field_validator("minimum_amount", "preset_amount", "maximum_amount")
    @classmethod
    def validate_amount_not_in_minimum_gap(
        cls, v: int | None, info: ValidationInfo
    ) -> int | None:
        if v is None:
            return v
        currency = info.data.get("price_currency")
        if currency is None:
            # price_currency failed its own validation; skip currency-specific check
            # (Pydantic will already report the currency error)
            return v
        return validate_price_amount(currency, v, allow_zero=True)

    def get_model_class(self) -> builtins.type[ProductPriceCustomModel]:
        return ProductPriceCustomModel


class ProductPriceFreeCreate(ProductPriceCreateBase):
    """
    Schema to create a free price.
    """

    amount_type: Literal[ProductPriceAmountType.free]

    def get_model_class(self) -> builtins.type[ProductPriceFreeModel]:
        return ProductPriceFreeModel


ProductPriceCreate = Annotated[
    ProductPriceFixedCreate
    | ProductPriceCustomCreate
    | ProductPriceFreeCreate,
    Discriminator("amount_type"),
]


ProductPriceCreateList = Annotated[
    list[ProductPriceCreate],
    Field(min_length=1),
    MergeJSONSchema(
        {
            "title": "ProductPriceCreateList",
            "description": (
                "List of prices for the product. "
                "At most one static price (fixed, custom or free) is allowed."
            ),
        }
    ),
]


class ProductCreateBase(MetadataInputMixin, Schema):
    name: ProductName
    description: ProductDescription = None
    visibility: ProductVisibility = Field(
        default=Visibility.public,
        description="The visibility of the product.",
    )
    prices: ProductPriceCreateList = Field(
        ...,
        description="List of available prices for this product. "
        "It should contain at most one static price (fixed, custom or free).",
    )
    attached_custom_fields: AttachedCustomFieldListCreate = Field(default_factory=list)
    organization_id: OrganizationID | None = Field(
        default=None,
        description=(
            "The ID of the organization owning the product. "
            "**Required unless you use an organization token.**"
        ),
    )


class ProductCreateRecurring(TrialConfigurationInputMixin, ProductCreateBase):
    recurring_interval: SubscriptionRecurringInterval = Field(
        description="The recurring interval of the product.",
    )
    recurring_interval_count: int = Field(
        default=1,
        ge=1,
        le=999,
        description=(
            "Number of interval units of the subscription. "
            "If this is set to 1 the charge will happen every interval (e.g. every month), "
            "if set to 2 it will be every other month, and so on."
        ),
    )


class ProductCreateOneTime(ProductCreateBase):
    recurring_interval: Literal[None] = Field(
        default=None, description="States that the product is a one-time purchase."
    )
    recurring_interval_count: Literal[None] = Field(
        default=None,
        description="One-time products don't have a recurring interval count.",
    )


def _product_create_discriminator(v: Any) -> str:
    if isinstance(v, dict):
        ri = v.get("recurring_interval")
    else:
        ri = getattr(v, "recurring_interval", None)
    return "recurring" if ri is not None else "one_time"


ProductCreate = Annotated[
    Annotated[ProductCreateRecurring, Tag("recurring")]
    | Annotated[ProductCreateOneTime, Tag("one_time")],
    Discriminator(_product_create_discriminator),
    SetSchemaReference("ProductCreate"),
]


class ExistingProductPrice(Schema):
    """
    A price that already exists for this product.

    Useful when updating a product if you want to keep an existing price.
    """

    id: UUID4


ProductPriceUpdate = Annotated[
    ExistingProductPrice | ProductPriceCreate, Field(union_mode="left_to_right")
]


class ProductUpdate(TrialConfigurationInputMixin, MetadataInputMixin, Schema):
    """
    Schema to update a product.
    """

    name: ProductName | None = None
    description: ProductDescription = None
    recurring_interval: SubscriptionRecurringInterval | None = Field(
        default=None,
        description=(
            "The recurring interval of the product. "
            "If `None`, the product is a one-time purchase. "
            "**Can only be set on legacy recurring products. "
            "Once set, it can't be changed.**"
        ),
    )
    recurring_interval_count: int | None = Field(
        default=None,
        ge=1,
        le=999,
        description=(
            "Number of interval units of the subscription. "
            "If this is set to 1 the charge will happen every interval (e.g. every month), "
            "if set to 2 it will be every other month, and so on. "
            "Once set, it can't be changed.**"
        ),
    )
    is_archived: bool | None = Field(
        default=None,
        description=(
            "Whether the product is archived. "
            "If `true`, the product won't be available for purchase anymore. "
            "Existing customers will still have access to their benefits, "
            "and subscriptions will continue normally."
        ),
    )
    visibility: ProductVisibility | None = Field(
        default=None,
        description="The visibility of the product.",
    )
    prices: list[ProductPriceUpdate] | None = Field(
        default=None,
        description=(
            "List of available prices for this product. "
            "If you want to keep existing prices, include them in the list "
            "as an `ExistingProductPrice` object."
        ),
    )
    attached_custom_fields: AttachedCustomFieldListCreate | None = None


class ProductPriceBase(TimestampedSchema):
    id: UUID4 = Field(description="The ID of the price.")
    source: ProductPriceSource = Field(
        description=(
            "The source of the price . "
            "`catalog` is a predefined price, "
            "while `ad_hoc` is a price created dynamically on a Checkout session."
        )
    )
    amount_type: ProductPriceAmountType = Field(
        description="The type of amount, either fixed or custom."
    )
    price_currency: str = Field(
        description="The currency in which the customer will be charged."
    )
    is_archived: bool = Field(
        description="Whether the price is archived and no longer available."
    )
    product_id: UUID4 = Field(description="The ID of the product owning the price.")

    type: SkipJsonSchema[ProductPriceType] = Field(
        validation_alias=AliasChoices("legacy_type", "type"),
        deprecated=(
            "This field is actually set from Product. "
            "It's only kept for backward compatibility."
        ),
    )
    recurring_interval: SkipJsonSchema[SubscriptionRecurringInterval | None] = Field(
        validation_alias=AliasChoices(
            "legacy_recurring_interval", "recurring_interval"
        ),
        deprecated=(
            "This field is actually set from Product. "
            "It's only kept for backward compatibility."
        ),
    )


class ProductPriceFixedBase(ProductPriceBase):
    amount_type: Literal[ProductPriceAmountType.fixed]
    price_amount: int = Field(description="The price in cents.")


class ProductPriceCustomBase(ProductPriceBase):
    amount_type: Literal[ProductPriceAmountType.custom]
    minimum_amount: int = Field(
        description=(
            "The minimum amount the customer can pay. "
            "If 0, the price is 'free or pay what you want'."
        )
    )
    maximum_amount: int | None = Field(
        description="The maximum amount the customer can pay."
    )
    preset_amount: int | None = Field(
        description="The initial amount shown to the customer."
    )


class ProductPriceFreeBase(ProductPriceBase):
    amount_type: Literal[ProductPriceAmountType.free]


class LegacyRecurringProductPriceMixin:
    @computed_field
    def legacy(self) -> Literal[True]:
        return True


class LegacyRecurringProductPriceFixed(
    ProductPriceFixedBase, LegacyRecurringProductPriceMixin
):
    """
    A recurring price for a product, i.e. a subscription.

    **Deprecated**: The recurring interval should be set on the product itself.
    """

    type: Literal[ProductPriceType.recurring] = Field(
        description="The type of the price."
    )
    recurring_interval: SubscriptionRecurringInterval = Field(
        description="The recurring interval of the price."
    )


class LegacyRecurringProductPriceCustom(
    ProductPriceCustomBase, LegacyRecurringProductPriceMixin
):
    """
    A pay-what-you-want recurring price for a product, i.e. a subscription.

    **Deprecated**: The recurring interval should be set on the product itself.
    """

    type: Literal[ProductPriceType.recurring] = Field(
        description="The type of the price."
    )
    recurring_interval: SubscriptionRecurringInterval = Field(
        description="The recurring interval of the price."
    )


class LegacyRecurringProductPriceFree(
    ProductPriceFreeBase, LegacyRecurringProductPriceMixin
):
    """
    A free recurring price for a product, i.e. a subscription.

    **Deprecated**: The recurring interval should be set on the product itself.
    """

    type: Literal[ProductPriceType.recurring] = Field(
        description="The type of the price."
    )
    recurring_interval: SubscriptionRecurringInterval = Field(
        description="The recurring interval of the price."
    )


LegacyRecurringProductPrice = Annotated[
    LegacyRecurringProductPriceFixed
    | LegacyRecurringProductPriceCustom
    | LegacyRecurringProductPriceFree,
    Discriminator("amount_type"),
    SetSchemaReference("LegacyRecurringProductPrice"),
]


class ProductPriceFixed(ProductPriceFixedBase):
    """
    A fixed price for a product.
    """


class ProductPriceCustom(ProductPriceCustomBase):
    """
    A pay-what-you-want price for a product.
    """


class ProductPriceFree(ProductPriceFreeBase):
    """
    A free price for a product.
    """


NewProductPrice = Annotated[
    ProductPriceFixed | ProductPriceCustom | ProductPriceFree,
    Discriminator("amount_type"),
    SetSchemaReference("ProductPrice"),
]


def _get_discriminator_value(v: Any) -> Literal["legacy", "new"]:
    if isinstance(v, dict):
        return "legacy" if "legacy" in v else "new"
    type = getattr(v, "type", None)
    return "legacy" if type is not None else "new"


ProductPrice = Annotated[
    Annotated[LegacyRecurringProductPrice, Tag("legacy")]
    | Annotated[NewProductPrice, Tag("new")],
    Discriminator(_get_discriminator_value),
]


class ProductBase(TrialConfigurationOutputMixin, TimestampedSchema, IDSchema):
    name: str = Field(description="The name of the product.")
    description: str | None = Field(description="The description of the product.")
    visibility: ProductVisibility = Field(description="The visibility of the product.")
    recurring_interval: SubscriptionRecurringInterval | None = Field(
        description=(
            "The recurring interval of the product. "
            "If `None`, the product is a one-time purchase."
        )
    )
    recurring_interval_count: int | None = Field(
        description=(
            "Number of interval units of the subscription. "
            "If this is set to 1 the charge will happen every interval (e.g. every month), "
            "if set to 2 it will be every other month, and so on. "
            "None for one-time products."
        )
    )
    is_recurring: bool = Field(description="Whether the product is a subscription.")
    is_archived: bool = Field(
        description="Whether the product is archived and no longer available."
    )
    organization_id: UUID4 = Field(
        description="The ID of the organization owning the product."
    )


ProductPriceList = Annotated[
    list[ProductPrice],
    Field(
        description="List of prices for this product.",
    ),
]
class Product(MetadataOutputMixin, ProductBase):
    """
    A product.
    """

    prices: ProductPriceList
    attached_custom_fields: list[AttachedCustomField] = Field(
        description="List of custom fields attached to the product."
    )

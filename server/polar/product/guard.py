import typing

from typing_extensions import TypeIs

from polar.enums import SubscriptionRecurringInterval
from polar.models.product import Product
from polar.models.product_price import (
    LegacyRecurringProductPriceCustom,
    LegacyRecurringProductPriceFixed,
    LegacyRecurringProductPriceFree,
    ProductPrice,
    ProductPriceCustom,
    ProductPriceFixed,
    ProductPriceFree,
)

type StaticPrice = (
    ProductPriceFixed
    | LegacyRecurringProductPriceFixed
    | ProductPriceFree
    | LegacyRecurringProductPriceFree
    | ProductPriceCustom
    | LegacyRecurringProductPriceCustom
)

type FixedPrice = ProductPriceFixed | LegacyRecurringProductPriceFixed

type CustomPrice = ProductPriceCustom | LegacyRecurringProductPriceCustom

type FreePrice = ProductPriceFree | LegacyRecurringProductPriceFree

type LegacyPrice = (
    LegacyRecurringProductPriceFixed
    | LegacyRecurringProductPriceFree
    | LegacyRecurringProductPriceCustom
)


def is_legacy_price(price: ProductPrice) -> TypeIs[LegacyPrice]:
    return isinstance(
        price,
        LegacyRecurringProductPriceFixed
        | LegacyRecurringProductPriceFree
        | LegacyRecurringProductPriceCustom,
    )


def is_fixed_price(price: ProductPrice) -> TypeIs[FixedPrice]:
    return isinstance(price, ProductPriceFixed | LegacyRecurringProductPriceFixed)


def is_custom_price(price: ProductPrice) -> TypeIs[CustomPrice]:
    return isinstance(price, ProductPriceCustom | LegacyRecurringProductPriceCustom)


def is_free_price(price: ProductPrice) -> TypeIs[FreePrice]:
    return isinstance(price, ProductPriceFree | LegacyRecurringProductPriceFree)


def is_static_price(price: ProductPrice) -> TypeIs[StaticPrice]:
    return price.is_static


def is_discount_applicable(
    price: ProductPrice,
) -> TypeIs[FixedPrice | CustomPrice]:
    return is_fixed_price(price) or is_custom_price(price)


if typing.TYPE_CHECKING:

    class RecurringProduct(Product):
        recurring_interval: SubscriptionRecurringInterval  # pyright: ignore
        recurring_interval_count: int  # pyright: ignore
else:
    RecurringProduct = Product


def is_recurring_product(product: Product) -> TypeIs[RecurringProduct]:
    return product.recurring_interval is not None

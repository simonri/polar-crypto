import { schemas } from '@polar-sh/client'

type ProductPrice =
  | schemas['ProductPriceFixed']
  | schemas['ProductPriceCustom']
  | schemas['ProductPriceFree']

export const isLegacyRecurringPrice = (
  price: schemas['ProductPrice'] | schemas['LegacyRecurringProductPrice'],
): price is schemas['LegacyRecurringProductPrice'] => 'legacy' in price

export const hasLegacyRecurringPrices = (
  product: schemas['Product'],
): product is schemas['Product'] & {
  prices: schemas['LegacyRecurringProductPrice'][]
} => product.prices.some(isLegacyRecurringPrice)

export const isStaticPrice = (
  price: ProductPrice,
): price is
  | schemas['ProductPriceFixed']
  | schemas['ProductPriceCustom']
  | schemas['ProductPriceFree'] =>
  price.amount_type !== undefined &&
  ['fixed', 'custom', 'free'].includes(price.amount_type)


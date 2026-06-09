import type { schemas } from '@polar-sh/client'

export const isLegacyRecurringPrice = (
  price: schemas['ProductPrice'] | schemas['LegacyRecurringProductPrice'],
): price is schemas['LegacyRecurringProductPrice'] => 'legacy' in price

export const hasLegacyRecurringPrices = (
  prices: schemas['ProductPrice'][],
): prices is schemas['LegacyRecurringProductPrice'][] =>
  prices.some(isLegacyRecurringPrice)

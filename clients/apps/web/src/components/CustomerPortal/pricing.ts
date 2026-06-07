import { schemas } from '@polar-sh/client'

export function getPendingTotalAmount(
  product: schemas['Product'],
  currency: string,
): number | null {
  const price = product.prices.find((p) => p.price_currency === currency)
  if (!price) return null

  if (price.amount_type === 'fixed') {
    return price.price_amount
  }

  return null
}

export const getCustomerSubscriptionBasePrice = (
  subscription: schemas['CustomerSubscription'],
): { amount: number; currency: string } | null => {
  const price = subscription.product.prices.find(
    ({ amount_type, price_currency }) =>
      (amount_type === 'fixed' || amount_type === 'custom') &&
      price_currency === subscription.currency,
  )

  if (!price) {
    return null
  }

  // This should be obsolete but I don't think we have proper type guards for the generated schema
  if ('price_amount' in price) {
    return {
      amount: price.price_amount,
      currency: price.price_currency,
    }
  }

  return null
}

import type { schemas } from '@polar-sh/client'
import { describe, expect, it } from 'vitest'
import {
  hasLegacyRecurringPrices,
  isLegacyRecurringPrice,
} from './product'

const makePrice = (
  overrides: Partial<schemas['ProductPrice']>,
): schemas['ProductPrice'] =>
  ({
    id: 'price_1',
    amount_type: 'fixed',
    price_currency: 'usd',
    price_amount: 1000,
    ...overrides,
  }) as schemas['ProductPrice']

const makeLegacyPrice = (): schemas['LegacyRecurringProductPrice'] =>
  ({
    id: 'price_legacy',
    legacy: true,
    amount_type: 'fixed',
    price_currency: 'usd',
    price_amount: 500,
    recurring_interval: 'month',
  }) as schemas['LegacyRecurringProductPrice']

describe('isLegacyRecurringPrice', () => {
  it('returns true for legacy prices', () => {
    expect(isLegacyRecurringPrice(makeLegacyPrice())).toBe(true)
  })

  it('returns false for non-legacy prices', () => {
    expect(isLegacyRecurringPrice(makePrice({}))).toBe(false)
  })
})

describe('hasLegacyRecurringPrices', () => {
  it('returns true when array contains a legacy price', () => {
    // Legacy prices satisfy the ProductPrice union too for this test
    const prices = [makeLegacyPrice()] as unknown as schemas['ProductPrice'][]
    expect(hasLegacyRecurringPrices(prices)).toBe(true)
  })

  it('returns false when array has no legacy prices', () => {
    const prices = [makePrice({})]
    expect(hasLegacyRecurringPrices(prices)).toBe(false)
  })

  it('returns false for empty array', () => {
    expect(hasLegacyRecurringPrices([])).toBe(false)
  })
})


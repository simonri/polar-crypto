import { render } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { createCheckout, createFreePrice } from '../test-utils/makeCheckout'
import { CheckoutProductSwitcherItemPrice } from './CheckoutProductSwitcher'

function getRenderedText(container: HTMLElement): string {
  return container.textContent?.trim() ?? ''
}

describe('CheckoutProductSwitcherItemPrice', () => {
  describe('fixed price (non-seat-based)', () => {
    it('shows catalog price via ProductPriceLabel', () => {
      const checkout = createCheckout()
      const { container } = render(
        <CheckoutProductSwitcherItemPrice
          isSelected={true}
          product={checkout.product}
          price={checkout.product_price}
          checkout={checkout}
          locale="en"
        />,
      )

      expect(getRenderedText(container)).toBe('$9.99')
    })

    it('shows catalog price even when not selected', () => {
      const checkout = createCheckout()
      const { container } = render(
        <CheckoutProductSwitcherItemPrice
          isSelected={false}
          product={checkout.product}
          price={checkout.product_price}
          checkout={checkout}
          locale="en"
        />,
      )

      expect(getRenderedText(container)).toBe('$9.99')
    })
  })

  describe('free price', () => {
    it('shows "Free"', () => {
      const freePrice = createFreePrice({ id: 'price_free' })
      const checkout = createCheckout({
        amount: 0,
        net_amount: 0,
        total_amount: 0,
        is_free_product_price: true,
        product_price: freePrice,
      })

      const { container } = render(
        <CheckoutProductSwitcherItemPrice
          isSelected={true}
          product={checkout.product}
          price={freePrice}
          checkout={checkout}
          locale="en"
        />,
      )

      expect(getRenderedText(container)).toBe('Free')
    })
  })
})

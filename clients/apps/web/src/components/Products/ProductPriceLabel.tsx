import { isLegacyRecurringPrice } from '@/utils/product'
import { schemas } from '@polar-sh/client'
import AmountLabel from '../Shared/AmountLabel'

interface ProductPriceLabelProps {
  product: schemas['Product'] | schemas['CheckoutProduct']
  currency: string
}

const ProductPriceLabel: React.FC<ProductPriceLabelProps> = ({
  product,
  currency,
}: ProductPriceLabelProps) => {
  const staticPrice = product.prices.find(
    ({ amount_type, price_currency }) =>
      price_currency === currency &&
      ['fixed', 'custom', 'free'].includes(amount_type),
  )

  if (!staticPrice) {
    return null
  }

  if (staticPrice.amount_type === 'fixed') {
    return (
      <AmountLabel
        amount={staticPrice.price_amount}
        currency={staticPrice.price_currency}
        interval={
          isLegacyRecurringPrice(staticPrice)
            ? staticPrice.recurring_interval
            : product.recurring_interval || undefined
        }
        intervalCount={product.recurring_interval_count}
      />
    )
  } else if (staticPrice.amount_type === 'custom') {
    return <div className="text-[min(1em,24px)]">Pay what you want</div>
  } else {
    return <div className="text-[min(1em,24px)]">Free</div>
  }
}

export default ProductPriceLabel

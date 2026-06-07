import type { schemas } from '@polar-sh/client'
import {
  DEFAULT_LOCALE,
  useTranslations,
  type AcceptedLocale,
} from '@polar-sh/i18n'
import { isLegacyRecurringPrice } from '../utils/product'
import AmountLabel from './AmountLabel'

interface ProductPriceLabelProps {
  product: schemas['CheckoutProduct']
  price: schemas['ProductPrice'] | schemas['LegacyRecurringProductPrice']
  locale?: AcceptedLocale
  mode?: 'compact' | 'standard'
}

const ProductPriceLabel: React.FC<ProductPriceLabelProps> = ({
  product,
  price,
  locale = DEFAULT_LOCALE,
  mode = 'compact',
}) => {
  const t = useTranslations(locale)

  if (price.amount_type === 'fixed') {
    return (
      <AmountLabel
        amount={price.price_amount}
        currency={price.price_currency}
        interval={
          isLegacyRecurringPrice(price)
            ? price.recurring_interval
            : product.recurring_interval
        }
        intervalCount={product.recurring_interval_count}
        mode={mode}
        locale={locale}
      />
    )
  } else if (price.amount_type === 'custom') {
    return (
      <div className="text-[min(1em,24px)]">
        {t('checkout.pricing.payWhatYouWant')}
      </div>
    )
  } else if (price.amount_type === 'free') {
    return (
      <div className="text-[min(1em,24px)]">{t('checkout.pricing.free')}</div>
    )
  }
}

export default ProductPriceLabel

'use client'

import { isLegacyRecurringPrice } from '@/utils/product'
import { schemas } from '@polar-sh/client'
import { Button } from '@polar-sh/orbit'
import { useCallback, useMemo, useState } from 'react'
import { useFieldArray, useFormContext } from 'react-hook-form'
import { Section } from '../../Layout/Section'
import { CurrencyTabs } from './Pricing/CurrencyTabs'
import { ProductPriceItem } from './Pricing/ProductPriceItem'
import { useAutoSwitchToErroredPriceTab } from './Pricing/useAutoSwitchToErroredPriceTab'
import {
  getActiveCurrencies,
  groupPricesByCurrency,
  hasPriceCurrency,
  ProductPrice,
  ProductPriceCreate,
} from './Pricing/utils'
import { ProductFormType } from './ProductForm'

export interface ProductPricingSectionProps {
  organization: schemas['Organization']
  className?: string
  update?: boolean
  compact?: boolean
}

export const ProductPricingSection = ({
  organization,
  className,
  update,
  compact,
}: ProductPricingSectionProps) => {
  const { control, setValue, getValues } =
    useFormContext<ProductFormType>()

  const pricesFieldArray = useFieldArray({
    control,
    name: 'prices',
  })

  const { fields: prices, append, remove } = pricesFieldArray

  const defaultCurrency = organization.default_presentment_currency

  const [selectedCurrency, setSelectedCurrency] =
    useState<string>(defaultCurrency)

  const activeCurrencies = useMemo(() => {
    const currencies = getActiveCurrencies(prices as ProductFormType['prices'])
    if (!currencies.includes(defaultCurrency)) {
      return [defaultCurrency, ...currencies]
    }
    return [defaultCurrency, ...currencies.filter((c) => c !== defaultCurrency)]
  }, [prices, defaultCurrency])

  const validatedSelectedCurrency = activeCurrencies.includes(selectedCurrency)
    ? selectedCurrency
    : defaultCurrency

  useAutoSwitchToErroredPriceTab(setSelectedCurrency)

  const isLegacyRecurringProduct = useMemo(
    () => (prices as ProductPrice[]).some(isLegacyRecurringPrice),
    [prices],
  )

  const pricesByCurrency = useMemo(
    () => groupPricesByCurrency(prices as ProductFormType['prices']),
    [prices],
  )

  const pricesForSelectedCurrency = useMemo(
    () => pricesByCurrency.get(validatedSelectedCurrency) || [],
    [pricesByCurrency, validatedSelectedCurrency],
  )

  const handleAmountTypeChange = useCallback(
    (
      changedIndex: number,
      newAmountType: ProductPriceCreate['amount_type'],
    ) => {
      const currentPrices = getValues('prices')
      if (!currentPrices) return
      const changedPrice = currentPrices[changedIndex]
      if (!hasPriceCurrency(changedPrice)) return
      const changedCurrency = changedPrice.price_currency

      const pricesByCurr = groupPricesByCurrency(currentPrices)
      const changedCurrencyPrices = pricesByCurr.get(changedCurrency) || []
      const positionInCurrency = changedCurrencyPrices.findIndex(
        (p) => p.index === changedIndex,
      )

      const createPriceForCurrency = (currency: string): ProductPriceCreate => {
        const base = {
          price_currency: currency as schemas['PresentmentCurrency'],
        }
        if (newAmountType === 'fixed') {
          return { ...base, amount_type: 'fixed', price_amount: 0 }
        } else if (newAmountType === 'custom') {
          return { ...base, amount_type: 'custom', minimum_amount: 0 }
        } else if (newAmountType === 'free') {
          return { ...base, amount_type: 'free' }
        }
        return { ...base, amount_type: 'free' }
      }

      setValue(
        `prices.${changedIndex}`,
        createPriceForCurrency(changedCurrency),
      )
      setValue(`prices.${changedIndex}.id`, '')

      pricesByCurr.forEach((currencyPrices, currency) => {
        if (currency === changedCurrency) return
        if (positionInCurrency < currencyPrices.length) {
          const correspondingPrice = currencyPrices[positionInCurrency]
          setValue(
            `prices.${correspondingPrice.index}`,
            createPriceForCurrency(currency),
          )
          setValue(`prices.${correspondingPrice.index}.id`, '')
        }
      })
    },
    [getValues, setValue],
  )

  const handleAddCurrency = useCallback(
    (newCurrency: string) => {
      const currentPrices = getValues('prices')
      if (!currentPrices) return
      const defaultCurrencyPrices = currentPrices.filter(
        (p) => hasPriceCurrency(p) && p.price_currency === defaultCurrency,
      )

      defaultCurrencyPrices.forEach((price) => {
        if (!('amount_type' in price)) return

        let newPrice: ProductPriceCreate
        const baseCurrency = {
          price_currency: newCurrency as schemas['PresentmentCurrency'],
        }

        if (price.amount_type === 'fixed') {
          newPrice = { ...baseCurrency, amount_type: 'fixed', price_amount: 0 }
        } else if (price.amount_type === 'custom') {
          newPrice = {
            ...baseCurrency,
            amount_type: 'custom',
            minimum_amount: 0,
          }
        } else if (price.amount_type === 'free') {
          newPrice = { ...baseCurrency, amount_type: 'free' }
        } else {
          newPrice = { ...baseCurrency, amount_type: 'free' }
        }

        append(newPrice)
      })

      setSelectedCurrency(newCurrency)
    },
    [getValues, defaultCurrency, append],
  )

  const handleRemoveCurrency = useCallback(
    (currencyToRemove: string) => {
      if (currencyToRemove === defaultCurrency) return

      const currentPrices = getValues('prices')
      if (!currentPrices) return
      const indicesToRemove = currentPrices
        .map((p, i) =>
          hasPriceCurrency(p) && p.price_currency === currencyToRemove ? i : -1,
        )
        .filter((i) => i !== -1)
        .reverse()

      indicesToRemove.forEach((i) => remove(i))
      setSelectedCurrency(defaultCurrency)
    },
    [getValues, defaultCurrency, remove],
  )

  const handleRemovePrice = useCallback(
    (indexToRemove: number) => {
      remove(indexToRemove)
    },
    [remove],
  )

  if (isLegacyRecurringProduct) {
    return (
      <Section
        title="Pricing"
        description="Set your billing cycle and pricing model"
        className={className}
        compact={compact}
      >
        <div className="prose dark:bg-polar-700 dark:text-polar-500 rounded-2xl bg-gray-100 p-6 text-sm text-gray-500">
          <p>
            This product uses a deprecated pricing model with both a monthly and
            yearly pricing.
          </p>
          <p>
            To better support future pricing model, the billing cycle is now set
            at the product level, meaning you need to create a separate product
            for each billing cycle.
          </p>
          <p>
            If you want to make any changes to the pricing model, you need to
            create a new product. Feel free to reach out to our support team if
            you need assistance.
          </p>
        </div>
      </Section>
    )
  }

  return (
    <Section
      title="Pricing"
      description="Set your billing cycle and pricing model"
      className={className}
      compact={compact}
    >
      <div className="dark:divide-polar-700 flex w-full flex-col divide-y divide-gray-200">
        <CurrencyTabs
          activeCurrencies={activeCurrencies}
          selectedCurrency={validatedSelectedCurrency}
          onSelectCurrency={setSelectedCurrency}
          onAddCurrency={handleAddCurrency}
          onRemoveCurrency={handleRemoveCurrency}
          defaultCurrency={defaultCurrency}
        />

        <div className="flex flex-col gap-y-6 py-6">
          <h3>Price Type</h3>
          {pricesForSelectedCurrency.map(({ price, index }) => (
            <div key={`${selectedCurrency}-${index}`}>
              <ProductPriceItem
                organization={organization}
                index={index}
                currency={validatedSelectedCurrency}
                onRemove={handleRemovePrice}
                onAmountTypeChange={handleAmountTypeChange}
                canRemove={pricesForSelectedCurrency.length > 1}
                key={`${selectedCurrency}-${index}`}
              />
            </div>
          ))}
        </div>

      </div>
    </Section>
  )
}

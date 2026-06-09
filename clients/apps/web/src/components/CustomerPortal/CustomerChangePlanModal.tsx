'use client'

import { InlineModalHeader } from '@/components/Modal/InlineModal'
import { useCustomerUpdateSubscription } from '@/hooks/queries/customerPortal'
import { hasLegacyRecurringPrices } from '@/utils/product'
import { formatTrialEnd, useTrialChangeOutcome } from '@/utils/trial-change'
import { Client, schemas } from '@polar-sh/client'
import { Button } from '@polar-sh/orbit'
import { List, ListItem } from '@polar-sh/ui/components/atoms/List'
import { Checkbox } from '@polar-sh/ui/components/ui/checkbox'
import { useRouter } from 'next/navigation'
import { useCallback, useMemo, useState } from 'react'
import ProductPriceLabel from '../Products/ProductPriceLabel'
import { toast } from '../Toast/use-toast'

const ProductPriceListItem = ({
  product,
  currency,
  selected,
  onSelect,
}: {
  product: schemas['CustomerProduct']
  currency: string
  selected: boolean
  onSelect?: () => void
}) => {
  return (
    <ListItem
      selected={selected}
      className="flex flex-row items-center justify-between text-sm"
      onSelect={onSelect}
      size="small"
    >
      <h3 className="font-medium">{product.name}</h3>
      <ProductPriceLabel product={product} currency={currency} />
    </ListItem>
  )
}

const CustomerChangePlanModal = ({
  api,
  organization,
  products: _products,
  subscription,
  hide,
  onUserSubscriptionUpdate,
}: {
  api: Client
  organization: schemas['CustomerOrganization']
  products: schemas['CustomerProduct'][]
  subscription: schemas['CustomerSubscription']
  hide: () => void
  onUserSubscriptionUpdate: (
    subscription: schemas['CustomerSubscription'],
  ) => void
}) => {
  const router = useRouter()
  const products = useMemo(
    () =>
      _products.filter((p) => p.is_recurring && !hasLegacyRecurringPrices(p)),
    [_products],
  )

  const [selectedProduct, setSelectedProduct] = useState<
    schemas['CustomerProduct'] | null
  >(null)

  const needToAddPaymentMethod = false

  const trialOutcome = useTrialChangeOutcome(subscription, selectedProduct)

  const isTrialing = subscription.status === 'trialing'

  const [willTriggerImmediateCycle, nextInvoiceType] = useMemo(():
    | [false, null]
    | [true, 'charge' | 'credit'] => {
    if (!selectedProduct) return [false, null]
    if (isTrialing) return [false, null]

    const willTrigger =
      selectedProduct.recurring_interval !==
      subscription.product.recurring_interval

    if (!willTrigger) return [false, null]

    const newPrice = selectedProduct.prices.reduce((acc, price) => {
      if (price.amount_type === 'fixed') {
        return acc + price.price_amount
      }

      return acc
    }, 0)

    const currentPrice = subscription.amount

    const chargeOrCredit = newPrice > currentPrice ? 'charge' : 'credit'

    return [willTrigger, chargeOrCredit]
  }, [selectedProduct, subscription, isTrialing])

  const invoicingMessage = useMemo(() => {
    if (!selectedProduct) return null

    if (trialOutcome?.kind === 'continues') {
      return `Your trial will continue until ${formatTrialEnd(trialOutcome.trialEnd)}. You won't be charged before then.`
    }

    if (trialOutcome?.kind === 'ends') {
      return `This will end my trial and charge me immediately for ${selectedProduct.name}.`
    }

    if (willTriggerImmediateCycle) {
      const newPeriod =
        selectedProduct.recurring_interval === 'month' ? 'monthly' : 'yearly'

      if (nextInvoiceType === 'charge') {
        return `I'll be charged immediately for the new ${newPeriod} plan.`
      } else {
        return `My previous payment will appear as a credit on my next invoice.`
      }
    }

    return 'Your next invoice will include the new plan plus the proration for the current month.'
  }, [
    selectedProduct,
    willTriggerImmediateCycle,
    nextInvoiceType,
    trialOutcome,
  ])

  const willIssueInvoice =
    trialOutcome?.kind === 'ends' || willTriggerImmediateCycle
  const [approveImmediateInvoice, setApproveImmediateInvoice] = useState(false)

  const canChangePlan = useMemo(() => {
    if (!selectedProduct) return false
    const isSamePlan = selectedProduct?.id === subscription.product_id
    if (isSamePlan) return false

    if (willIssueInvoice && !approveImmediateInvoice) return false

    const selectedPlanIsFree = selectedProduct?.prices.some(
      (p) => p.amount_type === 'free',
    )

    if (selectedPlanIsFree) return true

    return true
  }, [selectedProduct, subscription, willIssueInvoice, approveImmediateInvoice])

  const updateSubscription = useCustomerUpdateSubscription(api)
  const onConfirm = useCallback(async () => {
    if (!selectedProduct) return
    const { data, error } = await updateSubscription.mutateAsync({
      id: subscription.id,
      body: {
        product_id: selectedProduct.id,
      },
    })
    if (error) {
      const errorMessage =
        typeof error.detail === 'string'
          ? error.detail
          : 'Failed to update subscription'
      toast({
        title: 'Error updating subscription',
        description: errorMessage,
        variant: 'error',
      })
    }
    if (data) {
      toast({
        title: 'Subscription Updated',
        description: `Subscription was updated successfully`,
      })
      onUserSubscriptionUpdate(data)
      router.refresh()
      hide()
    }
  }, [
    updateSubscription,
    selectedProduct,
    subscription,
    onUserSubscriptionUpdate,
    hide,
    router,
  ])

  const availableProducts = useMemo(
    () =>
      products
        .filter((product) => product.id !== subscription.product_id)
        .sort((a, b) =>
          a.name.localeCompare(b.name, 'en-US', { numeric: true }),
        ),
    [products, subscription],
  )

  return (
    <div className="flex flex-col overflow-y-auto">
      <InlineModalHeader hide={hide}>
        <div className="flex items-center justify-between gap-2">
          <h2 className="text-xl">Change Plan</h2>
        </div>
      </InlineModalHeader>
      <div className="flex flex-col gap-y-8 p-8">
        <h3 className="font-medium">Current Plan</h3>
        <List size="small">
          <ProductPriceListItem
            product={subscription.product}
            currency={subscription.currency}
            selected
          />
        </List>
        <h3 className="font-medium">Available Plans</h3>
        {availableProducts.length === 0 ? (
          <p className="dark:text-polar-500 dark:bg-polar-800 rounded-2xl bg-gray-50 p-3 text-center text-sm text-gray-500">
            No other plans available
          </p>
        ) : (
          <List size="small">
            {availableProducts.map((product) => (
              <ProductPriceListItem
                key={product.id}
                product={product}
                currency={subscription.currency}
                selected={selectedProduct?.id === product.id}
                onSelect={() => setSelectedProduct(product)}
              />
            ))}
          </List>
        )}
        <div className="flex flex-col gap-y-6">
          {invoicingMessage && (
            <label className="flex flex-row items-start gap-x-2">
              {willIssueInvoice && (
                <div>
                  <Checkbox
                    checked={approveImmediateInvoice}
                    onCheckedChange={(checked) =>
                      setApproveImmediateInvoice(checked === true)
                    }
                  />
                </div>
              )}

              <span className="dark:text-polar-500 text-sm text-pretty text-gray-500">
                {invoicingMessage}
              </span>
            </label>
          )}
        </div>
        {needToAddPaymentMethod && (
          <p className="dark:text-polar-500 text-sm text-gray-500">
            You need to add a payment method before updating your plan. Head to
            the Customer Portal Settings to add a payment method.
          </p>
        )}
        <Button
          disabled={!canChangePlan}
          loading={updateSubscription.isPending}
          onClick={onConfirm}
          size="lg"
        >
          {trialOutcome?.kind === 'ends'
            ? 'Change Plan & End Trial'
            : 'Change Plan'}
        </Button>
      </div>
    </div>
  )
}

export default CustomerChangePlanModal

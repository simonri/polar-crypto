'use client'

import { DISTINCT_ID_COOKIE } from '@/experiments/constants'
import { useCheckoutConfirmedRedirect } from '@/hooks/checkout'
import { usePostHog } from '@/hooks/posthog'
import { useOrganizationPaymentStatus } from '@/hooks/queries/org'
import { getServerURL } from '@/utils/api'
import { ArrowLeft } from 'lucide-react'
import {
  CheckoutForm,
  CheckoutHeroPrice,
  CheckoutPricingBreakdown,
  CheckoutPWYWForm,
} from '@polar-sh/checkout/components'
import { hasProductCheckout } from '@polar-sh/checkout/guards'
import { useCheckoutFulfillmentListener } from '@polar-sh/checkout/hooks'
import { useCheckout, useCheckoutForm } from '@polar-sh/checkout/providers'
import { ClientResponseError, type schemas } from '@polar-sh/client'
import { AcceptedLocale, useTranslations } from '@polar-sh/i18n'
import Alert from '@polar-sh/ui/components/atoms/Alert'
import ShadowBox from '@polar-sh/ui/components/atoms/ShadowBox'
import { getThemePreset } from '@polar-sh/ui/hooks/theming'
import { useTheme } from '@/providers/theme'
import Link from 'next/link'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { CheckoutDiscountInput } from './CheckoutDiscountInput'
import { CheckoutProductDescription } from './CheckoutProductDescription'
import { twMerge } from 'tailwind-merge'
import { useCheckoutClientSSE } from '@/hooks/sse'
import { Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import {
  CryptoPaymentPanel,
  CryptoTokenIcon,
  parseAcceptedCurrencies,
  readPersistedCurrency,
} from './CryptoCheckout'

// Tell people the payment rail before they commit their email, with the
// coins' own logos so it reads as a real, recognizable payment method
// rather than a line of text. Nobody should discover "crypto only" after
// pressing the button.
const AcceptedCryptoHint = ({
  currencies,
  label,
}: {
  currencies: string[]
  label: string
}) => {
  if (currencies.length === 0) return null
  return (
    <Box display="flex" alignItems="center" columnGap="s">
      <Box display="flex" alignItems="center" columnGap="xs">
        {currencies.map((c) => (
          <CryptoTokenIcon key={c} token={c} />
        ))}
      </Box>
      <Text variant="caption" color="muted">
        {label}
      </Text>
    </Box>
  )
}

const PaymentNotReadyBanner = ({
  organizationStatus,
  organizationName,
}: {
  organizationStatus: string | undefined
  organizationName: string
}) => {
  const isTestMode = organizationStatus === 'created'

  return (
    <Alert color={isTestMode ? 'gray' : 'red'}>
      <div className="flex flex-col gap-y-1 p-2">
        <div
          className={twMerge(
            'text-sm font-medium',
            isTestMode ? 'text-black dark:text-white' : '',
          )}
        >
          {isTestMode
            ? `${organizationName} is in test mode`
            : 'Payments are currently unavailable'}
        </div>
        <div className="text-sm">
          {isTestMode
            ? `You can test checkout with free products or 100% discount orders.`
            : `${organizationName} doesn't allow payments.`}
        </div>
      </div>
    </Alert>
  )
}

export interface CheckoutProps {
  embed?: boolean
  theme?: 'light' | 'dark'
  locale?: AcceptedLocale
}

const belowSubmitText =
  process.env.NEXT_PUBLIC_CHECKOUT_BELOW_SUBMIT_TEXT || null

// Renders text with an optional inline markdown link: "prefix [label](url) suffix"
const BelowSubmitText = ({ text }: { text: string }) => {
  const match = text.match(/^(.*?)\[([^\]]+)\]\(([^)]+)\)(.*)$/)
  if (!match) {
    return (
      <p className="dark:text-polar-500 text-center text-xs text-gray-500">
        {text}
      </p>
    )
  }
  const [, prefix, label, href, suffix] = match
  return (
    <p className="dark:text-polar-500 text-center text-xs text-gray-500">
      {prefix}
      <a href={href} target="_blank" rel="noreferrer" className="underline">
        {label}
      </a>
      {suffix}
    </p>
  )
}

const Checkout = ({
  embed: _embed,
  theme: _theme,
  locale: _locale,
}: CheckoutProps) => {
  const { client } = useCheckout()
  const {
    checkout,
    form,
    update: _update,
    confirm: _confirm,
    loading: confirmLoading,
    loadingLabel,
    isUpdatePending,
  } = useCheckoutForm()
  const embed = _embed === true
  const { resolvedTheme } = useTheme()
  const theme = _theme || (resolvedTheme as 'light' | 'dark')
  const locale: AcceptedLocale = _locale || 'en'
  const t = useTranslations(locale)
  const posthog = usePostHog()

  const openedTrackedRef = useRef(false)
  useEffect(() => {
    if (openedTrackedRef.current) return
    openedTrackedRef.current = true

    posthog.capture('storefront:checkout:page:view')

    const cookies = document.cookie.split(';')
    const distinctIdCookie = cookies.find((c) =>
      c.trim().startsWith(`${DISTINCT_ID_COOKIE}=`),
    )
    const distinctId = distinctIdCookie?.split('=')[1]?.trim()

    fetch(
      getServerURL(`/v1/checkouts/client/${checkout.client_secret}/opened`),
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ distinct_id: distinctId }),
      },
    ).catch(() => {
      // Silently ignore - don't affect checkout experience
    })
  }, [checkout.client_secret, posthog])

  const themePreset = getThemePreset(theme)

  const { data: paymentStatus } = useOrganizationPaymentStatus(
    checkout.organization.id,
  )

  const isPaymentReady = paymentStatus?.payment_ready ?? true // Default to true while loading
  const shouldBlockCheckout = !isPaymentReady
  const disableCheckout =
    shouldBlockCheckout &&
    (paymentStatus?.organization_status === 'denied' ||
      checkout.is_payment_required)

  // Track payment not ready state
  useEffect(() => {
    if (shouldBlockCheckout && paymentStatus) {
      posthog.capture('storefront:subscriptions:payment_not_ready:view', {
        organization_slug: checkout.organization.slug,
        organization_status: paymentStatus?.organization_status,
        product_id: checkout.product_id,
      })
    }
  }, [
    paymentStatus,
    shouldBlockCheckout,
    checkout.organization.slug,
    paymentStatus?.organization_status,
    checkout.product_id,
    posthog,
  ])

  const [fullLoading, setFullLoading] = useState(false)
  const [cryptoPendingCheckout, setCryptoPendingCheckout] = useState<
    schemas['CheckoutPublicConfirmed'] | null
  >(null)
  // Currencies the server will actually create addresses for. Never hard-code
  // this list client-side: an unavailable pick renders an empty payment pane.
  const acceptedCurrencies = useMemo(
    () =>
      parseAcceptedCurrencies(
        checkout.payment_processor_metadata?.accepted_currencies,
      ),
    [checkout.payment_processor_metadata?.accepted_currencies],
  )
  const [selectedCurrency, setSelectedCurrency] = useState<string>(() => {
    const persisted =
      typeof window !== 'undefined'
        ? readPersistedCurrency(checkout.client_secret)
        : null
    if (persisted && acceptedCurrencies.includes(persisted)) return persisted
    return acceptedCurrencies[0] ?? 'BTC'
  })
  const checkoutEvents = useCheckoutClientSSE(checkout.client_secret)
  const loading = useMemo(
    () => confirmLoading || fullLoading,
    [confirmLoading, fullLoading],
  )
  const [listenFulfillment, fullfillmentLabel] = useCheckoutFulfillmentListener(
    client,
    checkout,
  )
  const label = useMemo(
    () => fullfillmentLabel || loadingLabel,
    [fullfillmentLabel, loadingLabel],
  )
  const checkoutConfirmedRedirect = useCheckoutConfirmedRedirect(
    embed,
    theme,
    listenFulfillment,
  )

  const update = useCallback(
    async (data: schemas['CheckoutUpdatePublic']) => {
      try {
        return await _update(data)
      } catch (error) {
        if (
          error instanceof ClientResponseError &&
          error.response.status === 410
        ) {
          window.location.reload()
        }
        throw error
      }
    },
    [_update],
  )

  const confirm = useCallback(
    async (data: schemas['CheckoutConfirm']) => {
      setFullLoading(true)
      let confirmedCheckout: schemas['CheckoutPublicConfirmed']
      try {
        confirmedCheckout = await _confirm(data)
      } catch (error) {
        if (
          error instanceof ClientResponseError &&
          error.response.status === 410
        ) {
          window.location.reload()
        }
        setFullLoading(false)
        throw error
      }

      if (confirmedCheckout.payment_processor === 'crypto') {
        setCryptoPendingCheckout(confirmedCheckout)
        setFullLoading(false)
        return confirmedCheckout
      }

      await checkoutConfirmedRedirect(
        confirmedCheckout,
        confirmedCheckout.customer_session_token,
      )

      return confirmedCheckout
    },
    [_confirm, checkoutConfirmedRedirect],
  )

  const onCryptoConfirmed = useCallback(async () => {
    if (!cryptoPendingCheckout) return
    await checkoutConfirmedRedirect(
      cryptoPendingCheckout,
      cryptoPendingCheckout.customer_session_token,
    )
    setCryptoPendingCheckout(null)
  }, [cryptoPendingCheckout, checkoutConfirmedRedirect])

  const cryptoPaymentView = cryptoPendingCheckout ? (
    <CryptoPaymentPanel
      clientSecret={cryptoPendingCheckout.client_secret}
      acceptedCurrencies={acceptedCurrencies}
      initialCurrency={selectedCurrency}
      locale={locale}
      onConfirmed={onCryptoConfirmed}
      onCurrencyChange={setSelectedCurrency}
      events={checkoutEvents}
    />
  ) : null

  if (embed) {
    return (
      <ShadowBox className="dark:md:bg-polar-900 flex flex-col gap-y-12 divide-gray-200 overflow-hidden rounded-3xl md:bg-white dark:divide-transparent">
        {shouldBlockCheckout && (
          <PaymentNotReadyBanner
            organizationStatus={paymentStatus?.organization_status}
            organizationName={checkout.organization.name}
          />
        )}
        {!cryptoPendingCheckout &&
          hasProductCheckout(checkout) &&
          checkout.product_price.amount_type === 'custom' &&
          !checkout.amount && (
            <CheckoutPWYWForm
              checkout={checkout}
              update={update}
              productPrice={
                checkout.product_price as schemas['ProductPriceCustom']
              }
              locale={locale}
            />
          )}
        {cryptoPendingCheckout ? (
          cryptoPaymentView
        ) : (
          <CheckoutForm
            form={form}
            checkout={checkout}
            update={update}
            confirm={confirm}
            loading={loading}
            loadingLabel={label}
            theme={theme}
            themePreset={themePreset}
            disabled={disableCheckout}
            isUpdatePending={isUpdatePending}
            locale={locale}
            beforeSubmit={
              <div className="flex flex-col gap-4">
                {hasProductCheckout(checkout) &&
                  !checkout.is_free_product_price && (
                    <>
                      <CheckoutPricingBreakdown
                        checkout={checkout}
                        locale={locale}
                      />
                      <CheckoutDiscountInput
                        checkout={checkout}
                        update={update}
                        locale={locale}
                      />
                    </>
                  )}
                {checkout.payment_processor === 'crypto' && (
                  <AcceptedCryptoHint
                    currencies={acceptedCurrencies}
                    label={t('checkout.crypto.acceptedHint')}
                  />
                )}
              </div>
            }
            afterSubmit={
              belowSubmitText ? (
                <p className="dark:text-polar-500 text-center text-xs text-gray-500">
                  {belowSubmitText}
                </p>
              ) : undefined
            }
          />
        )}
      </ShadowBox>
    )
  }

  const orgHeader = (
    <div className="flex flex-row items-center gap-x-3">
      {checkout.return_url && (
        <Link
          href={checkout.return_url}
          className="dark:text-polar-500 shrink-0 text-gray-600"
        >
          <ArrowLeft size={20} />
        </Link>
      )}
      <span className="text-base font-medium dark:text-white">
        {checkout.organization.name}
      </span>
    </div>
  )

  return (
    <div className="md:grid md:min-h-screen md:grid-cols-2">
      <div className="md:flex md:items-center md:justify-end">
        <div className="mx-auto flex w-full max-w-[480px] flex-col gap-y-8 px-4 py-6 md:mx-0 md:py-12 md:pr-12 md:pl-4">
          {orgHeader}
          <div className="flex flex-col gap-y-8 md:sticky md:top-8">
            {hasProductCheckout(checkout) && (
              <>
                <div className="flex flex-col gap-y-2">
                  <span className="text-sm font-medium text-gray-900 dark:text-white">
                    {checkout.product.name}
                  </span>
                  <span className="text-3xl font-medium">
                    <CheckoutHeroPrice checkout={checkout} locale={locale} />
                  </span>
                </div>
                {!cryptoPendingCheckout &&
                  checkout.product_price.amount_type === 'custom' &&
                  !checkout.amount && (
                    <CheckoutPWYWForm
                      checkout={checkout}
                      update={update}
                      productPrice={
                        checkout.product_price as schemas['ProductPriceCustom']
                      }
                      locale={locale}
                    />
                  )}
                {!checkout.is_free_product_price && (
                  <div className="flex flex-col gap-4 text-sm">
                    <CheckoutPricingBreakdown
                      checkout={checkout}
                      locale={locale}
                    />
                    {/* The order is locked once confirmed; editing would 403. */}
                    {!cryptoPendingCheckout && (
                      <CheckoutDiscountInput
                        checkout={checkout}
                        update={update}
                        locale={locale}
                        collapsible
                      />
                    )}
                  </div>
                )}
                {checkout.product.description && (
                  <CheckoutProductDescription
                    description={checkout.product.description}
                    productName={checkout.product.name}
                    locale={locale}
                  />
                )}
              </>
            )}
          </div>
        </div>
      </div>
      <div className="dark:md:bg-polar-900 md:flex md:items-center md:bg-white">
        <Box
          display="flex"
          flexDirection="column"
          rowGap="xl"
          width="100%"
          maxWidth={480}
          marginHorizontal="auto"
          padding={{ base: 'l', md: '2xl' }}
          marginVertical={{ base: 'none', md: '2xl' }}
          borderRadius={{ base: 'none', md: 'l' }}
          borderWidth={{ base: 0, md: 1 }}
          borderStyle="solid"
          borderColor="border-primary"
          backgroundColor={{
            base: 'background-primary',
            md: 'background-card',
          }}
        >
          {shouldBlockCheckout && (
            <PaymentNotReadyBanner
              organizationStatus={paymentStatus?.organization_status}
              organizationName={checkout.organization.name}
            />
          )}
          {cryptoPendingCheckout ? (
            cryptoPaymentView
          ) : (
            <CheckoutForm
              form={form}
              checkout={checkout}
              update={update}
              confirm={confirm}
              loading={loading}
              loadingLabel={label}
              theme={theme}
              themePreset={themePreset}
              disabled={disableCheckout}
              isUpdatePending={isUpdatePending}
              locale={locale}
              beforeSubmit={
                checkout.payment_processor === 'crypto' ? (
                  <AcceptedCryptoHint
                    currencies={acceptedCurrencies}
                    label={t('checkout.crypto.acceptedHint')}
                  />
                ) : undefined
              }
              afterSubmit={
                belowSubmitText ? (
                  <BelowSubmitText text={belowSubmitText} />
                ) : undefined
              }
            />
          )}
        </Box>
      </div>
    </div>
  )
}

export default Checkout

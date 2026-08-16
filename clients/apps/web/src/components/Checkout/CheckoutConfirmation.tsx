'use client'

import { useCheckoutConfirmedRedirect } from '@/hooks/checkout'
import { useCheckoutClientSSE } from '@/hooks/sse'
import { getServerURL } from '@/utils/api'
import { hasProductCheckout } from '@polar-sh/checkout/guards'
import { createClient, unwrap, type schemas } from '@polar-sh/client'
import {
  DEFAULT_LOCALE,
  useTranslations,
  type AcceptedLocale,
} from '@polar-sh/i18n'
import ShadowBox from '@polar-sh/ui/components/atoms/ShadowBox'
import { useRouter } from 'next/navigation'
import { useCallback, useEffect, useMemo, useState } from 'react'
import LogoType from '../Brand/logos/LogoType'
import { CryptoPaymentPanel, parseAcceptedCurrencies } from './CryptoCheckout'

export interface CheckoutConfirmationProps {
  checkout: schemas['CheckoutPublic']
  embed: boolean
  theme?: 'light' | 'dark'
  locale?: AcceptedLocale
  customerSessionToken?: string
  disabled?: boolean
  maxWaitingTimeMs?: number
}

export const CheckoutConfirmation = ({
  checkout: _checkout,
  embed,
  theme,
  locale = DEFAULT_LOCALE,
  customerSessionToken,
  disabled,
  maxWaitingTimeMs = 15000,
}: CheckoutConfirmationProps) => {
  const t = useTranslations(locale)
  const router = useRouter()
  const client = useMemo(() => createClient(getServerURL()), [])
  const [checkout, setCheckout] = useState(_checkout)
  const { status } = checkout

  const updateCheckout = useCallback(async () => {
    try {
      const value = await unwrap(
        client.GET('/v1/checkouts/client/{client_secret}', {
          params: { path: { client_secret: checkout.client_secret } },
        }),
      )
      setCheckout(value)
    } catch {
      // Silently ignore - will retry on next interval/event
    }
  }, [client, checkout])
  const checkoutConfirmedRedirect = useCheckoutConfirmedRedirect(embed, theme)

  const checkoutEvents = useCheckoutClientSSE(checkout.client_secret)
  useEffect(() => {
    if (disabled) {
      return
    }

    if (status === 'open') {
      router.push(checkout.url)
      return
    }

    if (status === 'succeeded') {
      checkoutConfirmedRedirect(checkout, customerSessionToken)
      return
    }

    checkoutEvents.on('checkout.updated', updateCheckout)
    return () => {
      checkoutEvents.off('checkout.updated', updateCheckout)
    }
  }, [
    disabled,
    router,
    checkout,
    status,
    checkoutEvents,
    updateCheckout,
    checkoutConfirmedRedirect,
    customerSessionToken,
  ])

  useEffect(() => {
    if (checkout.status === 'open' || checkout.status === 'succeeded') {
      return
    }
    const intervalId = setInterval(() => updateCheckout(), maxWaitingTimeMs)
    return () => clearInterval(intervalId)
  }, [checkout.status, maxWaitingTimeMs, updateCheckout])

  return (
    <div className="flex min-h-screen items-center justify-center p-4">
      <ShadowBox className="flex w-full max-w-xl flex-col items-center justify-between gap-y-12 p-8 md:p-16">
        <div className="flex w-full max-w-md flex-col items-center gap-y-8 text-center">
          <h1 className="text-2xl font-medium">
            {status === 'succeeded' && t('checkout.confirmation.successTitle')}
            {status === 'failed' && t('checkout.confirmation.failedTitle')}
            {status === 'confirmed' && t('checkout.crypto.paymentTitle')}
          </h1>
          <p className="dark:text-polar-500 text-gray-500">
            {status === 'succeeded' &&
              hasProductCheckout(checkout) &&
              t('checkout.confirmation.successDescription', {
                product: checkout.product.name,
              })}
            {status === 'failed' &&
              t('checkout.confirmation.failedDescription')}
            {status === 'confirmed' &&
              hasProductCheckout(checkout) &&
              `${checkout.organization.name} · ${checkout.product.name}`}
          </p>
          {status === 'confirmed' && (
            <div className="w-full text-left">
              <CryptoPaymentPanel
                clientSecret={checkout.client_secret}
                acceptedCurrencies={parseAcceptedCurrencies(
                  checkout.payment_processor_metadata?.accepted_currencies,
                )}
                locale={locale}
                onConfirmed={updateCheckout}
              />
            </div>
          )}
          {status === 'succeeded' && (
            <p className="dark:text-polar-500 text-center text-xs text-gray-500">
              {t('checkout.footer.merchantOfRecord')}
            </p>
          )}
        </div>
        <div className="dark:text-polar-500 flex w-full flex-row items-center justify-center gap-x-3 text-sm text-gray-500">
          <span>{t('checkout.footer.poweredBy')}</span>
          <LogoType className="h-5" />
        </div>
      </ShadowBox>
    </div>
  )
}

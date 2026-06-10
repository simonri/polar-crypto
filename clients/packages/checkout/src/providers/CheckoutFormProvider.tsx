'use client'

import type { schemas } from '@polar-sh/client'

import {
  DEFAULT_LOCALE,
  useTranslations,
  type AcceptedLocale,
} from '@polar-sh/i18n'
import { createContext, useCallback, useContext, useState } from 'react'
import type { UseFormReturn } from 'react-hook-form'
import { useForm } from 'react-hook-form'
import { setValidationErrors } from '../utils/form'
import { useCheckout } from './CheckoutProvider'

const stub = (): never => {
  throw new Error(
    'You forgot to wrap your component in <CheckoutFormProvider>.',
  )
}

export interface CheckoutFormContextProps {
  checkout: schemas['CheckoutPublic']
  form: UseFormReturn<schemas['CheckoutUpdatePublic']>
  update: (
    data: schemas['CheckoutUpdatePublic'],
  ) => Promise<schemas['CheckoutPublic']>
  confirm: (
    data: schemas['CheckoutConfirm'],
  ) => Promise<schemas['CheckoutPublicConfirmed']>
  loading: boolean
  loadingLabel: string | undefined
  isUpdatePending: boolean
}

// @ts-expect-error - Allow to throw an error if the context is used without a provider
export const CheckoutFormContext = createContext<CheckoutFormContextProps>(stub)

export const CheckoutFormProvider = ({
  children,
  locale = DEFAULT_LOCALE,
}: React.PropsWithChildren<{ locale?: AcceptedLocale }>) => {
  const { checkout, update: updateOuter, confirm: confirmOuter } = useCheckout()
  const t = useTranslations(locale)
  const [loading, setLoading] = useState(false)
  const [loadingLabel, setLoadingLabel] = useState<string | undefined>()
  const [isUpdatePending, setIsUpdatePending] = useState(false)

  const savedEmail =
    typeof window !== 'undefined'
      ? (window.localStorage.getItem('polar_checkout_email') ?? undefined)
      : undefined

  const form = useForm<schemas['CheckoutUpdatePublic']>({
    defaultValues: {
      ...checkout,
      customer_email: checkout.customer_email ?? savedEmail,
      customer_billing_address: checkout.customer_billing_address as
        | schemas['AddressInput'] // We need to typecast here for some reason (it tries to match all_countries to supported_countries)
        | null,
      discount_code: checkout.discount ? checkout.discount.code : undefined,
      allow_trial: undefined,
    },
    shouldUnregister: true,
  })
  const { setError } = form

  const update = useCallback(
    async (
      checkoutUpdatePublic: schemas['CheckoutUpdatePublic'],
    ): Promise<schemas['CheckoutPublic']> => {
      setIsUpdatePending(true)
      const { ok, value, error } = await updateOuter(
        checkoutUpdatePublic,
      ).finally(() => {
        setIsUpdatePending(false)
      })
      if (ok) {
        return value
      } else {
        if (error) {
          switch (error.error) {
            case 'PolarRequestValidationError':
            case 'RequestValidationError':
              setValidationErrors(error.detail, setError)
              break
            case 'AlreadyActiveSubscriptionError':
            case 'NotOpenCheckout':
            case 'PaymentNotReady':
              setError('root', { message: error.detail })
              break
            case 'ResourceNotFound':
            case 'ExpiredCheckoutError':
              break
          }
        }
        throw error
      }
    },
    [updateOuter, setError],
  )

  const _confirm = useCallback(
    async (
      checkoutConfirm: schemas['CheckoutConfirm'],
    ): Promise<schemas['CheckoutPublicConfirmed']> => {
      const { ok, value, error } = await confirmOuter(checkoutConfirm)

      if (ok) {
        return value
      }

      if (error) {
        switch (error.error) {
          case 'PolarRequestValidationError':
          case 'RequestValidationError':
            setValidationErrors(error.detail, setError)
            break
          case 'PaymentError':
          case 'AlreadyActiveSubscriptionError':
          case 'NotOpenCheckout':
          case 'PaymentNotReady':
            setError('root', { message: error.detail })
            break
          case 'TrialAlreadyRedeemed':
            setError('root', { message: error.detail })
            await update({ allow_trial: false })
            break
          case 'ResourceNotFound':
          case 'ExpiredCheckoutError':
            break
        }
      }

      throw error
    },
    [confirmOuter, setError, update],
  )

  const confirm = useCallback(
    async (
      data: schemas['CheckoutConfirm'],
    ): Promise<schemas['CheckoutPublicConfirmed']> => {
      setLoading(true)
      setLoadingLabel(t('checkout.loading.processingOrder'))
      try {
        const checkoutConfirmed = await _confirm(data)
        const email = form.getValues('customer_email')
        if (email) {
          try {
            window.localStorage.setItem('polar_checkout_email', email)
          } catch {
            // localStorage unavailable (private browsing, storage full, etc.)
          }
        }
        return checkoutConfirmed
      } finally {
        setLoading(false)
      }
    },
    [_confirm, form, t],
  )

  return (
    <CheckoutFormContext.Provider
      value={{
        checkout,
        form,
        update,
        confirm,
        loading,
        loadingLabel,
        isUpdatePending,
      }}
    >
      {children}
    </CheckoutFormContext.Provider>
  )
}

export const useCheckoutForm = () => {
  return useContext(CheckoutFormContext)
}

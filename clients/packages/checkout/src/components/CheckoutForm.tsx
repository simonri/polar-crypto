'use client'

import type { schemas } from '@polar-sh/client'
import { useTranslations, type AcceptedLocale } from '@polar-sh/i18n'
import { Button, Input } from '@polar-sh/orbit'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@polar-sh/ui/components/ui/form'
import { ThemingPresetProps } from '@polar-sh/ui/hooks/theming'
import { useEffect, useMemo } from 'react'
import { UseFormReturn } from 'react-hook-form'
import { hasProductCheckout, isLegacyRecurringProductPrice } from '../guards'
import CustomFieldInput from './CustomFieldInput'

interface BaseCheckoutFormProps {
  form: UseFormReturn<schemas['CheckoutUpdatePublic']>
  checkout: schemas['CheckoutPublic']
  confirm: (
    data: schemas['CheckoutConfirm'],
  ) => Promise<schemas['CheckoutPublicConfirmed']>
  loading: boolean
  loadingLabel: string | undefined
  disabled?: boolean
  isUpdatePending?: boolean
  locale?: AcceptedLocale
  isWalletPayment?: boolean
  beforeSubmit?: React.ReactNode
}

const BaseCheckoutForm = ({
  form,
  checkout,
  confirm,
  loading,
  loadingLabel,
  disabled,
  isUpdatePending,
  children,
  locale: localeProp,
  beforeSubmit,
}: React.PropsWithChildren<BaseCheckoutFormProps>) => {
  const interval = hasProductCheckout(checkout)
    ? isLegacyRecurringProductPrice(checkout.product_price)
      ? checkout.product_price.recurring_interval
      : checkout.product.recurring_interval
    : null
  const {
    control,
    handleSubmit,
    watch,
    clearErrors,
    resetField,
    formState: { errors },
  } = form

  const discount = checkout.discount
  const isDiscountWithoutCode = discount && discount.code === null

  const locale: AcceptedLocale = localeProp || 'en'

  const t = useTranslations(locale)

  const discountCode = watch('discount_code')

  useEffect(() => {
    if (!discountCode && !checkout.discount) {
      clearErrors('discount_code')
    }
  }, [discountCode, checkout.discount, clearErrors])

  const onSubmit = async (data: schemas['CheckoutUpdatePublic']) => {
    // Don't send undefined/null data in the custom field object to please the SDK
    const cleanedFieldData = data.custom_field_data
      ? Object.fromEntries(
          Object.entries(data.custom_field_data).filter(
            ([, value]) => value !== undefined && value !== null,
          ),
        )
      : {}

    if (
      data.discount_code === '' ||
      // Avoid overwriting a programmatically set discount without a code.
      (!data.discount_code && isDiscountWithoutCode)
    ) {
      delete data.discount_code
    }

    await confirm({
      ...data,
      locale: localeProp,
      custom_field_data: cleanedFieldData,
    })
  }

  // Make sure to clear the discount code field if the discount is removed by the API
  useEffect(() => {
    if (!checkout.discount) {
      resetField('discount_code')
    }
  }, [checkout, resetField])

  const checkoutLabel = useMemo(() => {
    if (checkout.active_trial_interval) {
      return t('checkout.cta.startTrial')
    }

    if (checkout.is_payment_form_required) {
      return interval
        ? t('checkout.cta.subscribeNow')
        : t('checkout.cta.payNow')
    }

    return t('checkout.cta.getFree')
  }, [checkout, interval, t])

  return (
    <Form {...form}>
      <form
        onSubmit={handleSubmit(onSubmit)}
        className="flex flex-col gap-y-12"
      >
        <div className="flex flex-col gap-y-6">
          <FormField
            control={control}
            name="customer_email"
            rules={{
              required: t('checkout.form.fieldRequired'),
            }}
            render={({ field }) => (
              <FormItem>
                <FormLabel>{t('checkout.form.email')}</FormLabel>
                <FormControl>
                  <Input
                    type="email"
                    autoComplete="email"
                    {...field}
                    value={field.value || ''}
                    disabled={checkout.customer_id !== null}
                  />
                </FormControl>
                <FormMessage />
              </FormItem>
            )}
          />

          {children}

          {checkout.attached_custom_fields &&
            checkout.attached_custom_fields.map(
              ({ custom_field, required }) => (
                <FormField
                  key={custom_field.id}
                  control={control}
                  name={`custom_field_data.${custom_field.slug}`}
                  rules={{
                    required: required
                      ? t('checkout.form.fieldRequired')
                      : undefined,
                  }}
                  render={({ field }) => (
                    <CustomFieldInput
                      customField={custom_field}
                      required={required}
                      field={field}
                    />
                  )}
                />
              ),
            )}
        </div>
        {beforeSubmit}
        <div className="flex w-full flex-col items-center justify-center gap-y-2">
          <Button
            type="submit"
            size="lg"
            wrapperClassNames="text-base"
            className="w-full"
            disabled={disabled || isUpdatePending}
            loading={loading}
          >
            {checkoutLabel}
          </Button>
          {loading && loadingLabel && (
            <p className="dark:text-polar-500 text-sm text-gray-500">
              {loadingLabel}
            </p>
          )}
          {disabled && !loading && (
            <p className="text-sm text-red-500 dark:text-red-500">
              {t('checkout.cta.paymentsUnavailable')}
            </p>
          )}
          {errors.root && (
            <p className="text-destructive-foreground text-sm">
              {errors.root.message}
            </p>
          )}
        </div>
      </form>
    </Form>
  )
}

interface CheckoutFormProps {
  form: UseFormReturn<schemas['CheckoutUpdatePublic']>
  checkout: schemas['CheckoutPublic']
  update: (
    data: schemas['CheckoutUpdatePublic'],
  ) => Promise<schemas['CheckoutPublic']>
  confirm: (
    data: schemas['CheckoutConfirm'],
  ) => Promise<schemas['CheckoutPublicConfirmed']>
  loading: boolean
  loadingLabel: string | undefined
  disabled?: boolean
  isUpdatePending?: boolean
  theme?: 'light' | 'dark'
  themePreset: ThemingPresetProps
  locale?: AcceptedLocale
  beforeSubmit?: React.ReactNode
}

const CheckoutForm = (props: CheckoutFormProps) => {
  return <BaseCheckoutForm {...props} />
}

export default CheckoutForm

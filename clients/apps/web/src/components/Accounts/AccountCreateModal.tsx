'use client'

import { setValidationErrors } from '@/utils/api/errors'
import { api } from '@/utils/client'
import { isValidationError, schemas } from '@polar-sh/client'
import { Button } from '@polar-sh/orbit'
import {
  Form,
  FormControl,
  FormDescription,
  FormField,
  FormItem,
  FormLabel,
  FormMessage,
} from '@polar-sh/ui/components/ui/form'
import { useCallback, useState } from 'react'
import { useForm, useFormContext } from 'react-hook-form'

const COUNTRIES = [
  'US',
  'GB',
  'DE',
  'FR',
  'NL',
  'SE',
  'NO',
  'DK',
  'FI',
  'AU',
  'CA',
  'NZ',
  'SG',
  'JP',
  'BR',
  'IN',
  'MX',
  'AR',
  'ZA',
  'NG',
  'KE',
]

const AccountCreateModal = ({
  forOrganizationId,
}: {
  forOrganizationId: string
  returnPath: string
}) => {
  const form = useForm<schemas['PayoutAccountCreate']>({
    defaultValues: { country: 'US' },
  })

  const {
    handleSubmit,
    setError,
    formState: { errors },
  } = form

  const [loading, setLoading] = useState(false)
  const [created, setCreated] = useState(false)

  const onSubmit = useCallback(
    async (data: schemas['PayoutAccountCreate']) => {
      setLoading(true)

      const { error } = await api.POST('/v1/payout-accounts/', {
        body: {
          type: 'manual',
          country: data.country,
          organization_id: forOrganizationId,
        } as schemas['PayoutAccountCreate'],
      })

      setLoading(false)

      if (error) {
        if (isValidationError(error.detail)) {
          setValidationErrors(error.detail, setError)
        } else {
          setError('root', { message: error.detail })
        }
        return
      }

      setCreated(true)
    },
    [forOrganizationId, setError],
  )

  if (created) {
    return (
      <div className="flex flex-col gap-y-4 p-8">
        <h2>Payout account created</h2>
        <p className="text-sm text-gray-600">
          Your payout account is ready. Go to Finance settings to add your
          crypto wallet addresses for receiving payouts.
        </p>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-y-6 overflow-auto p-8">
      <h2>Setup payout account</h2>
      <p className="text-sm text-gray-600">
        Create a payout account to receive crypto payments. You can add your
        wallet addresses after account creation.
      </p>

      <Form {...form}>
        <form
          className="flex flex-col gap-y-4"
          onSubmit={handleSubmit(onSubmit)}
        >
          <FormField
            control={form.control}
            name="country"
            render={({ field }) => (
              <FormItem>
                <FormLabel>Country</FormLabel>
                <FormControl>
                  <select
                    value={field.value || 'US'}
                    onChange={field.onChange}
                    className="w-full rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
                  >
                    {COUNTRIES.map((c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ))}
                  </select>
                </FormControl>
                <FormDescription>
                  Select your country of residence or business tax residency.
                </FormDescription>
                <FormMessage />
              </FormItem>
            )}
          />

          {errors.root && (
            <p className="text-destructive-foreground text-sm">
              {errors.root.message}
            </p>
          )}
          <Button
            className="self-start"
            type="submit"
            loading={loading}
            disabled={loading}
          >
            Create account
          </Button>
        </form>
      </Form>
    </div>
  )
}

export default AccountCreateModal

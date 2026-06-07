import { useUpdateOrganization } from '@/hooks/queries'
import { useAutoSave } from '@/hooks/useAutoSave'
import { extractApiErrorMessage, setValidationErrors } from '@/utils/api/errors'
import { isValidationError, schemas } from '@polar-sh/client'
import Switch from '@polar-sh/ui/components/atoms/Switch'
import {
  Form,
  FormControl,
  FormField,
  FormItem,
  FormMessage,
} from '@polar-sh/ui/components/ui/form'
import React from 'react'
import { useForm } from 'react-hook-form'
import { toast } from '../Toast/use-toast'
import { SettingsGroup, SettingsGroupItem } from './SettingsGroup'

interface OrganizationSubscriptionSettingsProps {
  organization: schemas['Organization']
  readOnly: boolean
}

const OrganizationSubscriptionSettings: React.FC<
  OrganizationSubscriptionSettingsProps
> = ({ organization, readOnly }) => {
  const form = useForm<schemas['OrganizationSubscriptionSettings']>({
    defaultValues: organization.subscription_settings,
  })
  const { control, setError, reset } = form

  const updateOrganization = useUpdateOrganization()
  const onSave = async (
    subscription_settings: schemas['OrganizationSubscriptionSettings'],
  ) => {
    const { data, error } = await updateOrganization.mutateAsync({
      id: organization.id,
      body: {
        subscription_settings,
      },
    })

    if (error) {
      if (isValidationError(error.detail)) {
        setValidationErrors(error.detail, setError)
      } else {
        setError('root', { message: error.detail })
      }

      toast({
        title: 'Subscription Settings Update Failed',
        description: `Error updating subscription settings: ${extractApiErrorMessage(error)}`,
      })

      return
    }

    reset(data.subscription_settings)
  }

  useAutoSave({
    form,
    onSave,
    delay: 200,
  })

  return (
    <Form {...form}>
      <form
        onSubmit={(e) => {
          e.preventDefault()
        }}
      >
        <SettingsGroup>
          <SettingsGroupItem
            title="Allow customer updates"
            description="Customers can update their own subscription details."
          >
            <FormField
              control={control}
              name="allow_customer_updates"
              render={({ field }) => (
                <FormItem>
                  <FormControl>
                    <Switch
                      checked={field.value}
                      disabled={readOnly}
                      onCheckedChange={field.onChange}
                    />
                  </FormControl>

                  <FormMessage />
                </FormItem>
              )}
            />
          </SettingsGroupItem>
        </SettingsGroup>
      </form>
    </Form>
  )
}

export default OrganizationSubscriptionSettings

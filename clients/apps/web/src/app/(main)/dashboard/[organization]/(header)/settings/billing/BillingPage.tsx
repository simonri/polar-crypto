'use client'

import AccessRestricted from '@/components/Finance/AccessRestricted'
import { DashboardBody } from '@/components/Layout/DashboardLayout'
import { Modal } from '@/components/Modal'
import { useModal } from '@/components/Modal/useModal'
import { BillingAddressModal } from '@/components/Settings/Billing/BillingAddressModal'
import { BillingAddressSection } from '@/components/Settings/Billing/BillingAddressSection'
import { BillingOrdersTable } from '@/components/Settings/Billing/BillingOrdersTable'
import { BillingPaymentMethods } from '@/components/Settings/Billing/BillingPaymentMethods'
import { Section, SectionDescription } from '@/components/Settings/Section'
import { LoadingBox } from '@/components/Shared/LoadingBox'
import { toast } from '@/components/Toast/use-toast'
import { useHasPermission } from '@/hooks/permissions'
import {
  useOrganizationCustomerSession,
  useOrganizationOrders,
} from '@/hooks/queries/billing'

import { PolarEmbedPaymentMethod } from '@polar-sh/checkout/payment-method'
import { usePaymentMethodRedirectResult } from '@polar-sh/checkout/react/payment-method'
import { schemas } from '@polar-sh/client'
import { Box } from '@polar-sh/orbit/Box'
import { useQueryClient } from '@tanstack/react-query'
import { useTheme } from 'next-themes'
import { useState } from 'react'

export default function BillingPage({
  organization,
}: {
  organization: schemas['Organization']
}) {
  const queryClient = useQueryClient()
  const theme = useTheme()

  const canManageBilling = useHasPermission(
    organization.id,
    'organization:manage',
  )
  const gatedOrgId = canManageBilling ? organization.id : undefined

  const ordersQuery = useOrganizationOrders(gatedOrgId)
  const customerSessionQuery = useOrganizationCustomerSession(organization.id)

  const [addPaymentMethodError, setAddPaymentMethodError] = useState<
    string | null
  >(null)

  usePaymentMethodRedirectResult({
    onSuccess: () => toast({ title: 'Payment method added' }),
    onError: () =>
      setAddPaymentMethodError(
        'Could not add payment method. Please try again.',
      ),
  })

  const {
    isShown: isBillingAddressOpen,
    show: showBillingAddress,
    hide: hideBillingAddress,
  } = useModal()

  const onAddPaymentMethod = async () => {
    setAddPaymentMethodError(null)
    const session = customerSessionQuery.data
    if (!session) {
      toast({
        title: 'Could not start the payment method flow',
        description: 'Please try again in a moment.',
        variant: 'error',
      })
      return
    }
    const embed = await PolarEmbedPaymentMethod.create({
      sessionToken: session.token,
      theme: theme.resolvedTheme === 'dark' ? 'dark' : 'light',
    })
    embed.addEventListener('success', () => {
      queryClient.invalidateQueries({
        queryKey: ['organization-billing', organization.id, 'payment-methods'],
      })
      queryClient.invalidateQueries({
        queryKey: ['organization-billing', organization.id, 'customer-session'],
      })
    })
  }

  if (canManageBilling === false) {
    return (
      <DashboardBody
        wrapperClassName="max-w-(--breakpoint-md)!"
        title="Billing"
      >
        <AccessRestricted message="You don't have permission to manage billing for this organization. Ask an admin if you need access." />
      </DashboardBody>
    )
  }

  return (
    <DashboardBody wrapperClassName="max-w-(--breakpoint-md)!" title="Billing">
      <Box display="flex" flexDirection="column" rowGap="3xl">
        <Section id="payment-methods">
          <BillingPaymentMethods
            organizationId={organization.id}
            onAddPaymentMethod={onAddPaymentMethod}
            error={addPaymentMethodError}
          />
        </Section>

        <Section id="billing-address">
          <BillingAddressSection
            organizationId={organization.id}
            onEdit={showBillingAddress}
          />
        </Section>

        <Section id="orders">
          <SectionDescription
            title="Order history"
            description="Past invoices for your Polar subscription"
          />
          {ordersQuery.isLoading ? (
            <LoadingBox height={240} borderRadius="l" />
          ) : (
            <BillingOrdersTable
              organizationId={organization.id}
              orders={ordersQuery.data?.items ?? []}
            />
          )}
        </Section>
      </Box>

      <Modal
        title="Billing address"
        isShown={isBillingAddressOpen}
        hide={hideBillingAddress}
        modalContent={
          <BillingAddressModal
            organizationId={organization.id}
            hide={hideBillingAddress}
          />
        }
      />
    </DashboardBody>
  )
}

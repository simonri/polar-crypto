'use client'

import { usePortalAuthenticatedUser } from '@/hooks/queries/customerPortal'
import { createClientSideAPI } from '@/utils/client'
import { hasBillingPermission } from '@/utils/customerPortal'
import AllInclusiveOutlined from '@mui/icons-material/AllInclusiveOutlined'
import { schemas } from '@polar-sh/client'
import { CurrentPeriodOverview } from './CurrentPeriodOverview'
import {
  ActiveSubscriptionsOverview,
  InactiveSubscriptionsOverview,
} from './CustomerPortalSubscriptions'
import { EmptyState } from './EmptyState'
export interface CustomerPortalProps {
  organization: schemas['CustomerOrganization']
  products: schemas['CustomerProduct'][]
  subscriptions: schemas['CustomerSubscription'][]
  claimedSubscriptions: schemas['CustomerSubscription'][]
  customerSessionToken: string
}

export const CustomerPortalOverview = ({
  organization,
  products,
  subscriptions,
  customerSessionToken,
}: CustomerPortalProps) => {
  const api = createClientSideAPI(customerSessionToken)

  // Check if the user has billing permissions
  const { data: authenticatedUser } = usePortalAuthenticatedUser(api)
  const canManageBilling = hasBillingPermission(authenticatedUser)

  const activeOwnedSubscriptions = subscriptions.filter(
    (s) => s.status === 'active' || s.status === 'trialing',
  )
  const inactiveOwnedSubscriptions = subscriptions.filter(
    (s) => s.status !== 'active' && s.status !== 'trialing',
  )

  return (
    <div className="flex flex-col gap-y-12">
      {/* Billing sections - only visible to users with billing permissions */}
      {canManageBilling && activeOwnedSubscriptions.length > 0 && (
        <div className="flex flex-col gap-y-6">
          <div className="flex flex-col gap-y-4">
            {activeOwnedSubscriptions.map((s) => (
              <CurrentPeriodOverview
                key={s.id}
                products={products}
                subscription={s}
                api={api}
              />
            ))}
          </div>
          <ActiveSubscriptionsOverview
            api={api}
            organization={organization}
            products={products}
            subscriptions={activeOwnedSubscriptions}
            customerSessionToken={customerSessionToken}
          />
        </div>
      )}

      {/* Empty state */}
      {activeOwnedSubscriptions.length === 0 && (
        <EmptyState
          icon={<AllInclusiveOutlined />}
          title="No Active Subscriptions"
          description="You don't have any active subscriptions at the moment."
        />
      )}

      {/* Inactive subscriptions - only visible to users with billing permissions */}
      {canManageBilling && inactiveOwnedSubscriptions.length > 0 && (
        <InactiveSubscriptionsOverview
          organization={organization}
          subscriptions={inactiveOwnedSubscriptions}
          api={api}
          customerSessionToken={customerSessionToken}
          products={products}
        />
      )}
    </div>
  )
}

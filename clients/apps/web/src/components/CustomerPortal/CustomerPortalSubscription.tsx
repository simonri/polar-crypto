'use client'

import {
  useCustomerClearPendingSubscriptionUpdate,
  useCustomerCancelSubscription,
  usePortalAuthenticatedUser,
} from '@/hooks/queries/customerPortal'
import { hasBillingPermission } from '@/utils/customerPortal'
import { Client, schemas } from '@polar-sh/client'
import { Button } from '@polar-sh/orbit'
import { useState } from 'react'
import FormattedDateTime from '@polar-sh/ui/components/atoms/FormattedDateTime'
import { ConfirmModal } from '../Modal/ConfirmModal'
import { useModal } from '../Modal/useModal'
import AmountLabel from '../Shared/AmountLabel'
import { DetailRow } from '../Shared/DetailRow'
import CustomerCancellationModal from './CustomerCancellationModal'
import { SubscriptionStatusLabel } from '../Subscriptions/utils'

const CustomerPortalSubscription = ({
  api,
  customerSessionToken,
  subscription,
  products,
}: {
  api: Client
  customerSessionToken: string
  subscription: schemas['CustomerSubscription']
  products: schemas['CustomerProduct'][]
}) => {
  const {
    show: showCancelModal,
    hide: hideCancelModal,
    isShown: cancelModalIsShown,
  } = useModal()

  const [showClearPendingUpdateModal, setShowClearPendingUpdateModal] =
    useState(false)

  // Get authenticated user to check billing permissions
  const { data: authenticatedUser } = usePortalAuthenticatedUser(api)
  const canManageBilling = hasBillingPermission(authenticatedUser)

  const cancelSubscription = useCustomerCancelSubscription(api)
  const clearPendingUpdate = useCustomerClearPendingSubscriptionUpdate(api)

  const pendingUpdate = subscription.pending_update
  const pendingProduct = products.find(
    (product) => product.id === pendingUpdate?.product_id,
  )

  const isCancelled = !!(
    subscription.cancel_at_period_end || subscription.ended_at
  )

  return (
    <div className="flex flex-col gap-8">
      <div>
        <h3 className="text-xl">{subscription.product.name}</h3>
      </div>

      <div className="flex flex-col text-sm">
        <DetailRow
          label="Amount"
          value={
            subscription.amount && subscription.currency ? (
              <AmountLabel
                amount={subscription.amount}
                currency={subscription.currency}
                interval={subscription.recurring_interval}
                intervalCount={subscription.recurring_interval_count}
              />
            ) : (
              'Free'
            )
          }
        />
        <DetailRow
          label="Status"
          value={<SubscriptionStatusLabel subscription={subscription} />}
        />
        {subscription.started_at && (
          <DetailRow
            label="Start Date"
            value={
              <FormattedDateTime
                datetime={subscription.started_at}
                dateStyle="long"
                resolution="day"
              />
            }
          />
        )}
        {!subscription.ended_at && subscription.current_period_end && (
          <DetailRow
            label={
              subscription.cancel_at_period_end ? 'Expiry Date' : 'Renewal Date'
            }
            value={
              <FormattedDateTime
                datetime={subscription.current_period_end}
                dateStyle="long"
                resolution="day"
              />
            }
          />
        )}
        {subscription.ended_at && (
          <DetailRow
            label="Expired"
            value={
              <FormattedDateTime
                datetime={subscription.ended_at}
                dateStyle="long"
                resolution="day"
              />
            }
          />
        )}
      </div>

      {pendingUpdate && (
        <div className="flex flex-col gap-y-2">
          <div className="flex flex-row items-center justify-between">
            <h3>Pending Update</h3>
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setShowClearPendingUpdateModal(true)}
              loading={clearPendingUpdate.isPending}
            >
              Cancel scheduled change
            </Button>
          </div>
          <div className="flex flex-col">
            {pendingProduct && (
              <DetailRow
                label="New Product"
                value={`${subscription.product.name} -> ${pendingProduct?.name}`}
              />
            )}
            <DetailRow
              label="Update in effect from"
              value={
                <FormattedDateTime
                  datetime={pendingUpdate.applies_at}
                  dateStyle="long"
                />
              }
            />
          </div>
        </div>
      )}

      {/* Cancel button - only shown for users with billing permissions */}
      {!isCancelled && canManageBilling && (
        <Button
          variant="secondary"
          fullWidth
          onClick={showCancelModal}
          aria-label="Cancel subscription"
        >
          Cancel Subscription
        </Button>
      )}

      <CustomerCancellationModal
        subscription={subscription}
        isShown={cancelModalIsShown}
        hide={hideCancelModal}
        cancelSubscription={cancelSubscription}
      />

      <ConfirmModal
        isShown={showClearPendingUpdateModal}
        hide={() => setShowClearPendingUpdateModal(false)}
        title="Cancel scheduled change"
        description="Your subscription will remain unchanged on the next billing cycle. Are you sure you want to cancel this pending update?"
        onConfirm={async () => {
          await clearPendingUpdate.mutateAsync(subscription.id)
        }}
      />
    </div>
  )
}

export default CustomerPortalSubscription

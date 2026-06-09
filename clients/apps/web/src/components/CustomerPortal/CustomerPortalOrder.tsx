'use client'

import { Client, schemas } from '@polar-sh/client'
import { formatCurrency } from '@polar-sh/currency'
import { Status } from '@polar-sh/ui/components/atoms/Status'
import { ThemingPresetProps } from '@polar-sh/ui/hooks/theming'
import { useMemo } from 'react'
import { twMerge } from 'tailwind-merge'
import { DetailRow } from '../Shared/DetailRow'

const OrderStatusDisplayTitle: Record<schemas['Order']['status'], string> = {
  draft: 'Draft',
  paid: 'Paid',
  pending: 'Pending',
  refunded: 'Refunded',
  partially_refunded: 'Partially Refunded',
  void: 'Void',
}

const OrderStatusDisplayColor: Record<schemas['Order']['status'], string> = {
  draft: 'bg-gray-100 text-gray-500 dark:bg-gray-900 dark:text-gray-400',
  paid: 'bg-emerald-100 text-emerald-500 dark:bg-emerald-950 dark:text-emerald-500',
  pending:
    'bg-yellow-100 text-yellow-500 dark:bg-yellow-950 dark:text-yellow-500',
  refunded:
    'bg-violet-100 text-violet-500 dark:bg-violet-950 dark:text-violet-400',
  partially_refunded:
    'bg-violet-100 text-violet-500 dark:bg-violet-950 dark:text-violet-400',
  void: 'bg-red-100 text-red-500 dark:bg-red-950 dark:text-red-400',
}

const CustomerPortalOrder = ({
  api,
  order,
  themingPreset,
}: {
  api: Client
  order: schemas['CustomerOrder']
  themingPreset: ThemingPresetProps
}) => {
  const isPartiallyOrFullyRefunded = useMemo(() => {
    return order.status === 'partially_refunded' || order.status === 'refunded'
  }, [order])

  return (
    <div className="flex flex-col gap-12">
      <div className="flex w-full flex-col gap-8">
        <div className="flex flex-row flex-wrap gap-x-4">
          <h3 className="text-2xl">{order.description}</h3>
          <Status
            status={OrderStatusDisplayTitle[order.status]}
            className={twMerge(OrderStatusDisplayColor[order.status])}
          />
        </div>

        <div className="flex flex-col gap-8">
          <div className="flex flex-col">
            {order.product && (
              <DetailRow
                label="Product"
                value={<span>{order.product.name}</span>}
              />
            )}
            <DetailRow
              label="Date"
              value={
                <span>{new Date(order.created_at).toLocaleDateString()}</span>
              }
            />
          </div>

          {order.items.length > 0 && (
            <div className="flex flex-col gap-4">
              <h3 className="text-lg">Order Items</h3>
              <div className="flex flex-col gap-4">
                {order.items.map((item) => (
                  <DetailRow
                    key={item.id}
                    label={item.label}
                    value={
                      <span>
                        {formatCurrency('accounting')(
                          item.amount,
                          order.currency,
                        )}
                      </span>
                    }
                    valueClassName="justify-end"
                  />
                ))}
              </div>
            </div>
          )}

          <div className="flex flex-col">
            <DetailRow
              label="Subtotal"
              value={
                <span>
                  {formatCurrency('accounting')(
                    order.subtotal_amount,
                    order.currency,
                  )}
                </span>
              }
              valueClassName="justify-end"
            />
            <DetailRow
              label="Discount"
              value={
                <span>
                  {order.discount_amount
                    ? formatCurrency('accounting')(
                        -order.discount_amount,
                        order.currency,
                      )
                    : '—'}
                </span>
              }
              valueClassName="justify-end"
            />
            <DetailRow
              label="Net amount"
              value={
                <span>
                  {formatCurrency('accounting')(
                    order.net_amount,
                    order.currency,
                  )}
                </span>
              }
              valueClassName="justify-end"
            />
            <DetailRow
              label="Total"
              value={
                <span>
                  {formatCurrency('accounting')(
                    order.total_amount,
                    order.currency,
                  )}
                </span>
              }
              valueClassName="justify-end"
            />
            {order.applied_balance_amount !== 0 && (
              <>
                <DetailRow
                  label="Applied balance"
                  value={
                    <span>
                      {formatCurrency('accounting')(
                        order.applied_balance_amount,
                        order.currency,
                      )}
                    </span>
                  }
                  valueClassName="justify-end"
                />
                <DetailRow
                  label="To be paid"
                  value={
                    <span>
                      {formatCurrency('accounting')(
                        order.due_amount,
                        order.currency,
                      )}
                    </span>
                  }
                  valueClassName="justify-end"
                />
              </>
            )}

            {isPartiallyOrFullyRefunded && (
              <DetailRow
                label="Refunded amount"
                value={
                  <span>
                    {formatCurrency('accounting')(
                      order.refunded_amount,
                      order.currency,
                    )}
                  </span>
                }
                valueClassName="justify-end"
              />
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

export default CustomerPortalOrder

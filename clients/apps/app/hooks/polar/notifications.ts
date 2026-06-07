import { useSession } from '@/providers/SessionProvider'
import { queryClient } from '@/utils/query'
import { useMutation, useQuery, UseQueryResult } from '@tanstack/react-query'

export const useCreateNotificationRecipient = () => {
  return useMutation({
    mutationFn: async (_expoPushToken: string) => null,
  })
}

export const useListNotificationRecipients = () => {
  return useQuery({
    queryKey: ['notification_recipients'],
    queryFn: async () => ({ items: [] }),
  })
}

export const useGetNotificationRecipient = (
  _expoPushToken: string | undefined,
) => {
  const { session } = useSession()

  return useQuery({
    queryKey: ['notification_recipient', _expoPushToken],
    queryFn: async (): Promise<{ id: string } | null> => null,
    enabled: !!_expoPushToken && !!session,
    throwOnError: false,
  })
}

export const useDeleteNotificationRecipient = () => {
  return useMutation({
    mutationFn: async (_id: string) => {
      queryClient.invalidateQueries({ queryKey: ['notification_recipients'] })
      queryClient.invalidateQueries({ queryKey: ['notification_recipient'] })
      return null
    },
  })
}

export type MaintainerCreateAccountNotificationPayload = Record<string, never>

export type MaintainerNewPaidSubscriptionNotificationPayload = {
  subscriber_name?: string
  tier_name?: string
  subscription_id?: string
}

export type MaintainerNewProductSaleNotificationPayload = {
  customer_name?: string
  product_name?: string
  product_price_amount?: number
  currency?: string
  order_id?: string
}

export type MaintainerAccountCreditsGrantedNotificationPayload = {
  organization_name?: string
  amount?: number
  currency?: string
}

export type NotificationPayload = MaintainerCreateAccountNotificationPayload &
  MaintainerNewPaidSubscriptionNotificationPayload &
  MaintainerNewProductSaleNotificationPayload &
  MaintainerAccountCreditsGrantedNotificationPayload

export type Notification = {
  id: string
  type: string
  payload: NotificationPayload
  created_at: string
}

type NotificationsList = {
  notifications: Notification[]
  last_read_notification_id: string | null
}

export const useListNotifications = (): UseQueryResult<
  NotificationsList,
  Error
> => {
  const { session } = useSession()

  return useQuery({
    queryKey: ['notifications'],
    queryFn: async (): Promise<NotificationsList> => ({
      notifications: [],
      last_read_notification_id: null,
    }),
    enabled: !!session,
  })
}

export const useNotificationsMarkRead = () => {
  return useMutation({
    mutationFn: async (_variables: { notificationId: string }) => null,
  })
}

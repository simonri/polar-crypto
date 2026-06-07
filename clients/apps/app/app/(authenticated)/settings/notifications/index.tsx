import { SettingsItem } from '@/components/Settings/SettingsList'
import {
  useCreateNotificationRecipient,
  useDeleteNotificationRecipient,
  useGetNotificationRecipient,
} from '@/hooks/polar/notifications'
import { useOrganization } from '@/hooks/polar/organizations'
import { useNotifications } from '@/providers/NotificationsProvider'
import { useToast } from '@/providers/ToastProvider'
import * as Notifications from 'expo-notifications'
import { getPermissionsAsync } from 'expo-notifications'
import { Stack } from 'expo-router'
import { useCallback, useEffect, useState } from 'react'
import { RefreshControl, ScrollView, Switch } from 'react-native'
import { useTheme } from '@/design-system/useTheme'

export default function NotificationsPage() {
  const theme = useTheme()

  const {
    data: userNotificationSettings,
    refetch: refetchNotificationSettings,
    isRefetching: isRefetchingNotificationSettings,
  } = useUserOrganizationNotificationSettings(organization?.id)

  const {
    enablePushNotifications,
    disablePushNotifications,
    pushNotificationsEnabled,
  } = usePushNotifications()

  return (
    <>
      <Stack.Screen options={{ title: 'Notifications' }} />
      <ScrollView
        refreshControl={
          <RefreshControl
            refreshing={isRefetchingNotificationSettings}
            onRefresh={refetchNotificationSettings}
          />
        }
        contentContainerStyle={{
          padding: theme.spacing['spacing-16'],
        }}
      >
        <SettingsItem title="Push Notifications" variant="static">
          <Switch
            value={pushNotificationsEnabled}
            onValueChange={(value) => {
              if (value) {
                enablePushNotifications()
              } else {
                disablePushNotifications()
              }
            }}
          />
        </SettingsItem>
      </ScrollView>
    </>
  )
}

const usePushNotifications = () => {
  const [pushNotificationsEnabled, setPushNotificationsEnabled] =
    useState(false)

  const toast = useToast()
  const { expoPushToken } = useNotifications()
  const { data: notificationRecipient } = useGetNotificationRecipient(
    expoPushToken ?? undefined,
  )
  const { mutateAsync: deleteNotificationRecipient } =
    useDeleteNotificationRecipient()

  const { mutateAsync: createNotificationRecipient } =
    useCreateNotificationRecipient()

  useEffect(() => {
    getPermissionsAsync().then((status) => {
      setPushNotificationsEnabled(status.granted && !!notificationRecipient?.id)
    })
  }, [notificationRecipient])

  const enablePushNotifications = useCallback(async () => {
    const status = await Notifications.requestPermissionsAsync()

    if (status.granted) {
      try {
        const token = await Notifications.getExpoPushTokenAsync()
        if (token.data) {
          await createNotificationRecipient(token.data)
          setPushNotificationsEnabled(true)
          return
        }
      } catch (error: any) {
        if (error?.response?.status === 422) {
          setPushNotificationsEnabled(true)
          return
        }
        const status = error?.response?.status
        const message = error?.error?.detail?.[0]?.msg || error?.message
        toast.showError(
          `Failed to enable push notifications${status ? ` (${status})` : ''}${message ? `: ${message}` : ''}`,
        )
      }
    }

    setPushNotificationsEnabled(status.granted)
  }, [createNotificationRecipient, toast])

  const disablePushNotifications = useCallback(async () => {
    if (notificationRecipient?.id) {
      await deleteNotificationRecipient(notificationRecipient.id)
    }

    setPushNotificationsEnabled(false)
  }, [deleteNotificationRecipient, notificationRecipient])

  return {
    enablePushNotifications,
    disablePushNotifications,
    pushNotificationsEnabled,
  }
}

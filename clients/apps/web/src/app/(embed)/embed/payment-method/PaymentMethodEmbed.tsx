'use client'

import {
  EMBED_PAYMENT_METHOD_REDIRECT_STATUS_PARAM,
  PolarEmbedPaymentMethod,
} from '@polar-sh/checkout/payment-method'
import {
  DEFAULT_LOCALE,
  useTranslations,
  type AcceptedLocale,
} from '@polar-sh/i18n'
import { X } from 'lucide-react'
import { useCallback, useEffect } from 'react'
import { PaymentMethodForm } from './PaymentMethodForm'

interface Props {
  sessionToken: string
  embedOrigin: string
  embedReturnUrl: string
  theme?: 'light' | 'dark'
  mode: 'modal' | 'inline'
  setAsDefault: boolean
  locale?: AcceptedLocale
  serverURL: string
  redirectStatus?: string
  setupIntentId?: string
}

export const PaymentMethodEmbed = ({
  embedOrigin,
  embedReturnUrl,
  theme = 'light',
  mode,
  locale = DEFAULT_LOCALE,
  redirectStatus,
}: Props) => {
  const t = useTranslations(locale)

  useEffect(() => {
    PolarEmbedPaymentMethod.postMessage({ event: 'loaded' }, embedOrigin)
  }, [embedOrigin])

  useEffect(() => {
    if (mode !== 'inline') return

    const observer = new ResizeObserver((entries) => {
      const height = entries[0]?.contentRect.height ?? 0
      PolarEmbedPaymentMethod.postMessage(
        { event: 'resize', height },
        embedOrigin,
      )
    })
    observer.observe(document.body)
    return () => observer.disconnect()
  }, [mode, embedOrigin])

  const handleClose = useCallback(() => {
    PolarEmbedPaymentMethod.postMessage({ event: 'close' }, embedOrigin)
  }, [embedOrigin])

  const navigateBackIfStandalone = useCallback(
    (status: 'succeeded' | 'failed') => {
      if (window.parent !== window) return
      const url = new URL(embedReturnUrl)
      url.searchParams.set(EMBED_PAYMENT_METHOD_REDIRECT_STATUS_PARAM, status)
      window.location.href = url.toString()
    },
    [embedReturnUrl],
  )

  useEffect(() => {
    if (mode !== 'modal') return

    const handleOutsideClick = (event: MouseEvent) => {
      const content = document.getElementById('polar-embed-content')
      if (content && !content.contains(event.target as Node)) {
        handleClose()
      }
    }
    const layout = document.getElementById('polar-embed-layout')
    layout?.addEventListener('click', handleOutsideClick)
    return () => layout?.removeEventListener('click', handleOutsideClick)
  }, [mode, handleClose])

  void navigateBackIfStandalone

  const form = <PaymentMethodForm />

  if (redirectStatus) {
    return (
      <div className={theme === 'dark' ? 'dark' : 'light'}>
        <div className="flex h-screen w-full items-center justify-center dark:text-white">
          {form}
        </div>
      </div>
    )
  }

  if (mode === 'inline') {
    return <div className={theme === 'dark' ? 'dark' : 'light'}>{form}</div>
  }

  return (
    <div
      className={theme === 'dark' ? 'dark' : 'light'}
      id="polar-embed-layout"
    >
      <div className="flex h-full w-full items-center justify-center p-0 md:p-12 dark:text-white">
        <div
          className="dark:bg-polar-900 h-full w-full max-w-lg overflow-y-auto rounded-none bg-white p-8 md:h-auto md:rounded-2xl"
          id="polar-embed-content"
        >
          <div className="mb-6 flex items-center justify-between">
            <h2 className="text-lg font-medium">
              {t('embedPaymentMethod.title')}
            </h2>
          </div>
          {form}
        </div>
      </div>
      <button
        type="button"
        className="dark:bg-polar-950 fixed top-2 right-2 cursor-pointer rounded-full bg-white p-2 shadow-xl md:top-4 md:right-4 dark:text-white"
        onClick={handleClose}
        aria-label={t('embedPaymentMethod.close')}
      >
        <X className="h-4 w-4 md:h-6 md:w-6" />
      </button>
    </div>
  )
}

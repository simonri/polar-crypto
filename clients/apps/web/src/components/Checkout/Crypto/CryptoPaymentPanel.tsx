'use client'

import { formatCurrency } from '@polar-sh/currency'
import {
  DEFAULT_LOCALE,
  useTranslations,
  type AcceptedLocale,
} from '@polar-sh/i18n'
import { Button, Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { CryptoCurrencyCards } from './CryptoCurrencyCards'
import { Notice } from './pieces'
import { DetectedState, ReviewState } from './states/DetectedState'
import { ExpiredState } from './states/ExpiredState'
import { PartialState } from './states/PartialState'
import { PendingState } from './states/PendingState'
import { displaySymbol, formatCryptoAmount } from './types'
import { useCryptoInvoice, type CryptoInvoiceEvents } from './useCryptoInvoice'

const formatFiat = (
  amount: string | undefined,
  currency: string | undefined,
  locale: AcceptedLocale,
): string | null => {
  if (!amount || !currency) return null
  const cents = Math.round(parseFloat(amount) * 100)
  if (Number.isNaN(cents)) return null
  try {
    return formatCurrency('standard', locale)(cents, currency)
  } catch {
    return `${amount} ${currency}`
  }
}

export interface CryptoPaymentPanelProps {
  clientSecret: string
  /** Currencies enabled server-side (from checkout.payment_processor_metadata). */
  acceptedCurrencies?: string[]
  /** Preferred currency (upper-case), e.g. what the customer picked before confirming. */
  initialCurrency?: string
  locale?: AcceptedLocale
  onConfirmed: () => void | Promise<void>
  onCurrencyChange?: (currency: string) => void
  /** Polling interval in ms; exposed for tests. */
  pollInterval?: number
  /** SSE emitter for this checkout (useCheckoutClientSSE). */
  events?: CryptoInvoiceEvents
}

/**
 * Everything the customer sees after pressing the pay button on a crypto
 * checkout. One component for both the checkout page and the confirmation
 * page, so a reload never lands on a different (or broken) UI.
 *
 * Every state has an exit: expired → renew, underpaid → remainder,
 * error → retry, detected/review → "you can close this page".
 */
export function CryptoPaymentPanel({
  clientSecret,
  acceptedCurrencies = [],
  initialCurrency,
  locale = DEFAULT_LOCALE,
  onConfirmed,
  onCurrencyChange,
  pollInterval,
  events,
}: CryptoPaymentPanelProps) {
  const t = useTranslations(locale)
  const inv = useCryptoInvoice({
    clientSecret,
    acceptedCurrencies,
    initialCurrency,
    onConfirmed,
    onCurrencyChange,
    pollInterval,
    events,
  })
  const { data } = inv

  const fiat = formatFiat(data?.fiat_amount, data?.fiat_currency, locale)

  if (inv.state.kind === 'loading') {
    return (
      <Box
        display="flex"
        flexDirection="column"
        rowGap="l"
        aria-busy="true"
        data-testid="crypto-loading"
      >
        <Text color="muted">{t('checkout.crypto.loading')}</Text>
        <Text loading placeholderNumberOfLines={2} />
        <Text variant="heading-xs" loading placeholderText="0.00000000 BTC" />
        <Text loading placeholderNumberOfLines={3} />
      </Box>
    )
  }

  if (inv.state.kind === 'error') {
    return (
      <Box display="flex" flexDirection="column" rowGap="l">
        <Notice tone="bad" testId="crypto-error">
          {t('checkout.crypto.loadFailed')}
        </Notice>
        <Button variant="secondary" onClick={inv.retry}>
          {t('checkout.crypto.tryAgain')}
        </Button>
      </Box>
    )
  }

  if (!data) return null

  if (data.status === 'no_invoice' || data.status === 'complete') {
    const paid = inv.paidMethod
    const over =
      data.exception_status === 'paid_over' && paid && data.received_amount
        ? Number(data.received_amount) - Number(paid.amount)
        : 0
    return (
      <Box display="flex" flexDirection="column" rowGap="m">
        <Notice
          tone="good"
          title={t('checkout.crypto.completeTitle')}
          testId="crypto-complete"
        >
          {t('checkout.crypto.completeBody')}
        </Notice>
        {over > 0 && (
          <Notice tone="warn" testId="crypto-overpaid">
            {t('checkout.crypto.overpaidNote', {
              amount: formatCryptoAmount(over),
              currency: displaySymbol(inv.receivedCurrency),
            })}
          </Notice>
        )}
      </Box>
    )
  }

  const email = data.customer_email ?? ''

  if (data.status === 'unconfirmed') {
    return (
      <DetectedState
        t={t}
        data={data}
        paidMethod={inv.paidMethod}
        currency={inv.receivedCurrency}
      />
    )
  }

  if (data.status === 'paid_partial' && inv.paidMethod) {
    return (
      <PartialState
        t={t}
        data={data}
        paidMethod={inv.paidMethod}
        currency={inv.receivedCurrency}
        email={email}
      />
    )
  }

  if (data.status === 'needs_review') {
    return (
      <ReviewState
        t={t}
        data={data}
        currency={inv.receivedCurrency}
        email={email}
      />
    )
  }

  if (inv.methods.length === 0) {
    return (
      <Box display="flex" flexDirection="column" rowGap="l">
        <Notice tone="bad" testId="crypto-empty">
          {t('checkout.crypto.noMethods')}
        </Notice>
        <Button variant="secondary" onClick={inv.retry}>
          {t('checkout.crypto.tryAgain')}
        </Button>
      </Box>
    )
  }

  if (data.status === 'expired' || inv.localExpired) {
    return (
      <ExpiredState
        t={t}
        method={inv.method}
        onRenew={() => void inv.renew()}
        renewing={inv.renewing}
        renewFailed={inv.renewFailed}
      />
    )
  }

  return (
    <PendingState
      t={t}
      method={inv.method}
      currency={inv.currency ?? ''}
      fiat={fiat}
      secondsLeft={inv.secondsLeft}
      lockProgress={inv.lockProgress}
      selector={
        <CryptoCurrencyCards
          value={inv.currency ?? ''}
          onValueChange={inv.changeCurrency}
          currencies={inv.availableCodes}
          unavailable={inv.unavailableCodes}
          locale={locale}
        />
      }
    />
  )
}

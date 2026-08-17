'use client'

import type { TranslateFn } from '@polar-sh/i18n'
import { Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { AddressBlock, CryptoAmount, Notice } from '../pieces'
import {
  displaySymbol,
  formatCryptoAmount,
  type CryptoInvoiceStatus,
  type CryptoPaymentMethod,
} from '../types'

/** Money arrived but not enough: show exactly what is still missing. */
export const PartialState = ({
  t,
  data,
  paidMethod,
  currency,
  email,
}: {
  t: TranslateFn
  data: CryptoInvoiceStatus
  paidMethod: CryptoPaymentMethod
  currency: string
  email: string
}) => {
  const remaining = formatCryptoAmount(data.remaining_amount ?? '0')
  const symbol = displaySymbol(currency)
  return (
    <Box
      display="flex"
      flexDirection="column"
      rowGap="xl"
      data-testid="crypto-partial"
    >
      <Notice tone="warn" title={t('checkout.crypto.partialTitle')}>
        {t('checkout.crypto.partialBody', {
          received: formatCryptoAmount(data.received_amount ?? '0'),
          expected: formatCryptoAmount(paidMethod.amount),
          currency: symbol,
        })}
      </Notice>
      <Box display="flex" flexDirection="column" rowGap="s">
        <Text variant="label">{t('checkout.crypto.sendRemaining')}</Text>
        <CryptoAmount
          amount={remaining}
          currency={symbol}
          copyLabel={t('checkout.crypto.copyAmount')}
          copiedLabel={t('checkout.crypto.copied')}
          testId="crypto-remaining"
        />
        <Text variant="caption" color="muted">
          {t('checkout.crypto.sameAddress')}
        </Text>
      </Box>
      <AddressBlock
        address={paidMethod.payment_address}
        copyLabel={t('checkout.crypto.copyAddress')}
        copiedLabel={t('checkout.crypto.copied')}
      />
      <Text variant="caption" color="muted">
        {t('checkout.crypto.partialHelp', { email })}
      </Text>
    </Box>
  )
}

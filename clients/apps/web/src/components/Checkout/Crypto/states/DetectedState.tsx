'use client'

import type { TranslateFn } from '@polar-sh/i18n'
import { Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { Notice, TxLinks } from '../pieces'
import {
  displaySymbol,
  formatCryptoAmount,
  type CryptoInvoiceStatus,
  type CryptoPaymentMethod,
} from '../types'

/** Funds seen on-chain, waiting for the network to confirm them. */
export const DetectedState = ({
  t,
  data,
  paidMethod,
  currency,
}: {
  t: TranslateFn
  data: CryptoInvoiceStatus
  paidMethod: CryptoPaymentMethod | null
  currency: string
}) => {
  const required = Math.max(1, paidMethod?.required_confirmations ?? 1)
  const confs = paidMethod?.confirmations ?? 0
  return (
    <Box
      display="flex"
      flexDirection="column"
      rowGap="l"
      data-testid="crypto-detected"
    >
      <Notice tone="good" title={t('checkout.crypto.detectedTitle')}>
        {t('checkout.crypto.detectedBody', {
          amount: formatCryptoAmount(data.received_amount ?? '0'),
          currency: displaySymbol(currency),
        })}
      </Notice>
      <Box display="flex" flexDirection="column" rowGap="s">
        <Text>
          {t('checkout.crypto.confirmations', {
            count: Math.min(confs, required),
            required,
          })}
        </Text>
        <Box display="flex" columnGap="xs" aria-hidden="true">
          {Array.from({ length: required }).map((_, i) => (
            <Box
              key={i}
              flex="1"
              height={8}
              borderRadius="s"
              backgroundColor={
                i < confs ? 'background-success' : 'background-secondary'
              }
            />
          ))}
        </Box>
      </Box>
      <TxLinks
        currency={currency}
        hashes={data.tx_hashes ?? []}
        label={t('checkout.crypto.viewTx')}
      />
      <Text variant="caption" color="muted">
        {t('checkout.crypto.detectedCloseHint')}
      </Text>
    </Box>
  )
}

/** Funds seen but a human must accept them (late/short, duplicate…). */
export const ReviewState = ({
  t,
  data,
  currency,
  email,
}: {
  t: TranslateFn
  data: CryptoInvoiceStatus
  currency: string
  email: string
}) => {
  const reason = data.exception_status?.startsWith('paid_late')
    ? t('checkout.crypto.reviewReasonLate')
    : data.exception_status === 'duplicate_payment'
      ? t('checkout.crypto.reviewReasonDuplicate')
      : t('checkout.crypto.reviewReasonGeneric')
  return (
    <Box
      display="flex"
      flexDirection="column"
      rowGap="l"
      data-testid="crypto-review"
    >
      <Notice tone="info" title={t('checkout.crypto.reviewTitle')}>
        {t('checkout.crypto.reviewBody', {
          amount: formatCryptoAmount(data.received_amount ?? '0'),
          currency: displaySymbol(currency),
          reason,
          email,
        })}
      </Notice>
      <TxLinks
        currency={currency}
        hashes={data.tx_hashes ?? []}
        label={t('checkout.crypto.viewTx')}
      />
    </Box>
  )
}

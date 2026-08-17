'use client'

import type { TranslateFn } from '@polar-sh/i18n'
import { Button, Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { Notice } from '../pieces'
import {
  displaySymbol,
  formatCryptoAmount,
  type CryptoPaymentMethod,
} from '../types'

/** Price lock ran out with no funds seen: offer a fresh amount, never a dead end. */
export const ExpiredState = ({
  t,
  method,
  onRenew,
  renewing,
  renewFailed,
}: {
  t: TranslateFn
  method: CryptoPaymentMethod | null
  onRenew: () => void
  renewing: boolean
  renewFailed: boolean
}) => (
  <Box
    display="flex"
    flexDirection="column"
    rowGap="l"
    data-testid="crypto-expired"
  >
    <Notice tone="info" title={t('checkout.crypto.expiredTitle')}>
      {t('checkout.crypto.expiredBody')}
    </Notice>
    <Button onClick={onRenew} loading={renewing} size="lg" className="w-full">
      {renewing ? t('checkout.crypto.renewing') : t('checkout.crypto.renew')}
    </Button>
    {renewFailed && (
      <Text color="danger">{t('checkout.crypto.renewFailed')}</Text>
    )}
    <Text variant="caption" color="muted">
      {t('checkout.crypto.alreadySent')}
    </Text>
    {method && (
      <Box opacity={0.5} display="flex" flexDirection="column" rowGap="xs">
        <Text variant="caption" color="muted">
          {t('checkout.crypto.previousAmount')}
        </Text>
        <Text as="p" variant="heading-xxs" lineThrough>
          {formatCryptoAmount(method.amount)}{' '}
          <Text as="span" color="muted">
            {displaySymbol(method.currency)}
          </Text>
        </Text>
      </Box>
    )}
  </Box>
)

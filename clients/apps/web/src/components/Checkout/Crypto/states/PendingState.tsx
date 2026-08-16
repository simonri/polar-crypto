'use client'

import type { TranslateFn } from '@polar-sh/i18n'
import { Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { Timer } from 'lucide-react'
import { AddressBlock, CryptoAmount } from '../pieces'
import {
  CRYPTO_NETWORK,
  WRONG_NETWORK_RISK,
  formatCryptoAmount,
  formatDuration,
  type CryptoPaymentMethod,
} from '../types'
import { HowToSteps } from './HowToSteps'
import { QrOrWallet } from './QrOrWallet'

const LOW_TIME_SECONDS = 120

export const PendingState = ({
  t,
  method,
  currency,
  fiat,
  rate,
  email,
  secondsLeft,
  lockProgress,
  selector,
}: {
  t: TranslateFn
  method: CryptoPaymentMethod | null
  currency: string
  fiat: string | null
  /** Locked exchange rate, formatted as fiat (e.g. "$59,756.00"). */
  rate: string | null
  email: string
  secondsLeft: number | null
  lockProgress: number | null
  selector: React.ReactNode
}) => {
  const network = CRYPTO_NETWORK[currency]
  const risky = WRONG_NETWORK_RISK.has(currency)
  const lowTime = secondsLeft !== null && secondsLeft <= LOW_TIME_SECONDS

  return (
    <Box
      display="flex"
      flexDirection="column"
      rowGap="xl"
      data-testid="crypto-pending"
    >
      <HowToSteps t={t} />

      {selector}

      {method && (
        <>
          <Box display="flex" flexDirection="column" rowGap="s">
            <Text variant="label">{t('checkout.crypto.sendExactly')}</Text>
            <CryptoAmount
              amount={formatCryptoAmount(method.amount)}
              currency={method.currency.toUpperCase()}
              approx={
                fiat ? t('checkout.crypto.approxFiat', { amount: fiat }) : null
              }
              copyLabel={t('checkout.crypto.copyAmount')}
              copiedLabel={t('checkout.crypto.copied')}
              testId="crypto-amount"
            />
            <Text variant="caption" color="muted">
              {t('checkout.crypto.feeHelper')}
            </Text>
          </Box>

          <Box display="flex" flexDirection="column" rowGap="s">
            <Box display="flex" alignItems="center" columnGap="s">
              <Text variant="label">{t('checkout.crypto.toAddress')}</Text>
              {network && (
                <Box
                  as="span"
                  borderRadius="s"
                  paddingHorizontal="xs"
                  backgroundColor={
                    risky ? 'background-warning' : 'background-secondary'
                  }
                >
                  <Text
                    as="span"
                    variant="caption"
                    color={risky ? 'warning' : 'muted'}
                  >
                    {t('checkout.crypto.networkOnly', {
                      network: network,
                    })}
                  </Text>
                </Box>
              )}
            </Box>
            <AddressBlock
              address={method.payment_address}
              copyLabel={t('checkout.crypto.copyAddress')}
              copiedLabel={t('checkout.crypto.copied')}
            />
            {risky && (
              <Text variant="caption" color="danger">
                {t('checkout.crypto.wrongNetworkWarning', {
                  currency: 'USDC',
                })}
              </Text>
            )}
          </Box>

          <QrOrWallet t={t} method={method} />
        </>
      )}

      <Box display="flex" flexDirection="column" rowGap="s">
        {lockProgress !== null && (
          <Box
            height={6}
            width="100%"
            overflow="hidden"
            borderRadius="full"
            backgroundColor="background-secondary"
            color={lowTime ? 'text-warning' : 'text-pending'}
            role="progressbar"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(lockProgress * 100)}
            aria-label={t('checkout.crypto.whyTimer')}
          >
            {/* Dynamic width isn't expressible with tokens; the fill inherits
                the token colour from the track via currentColor. */}
            <span
              style={{
                display: 'block',
                height: '100%',
                width: `${Math.round(lockProgress * 100)}%`,
                background: 'currentColor',
                borderRadius: 9999,
                transition: 'width 1s linear',
              }}
            />
          </Box>
        )}
        <Box
          display="flex"
          alignItems="center"
          justifyContent="between"
          columnGap="s"
          flexWrap="wrap"
        >
          <Text color="muted">{t('checkout.crypto.waiting')}</Text>
          {secondsLeft !== null && (
            <Box
              display="flex"
              alignItems="center"
              columnGap="xs"
              title={t('checkout.crypto.whyTimer')}
            >
              <Timer className="h-3.5 w-3.5" />
              <Text
                as="span"
                color={lowTime ? 'warning' : 'muted'}
                data-testid="crypto-countdown"
              >
                {t('checkout.crypto.lockedFor', {
                  time: formatDuration(secondsLeft),
                })}
              </Text>
            </Box>
          )}
        </Box>
        {rate && method && (
          <Text variant="caption" color="muted" data-testid="crypto-rate">
            {t('checkout.crypto.rateLine', {
              currency: method.currency.toUpperCase(),
              rate,
            })}
          </Text>
        )}
        {email && (
          <Text variant="caption" color="muted">
            {t('checkout.crypto.closeHint', { email })}
          </Text>
        )}
      </Box>
    </Box>
  )
}

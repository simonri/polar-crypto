'use client'

import type { TranslateFn } from '@polar-sh/i18n'
import { Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { Timer } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { AddressBlock, CryptoAmount } from '../pieces'
import {
  CRYPTO_NETWORK,
  WRONG_NETWORK_RISK,
  formatCryptoAmount,
  formatDuration,
  type CryptoPaymentMethod,
} from '../types'

const LOW_TIME_SECONDS = 120

export const PendingState = ({
  t,
  method,
  currency,
  fiat,
  email,
  secondsLeft,
  lockProgress,
  selector,
}: {
  t: TranslateFn
  method: CryptoPaymentMethod | null
  currency: string
  fiat: string | null
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
                    {t('checkout.crypto.networkOnly', { network })}
                  </Text>
                </Box>
              )}
            </Box>
            <AddressBlock
              address={method.payment_address}
              copyLabel={t('checkout.crypto.copyAddress')}
              copiedLabel={t('checkout.crypto.copied')}
            />
          </Box>

          <Box
            display="flex"
            flexDirection="column"
            alignItems="center"
            rowGap="m"
          >
            <a href={method.payment_url} className="rounded-xl bg-white p-3">
              <QRCodeSVG
                value={method.payment_url}
                size={180}
                level="Q"
                marginSize={2}
              />
            </a>
            <a
              href={method.payment_url}
              className="text-xs text-blue-600 hover:underline dark:text-blue-400"
            >
              {t('checkout.crypto.openInWallet')}
            </a>
          </Box>
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
        {email && (
          <Text variant="caption" color="muted">
            {t('checkout.crypto.closeHint', { email })}
          </Text>
        )}
      </Box>
    </Box>
  )
}

'use client'

import {
  DEFAULT_LOCALE,
  useTranslations,
  type AcceptedLocale,
} from '@polar-sh/i18n'
import { Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { CryptoTokenIcon } from './CryptoTokenIcon'
import {
  CRYPTO_LABELS,
  CRYPTO_NETWORK,
  CURRENCY_META,
  sortCurrencies,
} from './types'

/**
 * The coin choice as option cards instead of a blind dropdown. Each card
 * shows what a first-timer needs to decide: speed and price stability.
 * Easiest option first, marked Recommended.
 */
export function CryptoCurrencyCards({
  value,
  onValueChange,
  currencies,
  unavailable = [],
  locale = DEFAULT_LOCALE,
}: {
  value: string
  onValueChange: (value: string) => void
  /** Upper-cased currency codes that are payable right now. */
  currencies: string[]
  /** Codes configured server-side but not currently payable (daemon down). */
  unavailable?: string[]
  locale?: AcceptedLocale
}) {
  const t = useTranslations(locale)
  const payable = new Set(currencies.map((c) => c.toUpperCase()))
  const options = sortCurrencies(
    Array.from(
      new Set([...currencies, ...unavailable].map((c) => c.toUpperCase())),
    ),
  )
  if (options.length <= 1) return null

  return (
    <Box display="flex" flexDirection="column" rowGap="s">
      <Text variant="label">{t('checkout.crypto.payWith')}</Text>
      <Box
        role="radiogroup"
        aria-label={t('checkout.crypto.payWith')}
        display="flex"
        flexDirection="column"
        rowGap="s"
      >
        {options.map((token) => {
          const meta = CURRENCY_META[token]
          const isUnavailable = !payable.has(token)
          const selected = token === value
          return (
            <Box
              as="div"
              key={token}
              role="radio"
              aria-checked={selected}
              aria-disabled={isUnavailable || undefined}
              tabIndex={isUnavailable ? -1 : 0}
              onClick={() => !isUnavailable && onValueChange(token)}
              onKeyDown={(e: React.KeyboardEvent) => {
                if ((e.key === 'Enter' || e.key === ' ') && !isUnavailable) {
                  e.preventDefault()
                  onValueChange(token)
                }
              }}
              display="grid"
              gridTemplateColumns="auto 1fr auto"
              alignItems="center"
              columnGap="m"
              padding="m"
              borderRadius="m"
              // Constant width: a border that only grows on selection eats
              // into the padding and reflows the text inside by a pixel or
              // two. Only the colour should change.
              borderWidth={2}
              borderStyle="solid"
              borderColor={selected ? 'border-warning' : 'border-primary'}
              backgroundColor={
                selected ? 'background-secondary' : 'background-card'
              }
              cursor={isUnavailable ? 'not-allowed' : 'pointer'}
              opacity={isUnavailable ? 0.5 : 1}
              data-testid={`crypto-option-${token}`}
            >
              <CryptoTokenIcon token={token} />
              <Box display="flex" flexDirection="column">
                <Box display="flex" alignItems="center" columnGap="s">
                  <Text as="span">{CRYPTO_LABELS[token] ?? token}</Text>
                  {meta?.recommended && !isUnavailable && (
                    <Box
                      as="span"
                      backgroundColor="background-success"
                      borderRadius="s"
                      paddingHorizontal="xs"
                    >
                      <Text as="span" variant="caption" color="success">
                        {t('checkout.crypto.recommended')}
                      </Text>
                    </Box>
                  )}
                </Box>
                <Text as="span" variant="caption" color="muted">
                  {isUnavailable
                    ? t('checkout.crypto.unavailable')
                    : [
                        t('checkout.crypto.networkLabel', {
                          network: CRYPTO_NETWORK[token] ?? token,
                        }),
                        meta?.stable
                          ? t('checkout.crypto.priceStable')
                          : undefined,
                      ]
                        .filter(Boolean)
                        .join(' · ')}
                </Text>
              </Box>
              {!isUnavailable && meta && (
                <Text as="span" variant="caption" color="muted" align="right">
                  {t(`checkout.crypto.${meta.etaKey}`)}
                </Text>
              )}
            </Box>
          )
        })}
      </Box>
    </Box>
  )
}

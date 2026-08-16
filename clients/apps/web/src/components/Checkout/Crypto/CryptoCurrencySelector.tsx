'use client'

import { StaticImage } from '@/components/Image/StaticImage'
import {
  DEFAULT_LOCALE,
  useTranslations,
  type AcceptedLocale,
} from '@polar-sh/i18n'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
  Text,
} from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { CRYPTO_LABELS, iconFor, sortCurrencies } from './types'

export const CryptoTokenIcon = ({ token }: { token: string }) => (
  <StaticImage
    src={`/assets/crypto/${iconFor(token)}.svg`}
    alt={token}
    width={20}
    height={20}
    className="shrink-0"
  />
)

export function CryptoCurrencySelector({
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
  /** Codes that are configured but not currently payable (daemon down). */
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
  return (
    <Box display="flex" flexDirection="column" rowGap="s">
      <Text variant="label">{t('checkout.crypto.payWith')}</Text>
      <Select value={value} onValueChange={onValueChange}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder={t('checkout.crypto.selectToken')} />
        </SelectTrigger>
        <SelectContent>
          {options.map((token) => {
            const isUnavailable = !payable.has(token)
            return (
              <SelectItem key={token} value={token} disabled={isUnavailable}>
                <Box display="flex" alignItems="center" columnGap="s">
                  <CryptoTokenIcon token={token} />
                  <Text as="span">{CRYPTO_LABELS[token] ?? token}</Text>
                  {token === 'SOL_USDC' && !isUnavailable && (
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
                  {isUnavailable && (
                    <Text as="span" variant="caption" color="disabled">
                      · {t('checkout.crypto.unavailable')}
                    </Text>
                  )}
                </Box>
              </SelectItem>
            )
          })}
        </SelectContent>
      </Select>
    </Box>
  )
}

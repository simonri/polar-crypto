'use client'

import { getServerURL } from '@/utils/api'
import {
  DEFAULT_LOCALE,
  useTranslations,
  type AcceptedLocale,
} from '@polar-sh/i18n'
import { Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { ExternalLink } from 'lucide-react'
import { useEffect, useState } from 'react'
import {
  explorerUrl,
  formatCryptoAmount,
  type CryptoInvoiceStatus,
} from './types'

/**
 * "Paid 0.00082 BTC · View transaction" on the success page. The fiat total
 * alone matches nothing in a crypto customer's wallet history; the coin
 * amount and transaction hash are what they (and their accountant) look for.
 */
export const CryptoPaidSummary = ({
  clientSecret,
  locale = DEFAULT_LOCALE,
}: {
  clientSecret: string
  locale?: AcceptedLocale
}) => {
  const t = useTranslations(locale)
  const [status, setStatus] = useState<CryptoInvoiceStatus | null>(null)

  useEffect(() => {
    let cancelled = false
    const load = async () => {
      try {
        const res = await fetch(
          `${getServerURL()}/v1/checkouts/client/${clientSecret}/crypto-status`,
          { credentials: 'include' },
        )
        if (!res.ok) return
        const data: CryptoInvoiceStatus = await res.json()
        if (!cancelled) {
          setStatus(data)
        }
      } catch {
        // Cosmetic detail: the success page stands on its own without it.
      }
    }
    void load()
    return () => {
      cancelled = true
    }
  }, [clientSecret])

  if (!status?.received_amount || !status.received_currency) return null

  const currency = status.received_currency.toUpperCase()
  const txHash = status.tx_hashes?.[0]
  const txUrl = txHash ? explorerUrl(currency, txHash) : null

  return (
    <Box
      display="flex"
      alignItems="center"
      justifyContent="center"
      columnGap="s"
      flexWrap="wrap"
      data-testid="crypto-paid-summary"
    >
      <Text variant="caption" color="muted">
        {t('checkout.crypto.paidCrypto', {
          amount: formatCryptoAmount(status.received_amount),
          currency,
        })}
      </Text>
      {txUrl && (
        <a
          href={txUrl}
          target="_blank"
          rel="noreferrer"
          className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline dark:text-blue-400"
        >
          {t('checkout.crypto.viewTx')} <ExternalLink className="h-3 w-3" />
        </a>
      )}
    </Box>
  )
}

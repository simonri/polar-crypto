// Types and pure helpers for the crypto payment panel.
// Payload shape mirrors checkout_service.get_crypto_invoice_status.

export interface CryptoPaymentMethod {
  currency: string
  amount: string
  rate?: string
  payment_address: string
  payment_url: string
  lightning: boolean
  /** BOLT11 invoice attached to an on-chain BTC method (unified QR / WebLN). */
  lightning_invoice?: string | null
  confirmations: number
  required_confirmations?: number
  /** Solana only: what a browser wallet needs to build the transfer. */
  rpc_url?: string
  reference?: string
  spl_token?: string
}

export type CryptoInvoiceStatusValue =
  | 'pending'
  | 'unconfirmed'
  | 'paid_partial'
  | 'complete'
  | 'expired'
  | 'needs_review'
  | 'invalid'
  | 'no_invoice'
  | 'not_found'

export interface CryptoInvoiceStatus {
  status: CryptoInvoiceStatusValue | string
  exception_status?: string
  created_at?: string
  expiry?: string
  monitoring_expiry?: string | null
  paid_at?: string | null
  payment_detected_at?: string | null
  fiat_amount?: string
  fiat_currency?: string
  received_amount?: string | null
  received_currency?: string | null
  remaining_amount?: string | null
  tx_hashes?: string[]
  customer_email?: string | null
  payment_methods?: CryptoPaymentMethod[]
}

/**
 * Display order: BTC first (the default, recommended option), then the
 * alternatives. The server decides which of these are actually offered.
 */
export const CURRENCY_ORDER = ['BTC', 'SOL_USDC', 'SOL', 'LTC']

export const CRYPTO_LABELS: Record<string, string> = {
  BTC: 'Bitcoin',
  LTC: 'Litecoin',
  SOL: 'Solana',
  SOL_USDC: 'USDC',
}

/** Short display symbol for amounts and inline text (never the internal code). */
export const displaySymbol = (currency: string): string =>
  currency.toUpperCase() === 'SOL_USDC' ? 'USDC' : currency.toUpperCase()

export const CRYPTO_NETWORK: Record<string, string> = {
  BTC: 'Bitcoin',
  LTC: 'Litecoin',
  SOL: 'Solana',
  SOL_USDC: 'Solana',
}

/**
 * Per-currency guidance for the option cards: how long a payment typically
 * takes to confirm. i18n key suffix under checkout.crypto.*. `recommended`
 * drives the "Recommended" tag; `stable` is a separate, purely descriptive
 * "Price stable" line (only true for USDC).
 */
export const CURRENCY_META: Record<
  string,
  {
    etaKey: 'etaInstant' | 'etaMinutes' | 'etaSlow'
    stable?: boolean
    recommended?: boolean
  }
> = {
  BTC: { etaKey: 'etaSlow', recommended: true },
  SOL_USDC: { etaKey: 'etaInstant', stable: true },
  SOL: { etaKey: 'etaInstant' },
  LTC: { etaKey: 'etaMinutes' },
}

export const iconFor = (token: string): string =>
  token.toLowerCase() === 'sol_usdc' ? 'usdc' : token.toLowerCase()

export const sortCurrencies = (currencies: string[]): string[] =>
  [...currencies].sort((a, b) => {
    const ia = CURRENCY_ORDER.indexOf(a.toUpperCase())
    const ib = CURRENCY_ORDER.indexOf(b.toUpperCase())
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib)
  })

export const explorerUrl = (
  currency: string,
  txHash: string,
): string | null => {
  switch (currency.toLowerCase()) {
    case 'btc':
      return `https://mempool.space/tx/${txHash}`
    case 'ltc':
      return `https://litecoinspace.org/tx/${txHash}`
    case 'sol':
    case 'sol_usdc':
      return `https://solscan.io/tx/${txHash}`
    default:
      return null
  }
}

/** Plain decimal string, no exponent, no trailing zeros. */
export const formatCryptoAmount = (amount: string | number): string => {
  const s = typeof amount === 'number' ? amount.toFixed(12) : amount
  if (!s.includes('.')) return s
  const trimmed = s.replace(/0+$/, '').replace(/\.$/, '')
  return trimmed === '' ? '0' : trimmed
}

export const formatDuration = (seconds: number): string => {
  const h = Math.floor(seconds / 3600)
  const m = Math.floor((seconds % 3600) / 60)
  const s = seconds % 60
  const mm = h > 0 ? m.toString().padStart(2, '0') : m.toString()
  return `${h > 0 ? `${h}:` : ''}${mm}:${s.toString().padStart(2, '0')}`
}

const storageKey = (clientSecret: string) =>
  `polar:crypto-currency:${clientSecret}`

export const readPersistedCurrency = (clientSecret: string): string | null => {
  try {
    return window.localStorage.getItem(storageKey(clientSecret))
  } catch {
    return null
  }
}

export const persistCurrency = (clientSecret: string, currency: string) => {
  try {
    window.localStorage.setItem(storageKey(clientSecret), currency)
  } catch {
    // Private mode / disabled storage: the choice just won't survive reload.
  }
}

/** Parse the comma-separated `accepted_currencies` metadata into upper-case codes. */
export const parseAcceptedCurrencies = (
  raw: string | undefined | null,
): string[] =>
  sortCurrencies(
    (raw ?? '')
      .split(',')
      .map((c) => c.trim().toUpperCase())
      .filter(Boolean),
  )

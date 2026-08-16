// Types and pure helpers for the crypto payment panel.
// Payload shape mirrors checkout_service.get_crypto_invoice_status.

export interface CryptoPaymentMethod {
  currency: string
  amount: string
  rate?: string
  payment_address: string
  payment_url: string
  lightning: boolean
  confirmations: number
  required_confirmations?: number
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
 * Display order: easiest for a first-timer first (stable price, fast, cheap).
 * The server decides which of these are actually offered.
 */
export const CURRENCY_ORDER = ['SOL_USDC', 'SOL', 'LTC', 'BTC']

export const CRYPTO_LABELS: Record<string, string> = {
  BTC: 'Bitcoin (BTC)',
  LTC: 'Litecoin (LTC)',
  SOL: 'Solana (SOL)',
  SOL_USDC: 'USDC on Solana',
}

export const CRYPTO_NETWORK: Record<string, string> = {
  BTC: 'Bitcoin',
  LTC: 'Litecoin',
  SOL: 'Solana',
  SOL_USDC: 'Solana',
}

/** Tokens living on another chain: sending on the wrong network loses funds. */
export const WRONG_NETWORK_RISK = new Set(['SOL_USDC'])

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
    // Private mode / disabled storage — the choice just won't survive reload.
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

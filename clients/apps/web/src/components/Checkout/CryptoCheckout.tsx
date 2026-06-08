'use client'

import { getServerURL } from '@/utils/api'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@polar-sh/orbit'
import { Copy, Timer } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { useCallback, useEffect, useState } from 'react'

interface CryptoPaymentMethod {
  currency: string
  amount: string
  payment_address: string
  payment_url: string
  lightning: boolean
  confirmations: number
}

interface CryptoInvoiceStatus {
  status: string
  exception_status?: string
  expiry?: string
  paid_at?: string | null
  payment_methods?: CryptoPaymentMethod[]
}

interface CryptoCheckoutStatusProps {
  clientSecret: string
  selectedCurrency: string
  onConfirmed: () => void
  onExpired: () => void
}

export function CryptoCurrencySelector({
  value,
  onValueChange,
}: {
  value: string
  onValueChange: (value: string) => void
}) {
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium leading-none">Pay with</p>
      <Select value={value} onValueChange={onValueChange}>
        <SelectTrigger className="w-full">
          <SelectValue placeholder="Select token" />
        </SelectTrigger>
        <SelectContent>
          {SUPPORTED_TOKENS.map((token) => (
            <SelectItem key={token} value={token}>
              <div className="flex items-center gap-2">
                {CRYPTO_ICONS[token]}
                <span>{CRYPTO_LABELS[token] ?? token}</span>
              </div>
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  )
}

const SUPPORTED_TOKENS = ['BTC', 'LTC']

const BitcoinIcon = () => (
  <svg
    viewBox="0 0 32 32"
    className="h-5 w-5 shrink-0"
    xmlns="http://www.w3.org/2000/svg"
  >
    <circle cx="16" cy="16" r="16" fill="#F7931A" />
    <path
      d="M22.5 14.5c.3-2-1.2-3.1-3.3-3.8l.7-2.7-1.7-.4-.7 2.6c-.45-.1-.9-.2-1.35-.3l.7-2.6-1.7-.4-.7 2.7c-.37-.08-.73-.17-1.08-.25l0 0-2.35-.59-.44 1.84s1.22.28 1.19.3c.66.16.78.6.76.95l-.76 3.36c.05.01.11.03.18.06l-.18-.05-1.07 4.74c-.08.2-.28.5-.74.39.02.02-1.19-.3-1.19-.3l-.81 1.97 2.22.55c.41.1.82.21 1.22.31l-.72 2.88 1.7.42.72-2.89c.46.13.92.24 1.37.35l-.71 2.87 1.7.43.72-2.88c2.96.56 5.18.33 6.12-2.34.74-2.12-.04-3.34-1.57-4.14 1.12-.26 1.96-.99 2.19-2.52zm-3.93 5.51c-.53 2.12-4.1.97-5.26.68l.94-3.74c1.16.29 4.88.87 4.32 3.06zm.53-5.55c-.48 1.93-3.46.95-4.43.71l.85-3.4c.97.24 4.1.7 3.58 2.69z"
      fill="white"
    />
  </svg>
)

const LitecoinIcon = () => (
  <svg
    viewBox="0 0 32 32"
    className="h-5 w-5 shrink-0"
    xmlns="http://www.w3.org/2000/svg"
  >
    <circle cx="16" cy="16" r="16" fill="#BFBBBB" />
    <path
      d="M10.5 23l1.6-5.9-1.6.5.6-2.1 1.6-.5 2.3-8.5h4.7l-1.7 6.2 1.6-.5-.6 2.1-1.6.5-.9 3.2h7.9l-.7 3H10.5z"
      fill="white"
    />
  </svg>
)

const CRYPTO_ICONS: Record<string, React.ReactNode> = {
  BTC: <BitcoinIcon />,
  LTC: <LitecoinIcon />,
}

const CRYPTO_LABELS: Record<string, string> = {
  BTC: 'Bitcoin (BTC)',
  LTC: 'Litecoin (LTC)',
}

export function CryptoCheckoutStatus({
  clientSecret,
  selectedCurrency,
  onConfirmed,
  onExpired,
}: CryptoCheckoutStatusProps) {
  const [invoiceStatus, setInvoiceStatus] =
    useState<CryptoInvoiceStatus | null>(null)
  const [copied, setCopied] = useState(false)
  const [timeLeft, setTimeLeft] = useState<number | null>(null)

  const fetchStatus = useCallback(async () => {
    try {
      const res = await fetch(
        `${getServerURL()}/v1/checkouts/client/${clientSecret}/crypto-status`,
        { credentials: 'include' },
      )
      if (res.ok) {
        const data: CryptoInvoiceStatus = await res.json()
        setInvoiceStatus(data)

        if (data.status === 'complete') {
          onConfirmed()
        } else if (data.status === 'expired') {
          onExpired()
        }

      }
    } catch {
      // Network error — will retry
    }
  }, [clientSecret, onConfirmed, onExpired])

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, 5000)
    return () => clearInterval(interval)
  }, [fetchStatus])

  // Countdown timer
  useEffect(() => {
    if (!invoiceStatus?.expiry) return
    const update = () => {
      const remaining = Math.max(
        0,
        Math.floor(
          (new Date(invoiceStatus.expiry!).getTime() - Date.now()) / 1000,
        ),
      )
      setTimeLeft(remaining)
    }
    update()
    const t = setInterval(update, 1000)
    return () => clearInterval(t)
  }, [invoiceStatus?.expiry])

  const supportedMethods = invoiceStatus?.payment_methods?.filter((m) =>
    SUPPORTED_TOKENS.includes(m.currency.toUpperCase()),
  )

  const selectedMethod = supportedMethods?.find(
    (m) => m.currency.toUpperCase() === selectedCurrency,
  )

  const copyAddress = useCallback(async () => {
    if (!selectedMethod) return
    await navigator.clipboard.writeText(selectedMethod.payment_address)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }, [selectedMethod])

  const formatTime = (seconds: number) => {
    const m = Math.floor(seconds / 60)
    const s = seconds % 60
    return `${m}:${s.toString().padStart(2, '0')}`
  }

  if (!invoiceStatus || !invoiceStatus.payment_methods?.length) {
    return (
      <div className="flex items-center justify-center py-8 text-sm text-gray-500">
        Loading payment details...
      </div>
    )
  }

  const statusMessage: Record<string, string> = {
    pending: 'Waiting for payment...',
    unconfirmed: 'Payment detected! Awaiting confirmations...',
    complete: 'Payment confirmed ✓',
    expired: 'Invoice expired.',
    invalid: 'Invalid payment.',
  }

  return (
    <div className="flex flex-col gap-y-6">
      {selectedMethod && (
        <>
          {/* Amount */}
          <div className="space-y-2">
            <p className="text-sm font-medium leading-none">Send exactly</p>
            <p className="text-2xl font-semibold tabular-nums">
              {parseFloat(selectedMethod.amount).toString()}{' '}
              <span className="text-base font-normal text-gray-500 dark:text-gray-400">
                {selectedMethod.currency.toUpperCase()}
              </span>
            </p>
          </div>

          {/* Address */}
          <div className="space-y-2">
            <p className="text-sm font-medium leading-none">To address</p>
            <div className="dark:border-polar-700 dark:bg-polar-800 flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 shadow-xs">
              <code className="flex-1 overflow-hidden text-ellipsis text-xs">
                {selectedMethod.payment_address}
              </code>
              <button
                onClick={copyAddress}
                className="dark:text-polar-400 dark:hover:text-polar-200 shrink-0 text-gray-400 hover:text-gray-600"
              >
                <Copy className="h-4 w-4" />
              </button>
            </div>
            {copied && (
              <p className="text-xs text-green-600 dark:text-green-400">
                Copied!
              </p>
            )}
          </div>

          {/* QR code */}
          <div className="flex flex-col items-center gap-3">
            <a href={selectedMethod.payment_url} className="rounded-xl bg-white p-3">
              <QRCodeSVG value={selectedMethod.payment_url} size={180} />
            </a>
            <a
              href={selectedMethod.payment_url}
              className="text-xs text-blue-600 hover:underline dark:text-blue-400"
            >
              Open in Wallet App
            </a>
          </div>
        </>
      )}

      {/* Status + countdown */}
      <div className="flex items-center justify-between">
        <p className="text-sm text-gray-500 dark:text-gray-400">
          {statusMessage[invoiceStatus.status] ?? invoiceStatus.status}
        </p>
        {timeLeft !== null && timeLeft > 0 && (
          <div className="flex items-center gap-1 text-sm text-gray-500 dark:text-gray-400">
            <Timer className="h-3.5 w-3.5" />
            <span className="tabular-nums">{formatTime(timeLeft)}</span>
          </div>
        )}
      </div>

      {invoiceStatus.status === 'unconfirmed' && (
        <div className="rounded-xl bg-yellow-50 p-3 text-sm text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300">
          Payment detected on-chain! Waiting for{' '}
          {selectedMethod?.confirmations ?? 0} confirmation(s)...
        </div>
      )}
    </div>
  )
}

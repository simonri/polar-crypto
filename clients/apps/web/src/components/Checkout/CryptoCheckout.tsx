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
import { StaticImage } from '@/components/Image/StaticImage'
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
      <p className="text-sm leading-none font-medium">Pay with</p>
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

const SUPPORTED_TOKENS = ['BTC', 'LTC', 'SOL', 'SOL_USDC']

const CryptoTokenIcon = ({ token }: { token: string }) => (
  <StaticImage
    src={`/assets/crypto/${token.toLowerCase() === 'sol_usdc' ? 'usdc' : token.toLowerCase()}.svg`}
    alt={token}
    width={20}
    height={20}
    className="shrink-0"
  />
)

const CRYPTO_ICONS: Record<string, React.ReactNode> = {
  BTC: <CryptoTokenIcon token="BTC" />,
  LTC: <CryptoTokenIcon token="LTC" />,
  SOL: <CryptoTokenIcon token="SOL" />,
  SOL_USDC: <CryptoTokenIcon token="SOL_USDC" />,
}

const CRYPTO_LABELS: Record<string, string> = {
  BTC: 'Bitcoin (BTC)',
  LTC: 'Litecoin (LTC)',
  SOL: 'Solana (SOL)',
  SOL_USDC: 'USDC on Solana',
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

        if (data.status === 'complete' || data.status === 'no_invoice') {
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
    // eslint-disable-next-line react-hooks/set-state-in-effect
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
            <p className="text-sm leading-none font-medium">Send exactly</p>
            <p className="text-2xl font-semibold tabular-nums">
              {parseFloat(selectedMethod.amount).toString()}{' '}
              <span className="text-base font-normal text-gray-500 dark:text-gray-400">
                {selectedMethod.currency.toUpperCase()}
              </span>
            </p>
          </div>

          {/* Address */}
          <div className="space-y-2">
            <p className="text-sm leading-none font-medium">To address</p>
            <div className="dark:border-polar-700 dark:bg-polar-800 flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-3 py-2 shadow-xs">
              <code className="flex-1 overflow-hidden text-xs text-ellipsis">
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
            <a
              href={selectedMethod.payment_url}
              className="rounded-xl bg-white p-3"
            >
              <QRCodeSVG
                value={selectedMethod.payment_url}
                size={180}
                level="Q"
                marginSize={2}
              />
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

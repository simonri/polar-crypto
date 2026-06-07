'use client'

import { getServerURL } from '@/utils/api'
import { Trash2 } from 'lucide-react'
import { useCallback, useEffect, useState } from 'react'

interface CryptoPayoutWallet {
  id: string
  account_id: string
  currency: string
  wallet_address: string
  is_active: boolean
}

const SUPPORTED_CURRENCIES = [
  { value: 'btc', label: 'Bitcoin (BTC)' },
  { value: 'eth', label: 'Ethereum (ETH)' },
  { value: 'ltc', label: 'Litecoin (LTC)' },
]

interface CryptoPayoutWalletsProps {
  payoutAccountId: string
}

export function CryptoPayoutWallets({
  payoutAccountId,
}: CryptoPayoutWalletsProps) {
  const [wallets, setWallets] = useState<CryptoPayoutWallet[]>([])
  const [loading, setLoading] = useState(true)
  const [currency, setCurrency] = useState('btc')
  const [address, setAddress] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState<string | null>(null)

  const fetchWallets = useCallback(async () => {
    try {
      const res = await fetch(
        `${getServerURL()}/v1/integrations/crypto/payout-wallets?payout_account_id=${payoutAccountId}`,
        { credentials: 'include' },
      )
      if (res.ok) {
        setWallets(await res.json())
      }
    } finally {
      setLoading(false)
    }
  }, [payoutAccountId])

  useEffect(() => {
    fetchWallets()
  }, [fetchWallets])

  const addWallet = useCallback(async () => {
    if (!address.trim()) return
    setSaving(true)
    setError(null)
    setSuccess(null)
    try {
      const res = await fetch(
        `${getServerURL()}/v1/integrations/crypto/payout-wallets`,
        {
          method: 'POST',
          credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            payout_account_id: payoutAccountId,
            currency,
            wallet_address: address.trim(),
          }),
        },
      )
      if (res.ok) {
        setAddress('')
        setSuccess('Wallet address saved.')
        await fetchWallets()
      } else {
        const data = await res.json()
        setError(data.detail ?? 'Failed to save wallet address.')
      }
    } finally {
      setSaving(false)
    }
  }, [address, currency, fetchWallets, payoutAccountId])

  const removeWallet = useCallback(
    async (walletCurrency: string) => {
      const res = await fetch(
        `${getServerURL()}/v1/integrations/crypto/payout-wallets/${walletCurrency}?payout_account_id=${payoutAccountId}`,
        { method: 'DELETE', credentials: 'include' },
      )
      if (res.ok) {
        await fetchWallets()
      }
    },
    [fetchWallets, payoutAccountId],
  )

  if (loading) {
    return <div className="text-sm text-gray-500">Loading...</div>
  }

  return (
    <div className="flex flex-col gap-y-6">
      <div>
        <h3 className="text-sm font-medium text-gray-900 dark:text-gray-100">
          Crypto payout wallets
        </h3>
        <p className="mt-1 text-sm text-gray-500">
          Add wallet addresses to receive crypto payouts. Double-check addresses
          before saving — crypto transactions are irreversible.
        </p>
      </div>

      {wallets.length > 0 && (
        <div className="flex flex-col gap-y-2">
          {wallets.map((w) => (
            <div
              key={w.id}
              className="flex items-center gap-3 rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 dark:border-gray-700 dark:bg-gray-800"
            >
              <span className="w-10 text-xs font-medium text-gray-500 uppercase">
                {w.currency}
              </span>
              <code className="flex-1 overflow-hidden text-xs text-ellipsis">
                {w.wallet_address}
              </code>
              <button
                onClick={() => removeWallet(w.currency)}
                className="text-gray-400 hover:text-red-500"
              >
                <Trash2 className="h-4 w-4" />
              </button>
            </div>
          ))}
        </div>
      )}

      {/* Add wallet form */}
      <div className="flex flex-col gap-y-3 rounded-xl border border-gray-200 p-4 dark:border-gray-700">
        <h4 className="text-sm font-medium">Add wallet address</h4>
        <div className="flex gap-2">
          <select
            value={currency}
            onChange={(e) => setCurrency(e.target.value)}
            className="rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
          >
            {SUPPORTED_CURRENCIES.map((c) => (
              <option key={c.value} value={c.value}>
                {c.label}
              </option>
            ))}
          </select>
          <input
            type="text"
            placeholder="Wallet address"
            value={address}
            onChange={(e) => setAddress(e.target.value)}
            className="flex-1 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm dark:border-gray-700 dark:bg-gray-900"
          />
        </div>
        {error && <p className="text-sm text-red-600">{error}</p>}
        {success && <p className="text-sm text-green-600">{success}</p>}
        <div className="rounded-lg bg-yellow-50 p-2 text-xs text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-300">
          ⚠ Always verify your wallet address. Payouts to wrong addresses cannot
          be reversed.
        </div>
        <button
          onClick={addWallet}
          disabled={saving || !address.trim()}
          className="self-end rounded-lg bg-blue-600 px-4 py-2 text-sm font-medium text-white hover:bg-blue-700 disabled:opacity-50"
        >
          {saving ? 'Saving...' : 'Save address'}
        </button>
      </div>
    </div>
  )
}

'use client'

import { getServerURL } from '@/utils/api'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  persistCurrency,
  readPersistedCurrency,
  sortCurrencies,
  type CryptoInvoiceStatus,
  type CryptoPaymentMethod,
} from './types'

export type FetchState =
  | { kind: 'loading' }
  | { kind: 'error' }
  | { kind: 'ready'; data: CryptoInvoiceStatus }

/** Statuses after which nothing more can happen on this page. */
const TERMINAL: string[] = ['complete', 'no_invoice']

/** Consecutive failed polls before we show an error instead of a skeleton. */
const FAILURES_BEFORE_ERROR = 3

export interface UseCryptoInvoiceOptions {
  clientSecret: string
  acceptedCurrencies: string[]
  initialCurrency?: string
  onConfirmed: () => void | Promise<void>
  onCurrencyChange?: (currency: string) => void
  pollInterval?: number
}

/**
 * Polls the invoice status, resolves which currency to display, drives the
 * price-lock countdown and exposes the renew action. Terminal states stop the
 * polling and fire `onConfirmed` exactly once.
 */
export function useCryptoInvoice({
  clientSecret,
  acceptedCurrencies,
  initialCurrency,
  onConfirmed,
  onCurrencyChange,
  pollInterval = 5000,
}: UseCryptoInvoiceOptions) {
  const [state, setState] = useState<FetchState>({ kind: 'loading' })
  const [failures, setFailures] = useState(0)
  const [now, setNow] = useState(() => Date.now())
  const [renewing, setRenewing] = useState(false)
  const [renewFailed, setRenewFailed] = useState(false)
  const [selected, setSelected] = useState<string | null>(() => {
    if (typeof window === 'undefined') return initialCurrency ?? null
    return readPersistedCurrency(clientSecret) ?? initialCurrency ?? null
  })
  const confirmedRef = useRef(false)
  const stoppedRef = useRef(false)

  const applyStatus = useCallback(
    (next: CryptoInvoiceStatus) => {
      setState({ kind: 'ready', data: next })
      setFailures(0)
      if (TERMINAL.includes(next.status)) {
        stoppedRef.current = true
        if (!confirmedRef.current) {
          confirmedRef.current = true
          void onConfirmed()
        }
      }
    },
    [onConfirmed],
  )

  const fetchStatus = useCallback(async () => {
    if (stoppedRef.current) return
    try {
      const res = await fetch(
        `${getServerURL()}/v1/checkouts/client/${clientSecret}/crypto-status`,
        { credentials: 'include' },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      applyStatus((await res.json()) as CryptoInvoiceStatus)
    } catch {
      setFailures((n) => n + 1)
    }
  }, [clientSecret, applyStatus])

  useEffect(() => {
    fetchStatus()
    const interval = setInterval(fetchStatus, pollInterval)
    return () => clearInterval(interval)
  }, [fetchStatus, pollInterval])

  useEffect(() => {
    if (state.kind !== 'ready' && failures >= FAILURES_BEFORE_ERROR) {
      setState({ kind: 'error' })
    }
  }, [failures, state.kind])

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [])

  const retry = useCallback(() => {
    setState({ kind: 'loading' })
    setFailures(0)
    void fetchStatus()
  }, [fetchStatus])

  const renew = useCallback(async () => {
    setRenewing(true)
    setRenewFailed(false)
    try {
      const res = await fetch(
        `${getServerURL()}/v1/checkouts/client/${clientSecret}/crypto-invoice/renew`,
        { method: 'POST', credentials: 'include' },
      )
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      applyStatus((await res.json()) as CryptoInvoiceStatus)
    } catch {
      setRenewFailed(true)
      // A 409 means money arrived in the meantime — refresh to show it.
      void fetchStatus()
    } finally {
      setRenewing(false)
    }
  }, [clientSecret, applyStatus, fetchStatus])

  const data = state.kind === 'ready' ? state.data : null
  const methods = useMemo<CryptoPaymentMethod[]>(
    () => data?.payment_methods ?? [],
    [data?.payment_methods],
  )
  const availableCodes = useMemo(
    () => sortCurrencies(methods.map((m) => m.currency.toUpperCase())),
    [methods],
  )
  const unavailableCodes = useMemo(
    () =>
      acceptedCurrencies
        .map((c) => c.toUpperCase())
        .filter((c) => !availableCodes.includes(c)),
    [acceptedCurrencies, availableCodes],
  )
  const currency = useMemo(() => {
    if (selected && availableCodes.includes(selected)) return selected
    return availableCodes[0] ?? selected ?? null
  }, [selected, availableCodes])

  const changeCurrency = useCallback(
    (value: string) => {
      const upper = value.toUpperCase()
      setSelected(upper)
      persistCurrency(clientSecret, upper)
      onCurrencyChange?.(upper)
    },
    [clientSecret, onCurrencyChange],
  )

  const method = useMemo(
    () => methods.find((m) => m.currency.toUpperCase() === currency) ?? null,
    [methods, currency],
  )
  const receivedCurrency = (data?.received_currency ?? '').toUpperCase()
  const paidMethod = useMemo(
    () =>
      receivedCurrency
        ? (methods.find((m) => m.currency.toUpperCase() === receivedCurrency) ??
          null)
        : null,
    [methods, receivedCurrency],
  )

  const expiryMs = data?.expiry ? new Date(data.expiry).getTime() : null
  const createdMs = data?.created_at
    ? new Date(data.created_at).getTime()
    : null
  const secondsLeft =
    expiryMs !== null ? Math.max(0, Math.floor((expiryMs - now) / 1000)) : null
  /** Fraction of the price lock still remaining (1 → 0), null if unknown. */
  const lockProgress =
    expiryMs !== null && createdMs !== null && expiryMs > createdMs
      ? Math.min(1, Math.max(0, (expiryMs - now) / (expiryMs - createdMs)))
      : null
  /** Countdown hit zero before the server flipped the status. */
  const localExpired =
    data?.status === 'pending' && secondsLeft !== null && secondsLeft <= 0

  return {
    state,
    data,
    methods,
    availableCodes,
    unavailableCodes,
    currency,
    changeCurrency,
    method,
    paidMethod,
    receivedCurrency,
    secondsLeft,
    lockProgress,
    localExpired,
    retry,
    renew,
    renewing,
    renewFailed,
  }
}

import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  CryptoPaymentPanel,
  formatCryptoAmount,
  parseAcceptedCurrencies,
  sortCurrencies,
} from './CryptoCheckout'

vi.mock('@/utils/api', () => ({ getServerURL: () => 'https://api.polar.sh' }))
vi.mock('qrcode.react', () => ({ QRCodeSVG: () => null }))
vi.mock('@/components/Image/StaticImage', () => ({
  StaticImage: () => null,
}))
vi.mock('@polar-sh/orbit', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@polar-sh/orbit')>()
  return {
    ...actual,
    // Radix Select needs a real DOM layout; render options inline instead.
    Select: (props: {
      children?: React.ReactNode
      value?: string
      onValueChange?: (v: string) => void
    }) => (
      <div data-testid="select" data-value={props.value}>
        {props.children}
      </div>
    ),
    SelectTrigger: (props: { children?: React.ReactNode }) => (
      <div>{props.children}</div>
    ),
    SelectValue: () => null,
    SelectContent: (props: { children?: React.ReactNode }) => (
      <div>{props.children}</div>
    ),
    SelectItem: (props: {
      children?: React.ReactNode
      value: string
      disabled?: boolean
    }) => (
      <div
        role="option"
        aria-selected={false}
        aria-disabled={props.disabled}
        data-value={props.value}
      >
        {props.children}
      </div>
    ),
  }
})

const BTC_METHOD = {
  currency: 'btc',
  amount: '0.00123456',
  rate: '50000',
  payment_address: 'bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh',
  payment_url:
    'bitcoin:bc1qxy2kgdygjrsqtzq2n0yrf2493p83kkfjhx0wlh?amount=0.00123456',
  lightning: false,
  confirmations: 0,
  required_confirmations: 1,
}

const USDC_METHOD = {
  currency: 'sol_usdc',
  amount: '49',
  rate: '1',
  payment_address: '7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU',
  payment_url: 'solana:7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU?amount=49',
  lightning: false,
  confirmations: 0,
  required_confirmations: 1,
}

const inMinutes = (m: number) => new Date(Date.now() + m * 60_000).toISOString()

const PENDING = {
  status: 'pending',
  created_at: new Date(Date.now() - 60_000).toISOString(),
  expiry: inMinutes(14),
  monitoring_expiry: inMinutes(24 * 60),
  fiat_amount: '49',
  fiat_currency: 'USD',
  customer_email: 'alex@example.com',
  tx_hashes: [],
  payment_methods: [BTC_METHOD, USDC_METHOD],
}

function stubFetch(data: unknown, ok = true) {
  return vi.spyOn(global, 'fetch').mockResolvedValue({
    ok,
    status: ok ? 200 : 500,
    json: async () => data,
  } as Response)
}

const renderPanel = (
  props: Partial<React.ComponentProps<typeof CryptoPaymentPanel>> = {},
) =>
  render(
    <CryptoPaymentPanel
      clientSecret="cs_test"
      acceptedCurrencies={['BTC', 'SOL_USDC']}
      onConfirmed={vi.fn()}
      pollInterval={100_000}
      {...props}
    />,
  )

describe('helpers', () => {
  it('formats amounts without exponent or trailing zeros', () => {
    expect(formatCryptoAmount('0.00120000')).toBe('0.0012')
    expect(formatCryptoAmount('49.000000')).toBe('49')
    expect(formatCryptoAmount(1e-7)).toBe('0.0000001')
  })
  it('orders currencies easiest-first', () => {
    expect(sortCurrencies(['BTC', 'LTC', 'SOL_USDC'])).toEqual([
      'SOL_USDC',
      'LTC',
      'BTC',
    ])
  })
  it('parses accepted currencies metadata', () => {
    expect(parseAcceptedCurrencies('btc, ltc')).toEqual(['LTC', 'BTC'])
    expect(parseAcceptedCurrencies(undefined)).toEqual([])
  })
})

describe('CryptoPaymentPanel', () => {
  beforeEach(() => {
    window.localStorage.clear()
  })
  afterEach(() => {
    cleanup()
    vi.restoreAllMocks()
  })

  it('shows a skeleton while loading', () => {
    vi.spyOn(global, 'fetch').mockReturnValue(new Promise(() => {}))
    renderPanel()
    screen.getByTestId('crypto-loading')
  })

  it('renders payment details, fiat equivalent and countdown when pending', async () => {
    stubFetch(PENDING)
    renderPanel({ initialCurrency: 'BTC' })
    await screen.findByTestId('crypto-pending')
    expect(screen.getByTestId('crypto-amount').textContent).toContain(
      '0.00123456',
    )
    expect(screen.getByTestId('crypto-address').textContent).toBe(
      BTC_METHOD.payment_address,
    )
    expect(screen.getByText(/≈ \$49/)).toBeTruthy()
    expect(screen.getByTestId('crypto-countdown').textContent).toMatch(
      /1[34]:\d\d/,
    )
  })

  it('never selects a currency the server did not create an address for', async () => {
    stubFetch({ ...PENDING, payment_methods: [BTC_METHOD] })
    renderPanel({
      acceptedCurrencies: ['BTC', 'SOL_USDC'],
      initialCurrency: 'SOL_USDC',
    })
    await screen.findByTestId('crypto-pending')
    // Falls back to the payable BTC address instead of an empty pane…
    expect(screen.getByTestId('crypto-address').textContent).toBe(
      BTC_METHOD.payment_address,
    )
    // …and the unavailable option is disabled, not hidden.
    const usdc = screen
      .getAllByRole('option')
      .find((o) => o.getAttribute('data-value') === 'SOL_USDC')
    expect(usdc?.getAttribute('aria-disabled')).toBe('true')
  })

  it('shows an error with retry when the status endpoint keeps failing', async () => {
    const fetchMock = stubFetch({}, false)
    renderPanel({ pollInterval: 10 })
    await screen.findByTestId('crypto-error', undefined, { timeout: 3000 })
    fetchMock.mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => PENDING,
    } as Response)
    fireEvent.click(screen.getByText('Try again'))
    await screen.findByTestId('crypto-pending')
  })

  it('shows an actionable message when no address could be generated', async () => {
    stubFetch({ ...PENDING, payment_methods: [] })
    renderPanel()
    await screen.findByTestId('crypto-empty')
  })

  it('calls onConfirmed exactly once when complete', async () => {
    const onConfirmed = vi.fn()
    stubFetch({ ...PENDING, status: 'complete' })
    renderPanel({ onConfirmed, pollInterval: 10 })
    await screen.findByTestId('crypto-complete')
    await new Promise((r) => setTimeout(r, 50))
    expect(onConfirmed).toHaveBeenCalledTimes(1)
  })

  it('tells the customer when they overpaid', async () => {
    stubFetch({
      ...PENDING,
      status: 'complete',
      exception_status: 'paid_over',
      received_amount: '0.00223456',
      received_currency: 'btc',
    })
    renderPanel()
    await screen.findByTestId('crypto-overpaid')
    expect(screen.getByTestId('crypto-overpaid').textContent).toContain(
      '0.001 BTC more than needed',
    )
  })

  it('calls onConfirmed for no_invoice (100% discount)', async () => {
    const onConfirmed = vi.fn()
    stubFetch({ status: 'no_invoice' })
    renderPanel({ onConfirmed })
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledTimes(1))
  })

  it('shows detected state with confirmations progress and explorer link', async () => {
    stubFetch({
      ...PENDING,
      status: 'unconfirmed',
      received_amount: '0.00123456',
      received_currency: 'btc',
      tx_hashes: ['abcdef1234567890'],
      payment_methods: [
        { ...BTC_METHOD, confirmations: 0, required_confirmations: 1 },
      ],
    })
    renderPanel()
    await screen.findByTestId('crypto-detected')
    expect(screen.getByText('0 of 1 confirmations')).toBeTruthy()
    const link = screen.getByText('View transaction').closest('a')
    expect(link?.getAttribute('href')).toBe(
      'https://mempool.space/tx/abcdef1234567890',
    )
    expect(screen.getByText(/Safe to close this page/)).toBeTruthy()
  })

  it('shows the remaining amount when underpaid, never "confirmed"', async () => {
    stubFetch({
      ...PENDING,
      status: 'paid_partial',
      exception_status: 'paid_partial',
      received_amount: '0.001',
      received_currency: 'btc',
      remaining_amount: '0.00023456',
    })
    const onConfirmed = vi.fn()
    renderPanel({ onConfirmed })
    await screen.findByTestId('crypto-partial')
    expect(screen.getByTestId('crypto-remaining').textContent).toContain(
      '0.00023456',
    )
    expect(screen.getByTestId('crypto-address').textContent).toBe(
      BTC_METHOD.payment_address,
    )
    expect(onConfirmed).not.toHaveBeenCalled()
  })

  it('explains the review state for late payments', async () => {
    stubFetch({
      ...PENDING,
      status: 'needs_review',
      exception_status: 'paid_late_short',
      received_amount: '0.00123456',
      received_currency: 'btc',
    })
    renderPanel()
    await screen.findByTestId('crypto-review')
    expect(screen.getByText(/arrived after the price lock/)).toBeTruthy()
  })

  it('offers a fresh amount when expired and renews via the endpoint', async () => {
    const fetchMock = stubFetch({
      ...PENDING,
      status: 'expired',
      expiry: inMinutes(-1),
    })
    renderPanel()
    await screen.findByTestId('crypto-expired')

    fetchMock.mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ ...PENDING, expiry: inMinutes(15) }),
    } as Response)
    fireEvent.click(screen.getByText('Get a fresh amount'))
    await screen.findByTestId('crypto-pending')
    const renewCall = fetchMock.mock.calls.find(([url]) =>
      String(url).endsWith('/crypto-invoice/renew'),
    )
    expect(renewCall?.[1]).toMatchObject({ method: 'POST' })
  })

  it('switches to the expired state client-side when the countdown hits zero', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true })
    stubFetch({ ...PENDING, expiry: new Date(Date.now() + 1500).toISOString() })
    renderPanel()
    await screen.findByTestId('crypto-pending')
    await act(async () => {
      await vi.advanceTimersByTimeAsync(3000)
    })
    await screen.findByTestId('crypto-expired')
    vi.useRealTimers()
  })

  it('remembers the chosen currency per checkout', async () => {
    window.localStorage.setItem('polar:crypto-currency:cs_test', 'SOL_USDC')
    stubFetch(PENDING)
    renderPanel({ initialCurrency: 'BTC' })
    await screen.findByTestId('crypto-pending')
    expect(screen.getByTestId('crypto-address').textContent).toBe(
      USDC_METHOD.payment_address,
    )
  })

  it('polls the crypto-status endpoint with the client secret', async () => {
    const fetchMock = stubFetch(PENDING)
    renderPanel()
    await screen.findByTestId('crypto-pending')
    expect(fetchMock).toHaveBeenCalledWith(
      'https://api.polar.sh/v1/checkouts/client/cs_test/crypto-status',
      { credentials: 'include' },
    )
  })
})

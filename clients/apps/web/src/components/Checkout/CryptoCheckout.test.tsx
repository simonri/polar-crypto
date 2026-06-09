import { render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { CryptoCheckoutStatus } from './CryptoCheckout'

vi.mock('@/utils/api', () => ({ getServerURL: () => 'https://api.polar.sh' }))
vi.mock('qrcode.react', () => ({ QRCodeSVG: () => null }))
vi.mock('@polar-sh/orbit', () => ({
  Select: (props: { children?: React.ReactNode }) => props.children,
  SelectTrigger: (props: { children?: React.ReactNode }) => props.children,
  SelectValue: () => null,
  SelectContent: (props: { children?: React.ReactNode }) => props.children,
  SelectItem: (props: { children?: React.ReactNode }) => props.children,
}))

const BASE_PROPS = {
  clientSecret: 'cs_test',
  selectedCurrency: 'BTC',
}

const PENDING_BTC = {
  status: 'pending',
  expiry: new Date(Date.now() + 900_000).toISOString(),
  payment_methods: [
    {
      currency: 'BTC',
      amount: '0.00123456',
      payment_address: 'bc1qtest',
      payment_url: 'bitcoin:bc1qtest?amount=0.00123456',
      lightning: false,
      confirmations: 0,
    },
  ],
}

function stubFetch(data: unknown, ok = true) {
  vi.spyOn(global, 'fetch').mockResolvedValue({
    ok,
    json: async () => data,
  } as Response)
}

describe('CryptoCheckoutStatus', () => {
  afterEach(() => vi.restoreAllMocks())

  it('shows loading state on initial render', () => {
    vi.spyOn(global, 'fetch').mockReturnValue(new Promise(() => {}))
    render(
      <CryptoCheckoutStatus
        {...BASE_PROPS}
        onConfirmed={vi.fn()}
        onExpired={vi.fn()}
      />,
    )
    screen.getByText('Loading payment details...')
  })

  it('calls onConfirmed when status is complete', async () => {
    const onConfirmed = vi.fn()
    stubFetch({ status: 'complete', payment_methods: [] })
    render(
      <CryptoCheckoutStatus
        {...BASE_PROPS}
        onConfirmed={onConfirmed}
        onExpired={vi.fn()}
      />,
    )
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledTimes(1))
  })

  it('calls onConfirmed when status is no_invoice (100% discount)', async () => {
    const onConfirmed = vi.fn()
    stubFetch({ status: 'no_invoice' })
    render(
      <CryptoCheckoutStatus
        {...BASE_PROPS}
        onConfirmed={onConfirmed}
        onExpired={vi.fn()}
      />,
    )
    await waitFor(() => expect(onConfirmed).toHaveBeenCalledTimes(1))
  })

  it('calls onExpired when status is expired', async () => {
    const onExpired = vi.fn()
    stubFetch({ status: 'expired', payment_methods: [] })
    render(
      <CryptoCheckoutStatus
        {...BASE_PROPS}
        onConfirmed={vi.fn()}
        onExpired={onExpired}
      />,
    )
    await waitFor(() => expect(onExpired).toHaveBeenCalledTimes(1))
  })

  it('shows payment UI when pending with payment methods', async () => {
    stubFetch(PENDING_BTC)
    render(
      <CryptoCheckoutStatus
        {...BASE_PROPS}
        onConfirmed={vi.fn()}
        onExpired={vi.fn()}
      />,
    )
    await screen.findByText('Send exactly')
    screen.getByText(/0\.00123456/)
    screen.getByText('bc1qtest')
    screen.getByText('Waiting for payment...')
  })

  it('does not call onConfirmed or onExpired for pending status', async () => {
    const onConfirmed = vi.fn()
    const onExpired = vi.fn()
    stubFetch(PENDING_BTC)
    render(
      <CryptoCheckoutStatus
        {...BASE_PROPS}
        onConfirmed={onConfirmed}
        onExpired={onExpired}
      />,
    )
    await screen.findByText('Send exactly')
    expect(onConfirmed).not.toHaveBeenCalled()
    expect(onExpired).not.toHaveBeenCalled()
  })

  it('fetches from the correct URL with client secret', async () => {
    const fetchSpy = vi.spyOn(global, 'fetch').mockResolvedValue({
      ok: true,
      json: async () => ({ status: 'pending', payment_methods: [] }),
    } as Response)

    render(
      <CryptoCheckoutStatus
        clientSecret="cs_abc"
        selectedCurrency="BTC"
        onConfirmed={vi.fn()}
        onExpired={vi.fn()}
      />,
    )

    await waitFor(() => expect(fetchSpy).toHaveBeenCalled())
    expect(fetchSpy).toHaveBeenCalledWith(
      'https://api.polar.sh/v1/checkouts/client/cs_abc/crypto-status',
      { credentials: 'include' },
    )
  })
})

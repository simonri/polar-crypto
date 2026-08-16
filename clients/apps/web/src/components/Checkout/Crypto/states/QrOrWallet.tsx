'use client'

import type { TranslateFn } from '@polar-sh/i18n'
import { Button, Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { QRCodeSVG } from 'qrcode.react'
import { useEffect, useState } from 'react'
import type { CryptoPaymentMethod } from '../types'
import { WalletPay } from '../WalletPay'

/**
 * The "how do I actually pay" block, ordered by device:
 *
 * - Phone (coarse pointer): wallets live on this same device, so a QR of the
 *   own screen is useless — "Open in wallet app" is the primary button and
 *   the QR hides behind a toggle for the desktop-wallet-nearby case.
 * - Desktop: the QR is primary (scan with the phone), the deep link secondary.
 *
 * Browser-wallet one-click buttons (Phantom / WebLN) render above either.
 */
export const QrOrWallet = ({
  t,
  method,
}: {
  t: TranslateFn
  method: CryptoPaymentMethod
}) => {
  const [isCoarse, setIsCoarse] = useState(false)
  const [qrOpen, setQrOpen] = useState(false)

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const query = window.matchMedia('(pointer: coarse)')
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setIsCoarse(query.matches)
    const onChange = (e: MediaQueryListEvent) => setIsCoarse(e.matches)
    query.addEventListener('change', onChange)
    return () => query.removeEventListener('change', onChange)
  }, [])

  const qr = (
    <Box display="flex" flexDirection="column" alignItems="center" rowGap="s">
      <a
        href={method.payment_url}
        className="rounded-xl bg-white p-3"
        data-testid="crypto-qr"
      >
        <QRCodeSVG
          value={method.payment_url}
          size={180}
          level="Q"
          marginSize={2}
        />
      </a>
      <Text variant="caption" color="muted">
        {t('checkout.crypto.scanQr')}
      </Text>
    </Box>
  )

  return (
    <Box display="flex" flexDirection="column" rowGap="m">
      <WalletPay t={t} method={method} />
      {isCoarse ? (
        <>
          <a href={method.payment_url} data-testid="crypto-wallet-link">
            <Button size="lg" className="w-full">
              {t('checkout.crypto.openInWallet')}
            </Button>
          </a>
          <button
            type="button"
            onClick={() => setQrOpen((open) => !open)}
            className="text-xs text-blue-600 hover:underline dark:text-blue-400"
            data-testid="crypto-qr-toggle"
          >
            {qrOpen ? t('checkout.crypto.hideQr') : t('checkout.crypto.showQr')}
          </button>
          {qrOpen && qr}
        </>
      ) : (
        <>
          {qr}
          <Box display="flex" justifyContent="center">
            <a
              href={method.payment_url}
              className="text-xs text-blue-600 hover:underline dark:text-blue-400"
              data-testid="crypto-wallet-link"
            >
              {t('checkout.crypto.openInWallet')}
            </a>
          </Box>
        </>
      )}
    </Box>
  )
}

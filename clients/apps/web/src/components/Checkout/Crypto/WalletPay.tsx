'use client'

import type { TranslateFn } from '@polar-sh/i18n'
import { Button, Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { useCallback, useEffect, useState } from 'react'
import type { CryptoPaymentMethod } from './types'

type WalletState = 'idle' | 'paying' | 'sent' | 'failed'

interface PhantomProvider {
  isPhantom?: boolean
  publicKey: { toString(): string } | null
  connect(): Promise<{ publicKey: { toString(): string } }>
  signAndSendTransaction(tx: unknown): Promise<{ signature: string }>
}

const getPhantom = (): PhantomProvider | null => {
  if (typeof window === 'undefined') return null
  const w = window as unknown as {
    phantom?: { solana?: PhantomProvider }
    solana?: PhantomProvider
  }
  const provider = w.phantom?.solana ?? w.solana
  return provider?.isPhantom ? provider : null
}

const getWebln = (): {
  enable(): Promise<void>
  sendPayment(bolt11: string): Promise<unknown>
} | null => {
  if (typeof window === 'undefined') return null
  const w = window as unknown as {
    webln?: {
      enable(): Promise<void>
      sendPayment(b: string): Promise<unknown>
    }
  }
  return w.webln ?? null
}

/**
 * Build and send the Solana transfer through Phantom. Copy/paste is where
 * underpayments and wrong-network sends happen; one click from the wallet
 * sends the right amount, the right token, tagged with the Solana Pay
 * reference the backend watches for.
 */
async function payWithPhantom(
  provider: PhantomProvider,
  method: CryptoPaymentMethod,
): Promise<string> {
  const web3 = await import('@solana/web3.js')
  const connection = new web3.Connection(
    method.rpc_url ?? 'https://api.mainnet-beta.solana.com',
  )
  const { publicKey } = await provider.connect()
  const payer = new web3.PublicKey(publicKey.toString())
  const recipient = new web3.PublicKey(method.payment_address)
  const reference = method.reference
    ? new web3.PublicKey(method.reference)
    : null

  const instructions: InstanceType<typeof web3.TransactionInstruction>[] = []
  let transferInstruction

  if (method.spl_token) {
    const spl = await import('@solana/spl-token')
    const mint = new web3.PublicKey(method.spl_token)
    const source = spl.getAssociatedTokenAddressSync(mint, payer)
    const destination = spl.getAssociatedTokenAddressSync(mint, recipient)
    // The merchant's token account for this mint may not exist on-chain yet
    // (e.g. their very first payment in this currency). A wallet's own Send
    // screen creates it automatically; a hand-built instruction doesn't
    // unless we add this explicitly. Without it, simulation shows the
    // transfer failing and Phantom blocks the request outright. Idempotent:
    // a no-op, still paid by the customer as part of the same transaction,
    // if the account already exists.
    instructions.push(
      spl.createAssociatedTokenAccountIdempotentInstruction(
        payer,
        destination,
        recipient,
        mint,
      ),
    )
    // USDC has 6 decimals on Solana
    const amount = BigInt(Math.round(parseFloat(method.amount) * 1e6))
    transferInstruction = spl.createTransferCheckedInstruction(
      source,
      mint,
      destination,
      payer,
      amount,
      6,
    )
  } else {
    transferInstruction = web3.SystemProgram.transfer({
      fromPubkey: payer,
      toPubkey: recipient,
      lamports: Math.round(parseFloat(method.amount) * web3.LAMPORTS_PER_SOL),
    })
  }
  if (reference) {
    transferInstruction.keys.push({
      pubkey: reference,
      isSigner: false,
      isWritable: false,
    })
  }
  instructions.push(transferInstruction)

  const transaction = new web3.Transaction().add(...instructions)
  transaction.feePayer = payer
  transaction.recentBlockhash = (
    await connection.getLatestBlockhash()
  ).blockhash
  const { signature } = await provider.signAndSendTransaction(transaction)
  return signature
}

/**
 * One-click payment buttons for wallets the browser already has: Phantom for
 * SOL/USDC, WebLN (e.g. Alby) for Lightning. Rendered only when the matching
 * provider exists; failures fall back to the QR/address with a message, never
 * a dead end.
 */
export const WalletPay = ({
  t,
  method,
}: {
  t: TranslateFn
  method: CryptoPaymentMethod
}) => {
  const [state, setState] = useState<WalletState>('idle')
  const [hasPhantom, setHasPhantom] = useState(false)
  const [hasWebln, setHasWebln] = useState(false)

  // Providers inject after load; check on mount, client-side only.
  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setHasPhantom(getPhantom() !== null)

    setHasWebln(getWebln() !== null)
  }, [])

  const isSolana = method.currency === 'sol' || method.currency === 'sol_usdc'
  const bolt11 = method.lightning_invoice

  const onPhantom = useCallback(async () => {
    const provider = getPhantom()
    if (!provider) return
    setState('paying')
    try {
      await payWithPhantom(provider, method)
      setState('sent')
    } catch {
      setState('failed')
    }
  }, [method])

  const onWebln = useCallback(async () => {
    const webln = getWebln()
    if (!webln || !bolt11) return
    setState('paying')
    try {
      await webln.enable()
      await webln.sendPayment(bolt11)
      setState('sent')
    } catch {
      setState('failed')
    }
  }, [bolt11])

  const showPhantom = isSolana && hasPhantom
  const showWebln = Boolean(bolt11) && hasWebln
  if (!showPhantom && !showWebln) return null

  return (
    <Box display="flex" flexDirection="column" rowGap="s">
      {showPhantom && (
        <Button
          onClick={() => void onPhantom()}
          loading={state === 'paying'}
          disabled={state === 'sent'}
          size="lg"
          className="w-full"
          data-testid="crypto-phantom-pay"
        >
          {t('checkout.crypto.payWithPhantom')}
        </Button>
      )}
      {showWebln && (
        <Button
          onClick={() => void onWebln()}
          loading={state === 'paying'}
          disabled={state === 'sent'}
          variant="secondary"
          size="lg"
          className="w-full"
          data-testid="crypto-webln-pay"
        >
          {t('checkout.crypto.payWithWebln')}
        </Button>
      )}
      {state === 'sent' && (
        <Text variant="caption" color="success">
          {t('checkout.crypto.walletPaySent')}
        </Text>
      )}
      {state === 'failed' && (
        <Text variant="caption" color="warning">
          {t('checkout.crypto.walletPayFailed')}
        </Text>
      )}
    </Box>
  )
}

'use client'

import { Button, Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'
import { Check, Copy, ExternalLink } from 'lucide-react'
import { useCallback, useState } from 'react'
import { explorerUrl } from './types'

export const CopyButton = ({
  value,
  label,
  copiedLabel,
}: {
  value: string
  label: string
  copiedLabel: string
}) => {
  const [copied, setCopied] = useState(false)
  const copy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(value)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch {
      // Clipboard blocked; the value is still visible for manual copy.
    }
  }, [value])
  return (
    <Button
      type="button"
      variant="ghost"
      size="sm"
      onClick={copy}
      aria-label={label}
      title={label}
    >
      {copied ? (
        <Box display="inline-flex" alignItems="center" columnGap="xs">
          <Check className="h-4 w-4" />
          <Text as="span" variant="caption" color="success">
            {copiedLabel}
          </Text>
        </Box>
      ) : (
        <Box display="inline-flex" alignItems="center" columnGap="xs">
          <Copy className="h-4 w-4" />
          <Text as="span" variant="caption" color="inherit">
            {label}
          </Text>
        </Box>
      )}
    </Button>
  )
}

export type NoticeTone = 'info' | 'good' | 'warn' | 'bad'

const NOTICE_BG = {
  info: 'background-pending',
  good: 'background-success',
  warn: 'background-warning',
  bad: 'background-danger',
} as const

const NOTICE_TEXT = {
  info: 'text-pending',
  good: 'text-success',
  warn: 'text-warning',
  bad: 'text-danger',
} as const

export const Notice = ({
  tone,
  title,
  children,
  testId,
}: {
  tone: NoticeTone
  title?: string
  children: React.ReactNode
  testId?: string
}) => (
  <Box
    role="status"
    borderRadius="m"
    padding="m"
    backgroundColor={NOTICE_BG[tone]}
    color={NOTICE_TEXT[tone]}
    display="flex"
    flexDirection="column"
    rowGap="xs"
    data-testid={testId}
  >
    {title && (
      <Text as="p" color="inherit">
        <strong>{title}</strong>
      </Text>
    )}
    <Text as="div" color="inherit">
      {children}
    </Text>
  </Box>
)

/** Full address, wrapped, with the first/last four characters emphasised. */
export const AddressBlock = ({
  address,
  copyLabel,
  copiedLabel,
}: {
  address: string
  copyLabel: string
  copiedLabel: string
}) => {
  const head = address.slice(0, 4)
  const tail = address.slice(-4)
  const middle = address.slice(4, -4)
  return (
    <Box
      display="flex"
      flexDirection="column"
      rowGap="s"
      borderRadius="m"
      borderWidth={1}
      borderStyle="solid"
      borderColor="border-primary"
      backgroundColor="background-card"
      paddingHorizontal="m"
      paddingVertical="s"
    >
      {/* Plain element: a 44-char address must break anywhere, which no
          Text prop expresses; the pieces inside carry the design tokens. */}
      <code
        className="font-mono text-xs leading-relaxed break-all"
        data-testid="crypto-address"
      >
        <Text as="span" variant="mono" color="accent">
          <strong>{head}</strong>
        </Text>
        {middle}
        <Text as="span" variant="mono" color="accent">
          <strong>{tail}</strong>
        </Text>
      </code>
      <Box display="flex" justifyContent="start">
        <CopyButton
          value={address}
          label={copyLabel}
          copiedLabel={copiedLabel}
        />
      </Box>
    </Box>
  )
}

export const CryptoAmount = ({
  amount,
  currency,
  approx,
  copyLabel,
  copiedLabel,
  testId,
}: {
  amount: string
  currency: string
  approx?: string | null
  copyLabel: string
  copiedLabel: string
  testId?: string
}) => (
  <Box
    display="flex"
    flexWrap="wrap"
    alignItems="baseline"
    columnGap="m"
    rowGap="xs"
  >
    <Text as="p" variant="heading-xs" data-testid={testId}>
      {amount}{' '}
      <Text as="span" color="muted">
        {currency}
      </Text>
    </Text>
    {approx && (
      <Text as="span" color="muted">
        {approx}
      </Text>
    )}
    <CopyButton value={amount} label={copyLabel} copiedLabel={copiedLabel} />
  </Box>
)

export const TxLinks = ({
  currency,
  hashes,
  label,
}: {
  currency: string
  hashes: string[]
  label: string
}) => {
  const links = hashes
    .map((h) => ({ hash: h, url: explorerUrl(currency, h) }))
    .filter((x): x is { hash: string; url: string } => x.url !== null)
  if (links.length === 0) return null
  return (
    <Box as="ul" display="flex" flexDirection="column" rowGap="xs">
      {links.map(({ hash, url }) => (
        <Box as="li" key={hash}>
          <a
            href={url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-xs text-blue-600 hover:underline dark:text-blue-400"
          >
            {label}{' '}
            <Text as="code" variant="mono" color="muted">
              {hash.slice(0, 6)}…{hash.slice(-4)}
            </Text>
            <ExternalLink className="h-3 w-3" />
          </a>
        </Box>
      ))}
    </Box>
  )
}

'use client'

import { StaticImage } from '@/components/Image/StaticImage'
import { iconFor } from './types'

export const CryptoTokenIcon = ({ token }: { token: string }) => (
  <StaticImage
    src={`/assets/crypto/${iconFor(token)}.svg`}
    alt={token}
    width={20}
    height={20}
    className="shrink-0"
  />
)

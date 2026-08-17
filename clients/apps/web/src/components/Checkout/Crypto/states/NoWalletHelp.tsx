'use client'

import type { TranslateFn } from '@polar-sh/i18n'
import { Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'

/**
 * A single collapsed link for the one open question a first-timer has:
 * "how do I even do this?" Closed by default so it never adds to the page
 * unless someone actually needs it.
 */
export const NoWalletHelp = ({ t }: { t: TranslateFn }) => (
  <details>
    <summary className="cursor-pointer text-xs text-blue-600 hover:underline dark:text-blue-400">
      {t('checkout.crypto.noWallet')}
    </summary>
    <Box paddingTop="xs">
      <Text variant="caption" color="muted">
        {t('checkout.crypto.noWalletHelp')}
      </Text>
    </Box>
  </details>
)

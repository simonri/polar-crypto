'use client'

import type { TranslateFn } from '@polar-sh/i18n'
import { Text } from '@polar-sh/orbit'
import { Box } from '@polar-sh/orbit/Box'

/**
 * Three compact steps for someone who has never paid with crypto, plus an
 * expandable "don't have a wallet?" explainer. No external links: the help
 * is inline so nobody leaves the payment page to find out how to pay.
 */
export const HowToSteps = ({ t }: { t: TranslateFn }) => {
  const steps = [
    {
      title: t('checkout.crypto.step1Title'),
      body: t('checkout.crypto.step1Body'),
    },
    {
      title: t('checkout.crypto.step2Title'),
      body: t('checkout.crypto.step2Body'),
    },
    {
      title: t('checkout.crypto.step3Title'),
      body: t('checkout.crypto.step3Body'),
    },
  ]
  return (
    <Box display="flex" flexDirection="column" rowGap="s">
      <Box
        display="grid"
        gridTemplateColumns={{ base: '1fr', sm: 'repeat(3, 1fr)' }}
        gap="s"
      >
        {steps.map((step, i) => (
          <Box
            key={step.title}
            backgroundColor="background-secondary"
            borderRadius="s"
            padding="s"
            display="flex"
            flexDirection="column"
          >
            <Text as="span" variant="caption">
              <strong>
                {i + 1} · {step.title}
              </strong>
            </Text>
            <Text as="span" variant="caption" color="muted">
              {step.body}
            </Text>
          </Box>
        ))}
      </Box>
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
    </Box>
  )
}

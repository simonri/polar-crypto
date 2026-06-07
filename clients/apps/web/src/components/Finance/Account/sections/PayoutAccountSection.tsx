'use client'

import { useOrganization } from '@/hooks/queries'
import { usePayoutAccountSetup } from '@/hooks/usePayoutAccountSetup'
import { schemas } from '@polar-sh/client'
import type { OrganizationReviewCheck } from './index'
import { Box } from '@polar-sh/orbit/Box'
import { Button } from '@polar-sh/orbit'
import { ArrowRight, BanknoteIcon, CheckIcon } from 'lucide-react'
import { PathCardBanner } from './PathCardBanner'
import { StatusBlock } from './StatusBlock'

interface Props {
  organization: schemas['Organization']
  step: OrganizationReviewCheck
  reasonItems: string[]
}

export const PayoutAccountSection = ({
  organization: initialOrg,
  step,
  reasonItems,
}: Props) => {
  const tone = step.status === 'failed' ? 'danger' : 'warning'
  const banners = reasonItems.length > 0 && (
    <Box display="flex" flexDirection="column" rowGap="m">
      {reasonItems.map((reason) => (
        <PathCardBanner key={reason} tone={tone} title={reason} />
      ))}
    </Box>
  )
  const { data: organization = initialOrg } = useOrganization(
    initialOrg.id,
    true,
    initialOrg,
  )
  const { payoutAccount, openManage, openPrimary, modals } =
    usePayoutAccountSetup(
      organization,
      `/dashboard/${organization.slug}/finance/account`,
    )

  if (payoutAccount) {
    const ready = payoutAccount.is_payout_ready
    return (
      <>
        <StatusBlock
          tone={ready ? 'success' : 'pending'}
          icon={ready ? CheckIcon : BanknoteIcon}
          title={ready ? 'Payout account ready' : 'Payout account needs setup'}
          description={
            ready
              ? 'Your payout account is configured. Add crypto wallet addresses to receive payouts.'
              : 'Your payout account is set up but not yet ready for payouts.'
          }
          action={
            <Button onClick={openManage}>
              Manage payout accounts
              <ArrowRight className="ml-2 h-4 w-4" />
            </Button>
          }
        />
        {modals}
        {banners}
      </>
    )
  }

  return (
    <>
      <StatusBlock
        tone="neutral"
        icon={BanknoteIcon}
        title="Set up payout account"
        description="Create a payout account and add crypto wallet addresses to receive payments."
        action={
          <Button onClick={openPrimary}>
            Set up payout account
            <ArrowRight className="ml-2 h-4 w-4" />
          </Button>
        }
      />
      {modals}
      {banners}
    </>
  )
}

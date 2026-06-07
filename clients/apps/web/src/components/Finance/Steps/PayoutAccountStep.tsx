'use client'

import { usePayoutAccountSetup } from '@/hooks/usePayoutAccountSetup'
import { schemas } from '@polar-sh/client'
import { Button } from '@polar-sh/orbit'
import { ArrowRight, CheckIcon } from 'lucide-react'
import { useCallback } from 'react'

interface PayoutAccountStepProps {
  organization: schemas['Organization']
}

export default function PayoutAccountStep({
  organization,
}: PayoutAccountStepProps) {
  const returnPath = `/dashboard/${organization.slug}/finance/account`
  const { payoutAccount, openPrimary, modals } = usePayoutAccountSetup(
    organization,
    returnPath,
  )

  const isAccountSetupComplete = payoutAccount && payoutAccount.is_payout_ready

  const handleStartAccountSetup = useCallback(async () => {
    openPrimary()
  }, [openPrimary])

  if (isAccountSetupComplete) {
    return (
      <div className="dark:bg-polar-800 rounded-2xl border bg-white p-8 text-center">
        <span className="dark:bg-polar-700 mb-3 inline-flex h-8 w-8 items-center justify-center rounded-full bg-gray-100">
          <CheckIcon className="dark:text-polar-400 h-4 w-4 text-gray-500" />
        </span>
        <h4 className="mb-2 font-medium">Account setup complete</h4>
        <p className="dark:text-polar-400 mx-auto mb-6 max-w-sm text-sm text-balance text-gray-600">
          Your payout account is configured. Add your crypto wallet addresses in
          Finance settings to receive payouts.
        </p>
      </div>
    )
  }

  return (
    <>
      <div className="dark:bg-polar-800 rounded-2xl border bg-white p-8 text-center">
        <h4 className="mb-2 font-medium">Set up payout account</h4>
        <p className="dark:text-polar-400 mx-auto mb-6 max-w-sm text-sm text-balance text-gray-600">
          Create a payout account, then add your crypto wallet addresses to
          receive payments.
        </p>
        <Button onClick={handleStartAccountSetup} className="w-auto">
          Continue with Account Setup
          <ArrowRight className="ml-2 h-4 w-4" />
        </Button>
      </div>
      {modals}
    </>
  )
}

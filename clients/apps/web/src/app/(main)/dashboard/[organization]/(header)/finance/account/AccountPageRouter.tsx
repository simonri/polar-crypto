'use client'

import { schemas } from '@polar-sh/client'
import { AccountPageApproved } from './AccountPageApproved'
import { AccountPageDetailsRequired } from './AccountPageDetailsRequired'

interface Props {
  organization: schemas['Organization']
}

export const AccountPageRouter = ({ organization }: Props) => {
  const hasSubmittedDetails = !!organization.details_submitted_at

  if (!hasSubmittedDetails) {
    return <AccountPageDetailsRequired organization={organization} />
  }

  return <AccountPageApproved organization={organization} />
}

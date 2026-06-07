'use client'

import { schemas } from '@polar-sh/client'
import ShadowBox from '@polar-sh/ui/components/atoms/ShadowBox'

export default function ClientPage({
  organization: _organization,
}: {
  organization: schemas['CustomerOrganization']
  invitationToken?: string
}) {
  return (
    <div className="flex flex-col items-center">
      <ShadowBox className="flex w-full max-w-2xl flex-col items-center gap-6 p-12">
        <div className="flex flex-col items-center gap-2 text-center">
          <h2 className="text-xl">Page Not Available</h2>
          <p className="dark:text-polar-500 text-gray-500">
            This feature is no longer available.
          </p>
        </div>
      </ShadowBox>
    </div>
  )
}

'use client'

import AccessRestricted from '@/components/Finance/AccessRestricted'
import { DashboardBody } from '@/components/Layout/DashboardLayout'
import FeatureSettings from '@/components/Settings/FeatureSettings'
import OrganizationAccessTokensSettings from '@/components/Settings/OrganizationAccessTokensSettings'
import OrganizationCustomerEmailSettings from '@/components/Settings/OrganizationCustomerEmailSettings'
import OrganizationCustomerPortalSettings from '@/components/Settings/OrganizationCustomerPortalSettings'
import OrganizationDeleteSettings from '@/components/Settings/OrganizationDeleteSettings'
import OrganizationPaymentSettings from '@/components/Settings/OrganizationPaymentSettings'
import OrganizationProfileSettings from '@/components/Settings/OrganizationProfileSettings'
import { Section, SectionDescription } from '@/components/Settings/Section'
import { useHasPermission } from '@/hooks/permissions'
import { schemas } from '@polar-sh/client'

export default function ClientPage({
  organization: org,
}: {
  organization: schemas['Organization']
}) {
  const canManageOrganization = useHasPermission(org.id, 'organization:manage')

  return (
    <DashboardBody
      wrapperClassName="max-w-(--breakpoint-sm)!"
      title="Preferences"
    >
      <div className="flex flex-col gap-y-12">
        <Section id="organization">
          <SectionDescription title="Organization" />
          <OrganizationProfileSettings
            organization={org}
            readOnly={!canManageOrganization}
          />
        </Section>

        <Section id="payments">
          <SectionDescription title="Payments" />
          <OrganizationPaymentSettings
            organization={org}
            readOnly={!canManageOrganization}
          />
        </Section>

        <Section id="customer_portal">
          <SectionDescription title="Customer portal" />
          <OrganizationCustomerPortalSettings
            organization={org}
            readOnly={!canManageOrganization}
          />
        </Section>

        <Section id="customer_emails">
          <SectionDescription
            title="Customer notifications"
            description="Emails automatically sent to customers for purchases, renewals, and other subscription lifecycle events"
          />
          <OrganizationCustomerEmailSettings
            organization={org}
            readOnly={!canManageOrganization}
          />
        </Section>

        <Section id="features">
          <SectionDescription
            title="Features"
            description="Manage alpha & beta features for your organization"
          />
          <FeatureSettings
            organization={org}
            readOnly={!canManageOrganization}
          />
        </Section>

        <Section id="developers">
          <SectionDescription
            title="Developers"
            description="Manage access tokens to authenticate with the Polar API"
          />
          {canManageOrganization === false ? (
            <AccessRestricted message="You don't have permission to manage access tokens for this organization. Ask an admin if you need access." />
          ) : (
            <OrganizationAccessTokensSettings organization={org} />
          )}
        </Section>

        <Section id="danger">
          <SectionDescription
            title="Danger Zone"
            description="Irreversible actions for this organization"
          />
          {canManageOrganization === false ? (
            <AccessRestricted message="You don't have permission to delete this organization." />
          ) : (
            <OrganizationDeleteSettings organization={org} />
          )}
        </Section>
      </div>
    </DashboardBody>
  )
}

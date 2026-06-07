import { Section } from '@/components/Layout/Section'
import { schemas } from '@polar-sh/client'
import { ProductMetadataForm } from '../ProductMetadataForm'
import { ProductCheckoutSection } from './ProductCheckoutSection'
import { ProductCustomerPortalSection } from './ProductCustomerPortalSection'
import { ProductInfoSection } from './ProductInfoSection'
import { ProductPricingSection } from './ProductPricingSection'

export type ProductFormType = Omit<
  schemas['ProductCreate'] | schemas['ProductUpdate'],
  'metadata'
> & {
  metadata: { key: string; value: string | number | boolean }[]
}

const ProductForm = ({
  organization,
  update,
}: {
  organization: schemas['Organization']
  update?: boolean
}) => {
  return (
    <div className="dark:divide-polar-700 flex flex-col divide-y">
      <ProductInfoSection />

      <ProductPricingSection organization={organization} update={update} />

      <Section
        title="Metadata"
        description="Optional metadata to associate with the product"
      >
        <ProductMetadataForm />
      </Section>

      <ProductCustomerPortalSection />

      <ProductCheckoutSection organization={organization} />
    </div>
  )
}

export default ProductForm

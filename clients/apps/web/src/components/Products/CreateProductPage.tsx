import { useToast } from '@/components/Toast/use-toast'
import { useCreateProduct } from '@/hooks/queries'
import {
  findFirstErrorMessage,
  setProductValidationErrors,
} from '@/utils/api/errors'
import { ProductEditOrCreateForm, productToCreateForm } from '@/utils/product'
import { isValidationError, schemas } from '@polar-sh/client'
import { Button } from '@polar-sh/orbit'
import { Form } from '@polar-sh/ui/components/ui/form'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import { useCallback, useState } from 'react'
import type { FieldErrors } from 'react-hook-form'
import { useForm } from 'react-hook-form'
import { DashboardBody } from '../Layout/DashboardLayout'
import { getStatusRedirect } from '../Toast/utils'
import ProductForm from './ProductForm/ProductForm'
import { Wand2Icon } from 'lucide-react'

export interface CreateProductPageProps {
  organization: schemas['Organization']
  sourceProduct?: schemas['Product']
  returnTo?: string
}

export const CreateProductPage = ({
  organization,
  sourceProduct,
  returnTo,
}: CreateProductPageProps) => {
  const router = useRouter()
  const { toast } = useToast()
  const [isSubmitting, setIsSubmitting] = useState(false)

  const getDefaultValues = () => {
    if (sourceProduct) {
      return productToCreateForm(sourceProduct)
    }

    return {
      recurring_interval: null,
      visibility: 'public' as const,
      prices: [
        {
          amount_type: 'fixed' as const,
          price_amount: 0,
          price_currency:
            organization.default_presentment_currency as schemas['PresentmentCurrency'],
        },
      ],
      medias: [],
      organization_id: organization.id,
      metadata: [],
    }
  }

  const form = useForm<ProductEditOrCreateForm>({
    defaultValues: getDefaultValues(),
  })
  const { handleSubmit, setError } = form

  const onInvalid = useCallback(
    (errors: FieldErrors<ProductEditOrCreateForm>) => {
      const message =
        findFirstErrorMessage(errors) ?? 'Please check the form for errors'
      toast({ title: 'Validation Error', description: message })
    },
    [toast],
  )

  const createProduct = useCreateProduct(organization)

  const onSubmit = useCallback(
    async (productCreate: ProductEditOrCreateForm) => {
      setIsSubmitting(true)
      try {
        const { metadata, ...productCreateRest } = productCreate

        const { data: product, error } = await createProduct.mutateAsync({
          ...productCreateRest,
          metadata: metadata.reduce(
            (acc, { key, value }) => ({ ...acc, [key]: value }),
            {},
          ),
        } as schemas['ProductCreate'])

        if (error) {
          if (isValidationError(error.detail)) {
            setProductValidationErrors(error.detail, setError)
            toast({
              title: 'Error',
              description: error.detail[0]?.msg || 'An error occurred',
            })
          } else {
            toast({
              title: 'Error',
              description: String(error.detail || 'An error occurred'),
            })
          }
          return
        }

        router.push(
          getStatusRedirect(
            returnTo ?? `/dashboard/${organization.slug}/products`,
            'Product Created',
            `Product ${product.name} was created successfully`,
          ),
        )
      } catch (e) {
        toast({
          title: 'Error',
          description:
            e instanceof Error ? e.message : 'An unexpected error occurred',
        })
      } finally {
        setIsSubmitting(false)
      }
    },
    [createProduct, router, returnTo, organization, toast, setError],
  )

  return (
    <DashboardBody
      title={sourceProduct ? 'Duplicate Product' : 'Create Product'}
      wrapperClassName="max-w-(--breakpoint-md)!"
      className="gap-y-16"
      header={
        !sourceProduct ? (
          <Link href={`/dashboard/${organization.slug}/products/new/ai`}>
            <Button variant="secondary">
              <Wand2Icon className="mr-2" />
              Create with AI
            </Button>
          </Link>
        ) : undefined
      }
    >
      <div className="dark:border-polar-700 dark:divide-polar-700 flex flex-col divide-y divide-gray-200 rounded-4xl border border-gray-200">
        <Form {...form}>
          <form
            onSubmit={handleSubmit(onSubmit, onInvalid)}
            className="flex flex-col gap-y-6"
          >
            <ProductForm organization={organization} update={false} />
          </form>
        </Form>
      </div>
      <div className="flex flex-row items-center gap-2 pb-12">
        <Button
          onClick={handleSubmit(onSubmit, onInvalid)}
          loading={isSubmitting}
          disabled={isSubmitting}
        >
          Create Product
        </Button>
      </div>
    </DashboardBody>
  )
}

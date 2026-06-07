import { useToast } from '@/components/Toast/use-toast'
import { useAlertIfUnsaved } from '@/hooks/editor'
import { useUpdateProduct } from '@/hooks/queries'
import {
  findFirstErrorMessage,
  setProductValidationErrors,
} from '@/utils/api/errors'
import { ProductEditOrCreateForm } from '@/utils/product'
import { isValidationError, schemas } from '@polar-sh/client'
import { Button } from '@polar-sh/orbit'
import { Form } from '@polar-sh/ui/components/ui/form'
import { useRouter } from 'next/navigation'
import { useCallback, useEffect } from 'react'
import type { FieldErrors } from 'react-hook-form'
import { useForm } from 'react-hook-form'
import { DashboardBody } from '../Layout/DashboardLayout'
import { getStatusRedirect } from '../Toast/utils'
import ProductForm from './ProductForm/ProductForm'

export interface EditProductPageProps {
  organization: schemas['Organization']
  product: schemas['Product']
}

export const EditProductPage = ({
  organization,
  product,
}: EditProductPageProps) => {
  const router = useRouter()
  const { toast } = useToast()

  const form = useForm<ProductEditOrCreateForm>({
    defaultValues: {
      ...product,
      medias: [],
      prices: product.prices.map((price) => ({
        ...price,
        price_currency: price.price_currency as schemas['PresentmentCurrency'],
      })),
      metadata: Object.entries(product.metadata).map(([key, value]) => ({
        key,
        value,
      })),
    },
  })
  const { handleSubmit, setError, formState } = form

  const onInvalid = useCallback(
    (errors: FieldErrors<ProductEditOrCreateForm>) => {
      const message =
        findFirstErrorMessage(errors) ?? 'Please check the form for errors'
      toast({ title: 'Validation Error', description: message })
    },
    [toast],
  )

  const alertOnUnsavedChanges = useAlertIfUnsaved()

  useEffect(() => {
    alertOnUnsavedChanges(formState.isDirty)
  }, [formState.isDirty, alertOnUnsavedChanges])

  const updateProduct = useUpdateProduct(organization)

  const onSubmit = useCallback(
    async (productUpdate: ProductEditOrCreateForm) => {
      try {
        const { metadata, ...productUpdateRest } = productUpdate

        const { data: updatedProduct, error } = await updateProduct.mutateAsync(
          {
            id: product.id,
            body: {
              ...productUpdateRest,
              medias: [],
              metadata: metadata.reduce(
                (acc, { key, value }) => ({ ...acc, [key]: value }),
                {},
              ),
            },
          },
        )

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
              description: String(
                ('detail' in error ? error.detail : null) ||
                  'An error occurred',
              ),
            })
          }
          return
        }

        router.push(
          getStatusRedirect(
            `/dashboard/${organization.slug}/products/${product.id}`,
            'Product Updated',
            `Product ${updatedProduct.name} was updated successfully`,
          ),
        )
      } catch (e) {
        toast({
          title: 'Error',
          description:
            e instanceof Error ? e.message : 'An unexpected error occurred',
        })
      }
    },
    [updateProduct, product.id, router, organization.slug, setError, toast],
  )

  const isLoading = updateProduct.isPending

  return (
    <DashboardBody
      title="Edit Product"
      wrapperClassName="max-w-(--breakpoint-md)!"
      className="gap-y-16"
      header={
        <Button
          onClick={handleSubmit(onSubmit, onInvalid)}
          loading={isLoading}
          disabled={isLoading}
        >
          Update Product
        </Button>
      }
    >
      <div className="dark:border-polar-700 dark:divide-polar-700 flex flex-col divide-y divide-gray-200 rounded-4xl border border-gray-200">
        <Form {...form}>
          <form
            onSubmit={handleSubmit(onSubmit, onInvalid)}
            className="flex flex-col gap-y-6"
          >
            <ProductForm organization={organization} update={true} />
          </form>
        </Form>
      </div>
      <div className="flex flex-row items-center gap-2 pb-12">
        <Button
          onClick={handleSubmit(onSubmit, onInvalid)}
          loading={isLoading}
          disabled={isLoading}
        >
          Update Product
        </Button>
      </div>
    </DashboardBody>
  )
}

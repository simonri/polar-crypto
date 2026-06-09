'use client'

import { useAuth } from '@/hooks'
import { useUpdateUser } from '@/hooks/queries'
import { useOnboardingV2Tracking } from '@/hooks/onboardingV2'
import { Box } from '@polar-sh/orbit/Box'
import { Button } from '@polar-sh/orbit'
import { Form } from '@polar-sh/ui/components/ui/form'
import { useRouter } from 'next/navigation'
import { useRef, useState } from 'react'
import { useForm, useWatch } from 'react-hook-form'
import { useOnboardingData } from './OnboardingContext'
import { OnboardingShell } from './OnboardingShell'
import { TermsCheckbox } from './TermsCheckbox'

interface FormSchema {
  accepted_terms_of_service: boolean
}

function SubmitButton({ loading }: { loading: boolean }) {
  const { accepted_terms_of_service } = useWatch<FormSchema>()

  return (
    <Button
      type="submit"
      loading={loading}
      disabled={!accepted_terms_of_service}
      fullWidth
    >
      Continue
    </Button>
  )
}

export function PersonalDetailsStep() {
  const router = useRouter()
  const { currentUser, reloadUser } = useAuth()
  const { setApiLoading, showApiResponse } = useOnboardingData()
  const { trackStepViewed, trackStepCompleted } = useOnboardingV2Tracking()
  const showTerms = useRef(!currentUser?.accepted_terms_of_service)
  const updateUser = useUpdateUser()
  const [submitting, setSubmitting] = useState(false)
  const [submitError, setSubmitError] = useState(false)

  trackStepViewed('personal')

  const form = useForm<FormSchema>({
    defaultValues: {
      accepted_terms_of_service:
        currentUser?.accepted_terms_of_service ?? false,
    },
  })

  const { control, handleSubmit, setValue } = form

  const onSubmit = async (formData: FormSchema) => {
    setSubmitting(true)
    setSubmitError(false)
    setApiLoading(true)

    try {
      const { error } = await updateUser.mutateAsync({
        ...(formData.accepted_terms_of_service
          ? { accepted_terms_of_service: true }
          : {}),
      })

      if (error) {
        setSubmitting(false)
        setSubmitError(true)
        await showApiResponse(400, 'Failed')
        return
      }
    } catch {
      setSubmitting(false)
      setSubmitError(true)
      return
    }

    trackStepCompleted('personal')
    await showApiResponse(200, 'OK')
    reloadUser()
    router.push('/onboarding/business')
  }

  return (
    <OnboardingShell
      title="Let's get started"
      subtitle={`Signed in as ${currentUser?.email ?? ''}.`}
      step="personal"
    >
      <Form {...form}>
        <Box
          as="form"
          onSubmit={handleSubmit(onSubmit)}
          display="flex"
          flexDirection="column"
          rowGap="xl"
        >
          {showTerms.current && (
            <TermsCheckbox
              control={control}
              name="accepted_terms_of_service"
              setValue={setValue}
            />
          )}

          <Box display="flex" flexDirection="column" rowGap="s">
            <SubmitButton loading={submitting} />
            {submitError && (
              <p className="text-sm text-red-500 dark:text-red-500">
                Something went wrong, please try again.
              </p>
            )}
          </Box>
        </Box>
      </Form>
    </OnboardingShell>
  )
}

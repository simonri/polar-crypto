'use client'

import { useExperiment } from '@/experiments/client'
import { schemas } from '@polar-sh/client'
import { useCallback, useMemo } from 'react'

const ONBOARDING_COOKIE_NAME = 'polar_onboarding_session'
const SESSION_TIMEOUT_HOURS = 24

export type OnboardingStep =
  | 'org'
  | 'product'
  | 'integrate'
  | 'personal'
  | 'business'
  | 'product_details'
export type SignupMethod = 'github' | 'google' | 'email'

export const inferSignupMethod = (
  oauthAccounts?: schemas['OAuthAccountRead'][],
): SignupMethod => {
  if (!oauthAccounts || oauthAccounts.length === 0) {
    return 'email'
  }

  if (oauthAccounts.some((account) => account.platform === 'github')) {
    return 'github'
  }

  if (oauthAccounts.some((account) => account.platform === 'google')) {
    return 'google'
  }

  return 'email'
}

export interface OnboardingSessionState {
  session_id: string
  started_at: string
  current_step: OnboardingStep
  steps_completed: number
  signup_method: SignupMethod
  experiment_variant?: string | null
}

const getOnboardingSession = (): OnboardingSessionState | null => {
  if (typeof document === 'undefined') return null

  const cookieValue = document.cookie
    .split('; ')
    .find((row) => row.startsWith(`${ONBOARDING_COOKIE_NAME}=`))
    ?.split('=')[1]

  if (!cookieValue) return null

  try {
    return JSON.parse(decodeURIComponent(cookieValue))
  } catch {
    return null
  }
}

const setOnboardingSession = (session: OnboardingSessionState): void => {
  if (typeof document === 'undefined') return

  const maxAge = SESSION_TIMEOUT_HOURS * 60 * 60
  const encoded = encodeURIComponent(JSON.stringify(session))
  document.cookie = `${ONBOARDING_COOKIE_NAME}=${encoded}; max-age=${maxAge}; path=/; SameSite=Lax`
}

const clearOnboardingSession = (): void => {
  if (typeof document === 'undefined') return
  document.cookie = `${ONBOARDING_COOKIE_NAME}=; max-age=0; path=/`
}

interface UseOnboardingTrackingReturn {
  startOnboarding: (signupMethod: SignupMethod) => OnboardingSessionState | null
  trackStepStarted: (step: OnboardingStep, organizationId?: string) => void
  trackStepCompleted: (step: OnboardingStep, organizationId?: string) => void
  trackStepSkipped: (step: OnboardingStep, organizationId?: string) => void
  trackCompleted: (organizationId: string) => void
  getSession: () => OnboardingSessionState | null
  clearSession: () => void
  experimentVariant: string
}

export const useOnboardingTracking = (): UseOnboardingTrackingReturn => {
  const { variant: experimentVariant } = useExperiment('onboarding_flow_v1', {
    trackExposure: false,
  })

  const startOnboarding = useCallback(
    (signupMethod: SignupMethod): OnboardingSessionState | null => {
      const existingSession = getOnboardingSession()

      if (existingSession) {
        return existingSession
      }

      const sessionId = crypto.randomUUID()
      const startedAt = new Date().toISOString()

      const session: OnboardingSessionState = {
        session_id: sessionId,
        started_at: startedAt,
        current_step: 'org',
        steps_completed: 0,
        signup_method: signupMethod,
        experiment_variant: experimentVariant,
      }

      setOnboardingSession(session)
      return session
    },
    [experimentVariant],
  )

  const trackStepStarted = useCallback((step: OnboardingStep): void => {
    const session = getOnboardingSession()
    if (!session || session.current_step === step) return
    setOnboardingSession({ ...session, current_step: step })
  }, [])

  const trackStepCompleted = useCallback((): void => {
    const session = getOnboardingSession()
    if (!session) return
    setOnboardingSession({
      ...session,
      steps_completed: session.steps_completed + 1,
    })
  }, [])

  const trackStepSkipped = useCallback((): void => {}, [])

  const trackCompleted = useCallback((): void => {
    clearOnboardingSession()
  }, [])

  const getSession = useCallback((): OnboardingSessionState | null => {
    return getOnboardingSession()
  }, [])

  const clearSession = useCallback((): void => {
    clearOnboardingSession()
  }, [])

  return useMemo(
    () => ({
      startOnboarding,
      trackStepStarted,
      trackStepCompleted,
      trackStepSkipped,
      trackCompleted,
      getSession,
      clearSession,
      experimentVariant,
    }),
    [
      startOnboarding,
      trackStepStarted,
      trackStepCompleted,
      trackStepSkipped,
      trackCompleted,
      getSession,
      clearSession,
      experimentVariant,
    ],
  )
}

'use client'

import { useMemo } from 'react'
import { useExperimentContext } from './ExperimentProvider'
import {
  type ExperimentName,
  type ExperimentResult,
  type ExperimentVariant,
  experiments,
  getDefaultVariant,
} from './index'

function getUrlOverride<T extends ExperimentName>(
  experimentName: T,
): ExperimentVariant<T> | null {
  if (typeof window === 'undefined') return null
  const params = new URLSearchParams(window.location.search)
  const value = params.get(`experiment_${experimentName}`)
  if (!value) return null
  const validVariants = experiments[experimentName]
    .variants as readonly string[]
  if (validVariants.includes(value)) {
    return value as ExperimentVariant<T>
  }
  return null
}

export interface UseExperimentOptions {
  trackExposure?: boolean
}

export function useExperiment<T extends ExperimentName>(
  experimentName: T,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _options?: UseExperimentOptions,
): ExperimentResult<T> {
  const experimentContext = useExperimentContext()

  const urlOverride = useMemo(
    () => getUrlOverride(experimentName),
    [experimentName],
  )

  const variant =
    urlOverride ??
    experimentContext[experimentName] ??
    getDefaultVariant(experimentName)

  return useMemo(
    () => ({
      variant,
      isControl: variant === 'control',
      isTreatment: variant === 'treatment',
    }),
    [variant],
  )
}

import {
  type ExperimentName,
  type ExperimentVariant,
  getDefaultVariant,
} from './index'

export async function getExperiments<T extends ExperimentName>(
  experimentNames: T[],
): Promise<Record<T, ExperimentVariant<T>>> {
  const results = experimentNames.map(
    (name) => [name, getDefaultVariant(name)] as const,
  )
  return Object.fromEntries(results) as Record<T, ExperimentVariant<T>>
}

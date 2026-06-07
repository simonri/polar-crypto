export type MeterUnit = 'scalar' | 'token' | 'custom'

export const METER_UNIT_DISPLAY_NAMES: Record<MeterUnit, string> = {
  scalar: 'Scalar',
  token: 'Token',
  custom: 'Custom',
}

interface MeterUnitFormat {
  scale: number
  label: string
  formatValue: (value: number) => string
}

export function getMeterUnitFormat(
  unit: MeterUnit,
  customLabel?: string | null,
  customMultiplier?: number | null,
): MeterUnitFormat {
  if (unit === 'token') {
    return {
      scale: 1_000_000,
      label: '1M tokens',
      formatValue: (v) =>
        new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(
          v / 1_000_000,
        ) + 'M',
    }
  }

  if (unit === 'custom' && customLabel) {
    const scale = customMultiplier ?? 1
    return {
      scale,
      label: scale > 1 ? `${scale.toLocaleString()} ${customLabel}` : customLabel,
      formatValue: (v) =>
        new Intl.NumberFormat('en-US', { maximumFractionDigits: 2 }).format(
          v / scale,
        ),
    }
  }

  return {
    scale: 1,
    label: 'unit',
    formatValue: (v) =>
      new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 }).format(v),
  }
}

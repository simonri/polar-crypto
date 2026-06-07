interface FormattedUnitsProps {
  value: number
}

const FormattedUnits = ({ value }: FormattedUnitsProps) => {
  const formatted = new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 0,
  }).format(value)

  return <>{formatted}</>
}

export default FormattedUnits

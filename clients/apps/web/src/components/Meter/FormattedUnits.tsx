interface FormattedUnitsProps {
  value: number
}

const FormattedUnits = ({ value }: FormattedUnitsProps) => {
  const formatted = new Intl.NumberFormat('en-US', {
    maximumFractionDigits: 0,
  }).format(value)

  // eslint-disable-next-line react/jsx-no-useless-fragment
  return <>{formatted}</>
}

export default FormattedUnits

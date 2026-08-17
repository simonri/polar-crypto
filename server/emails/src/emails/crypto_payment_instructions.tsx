import {
  Button,
  FooterCustomer,
  Intro,
  Text,
  WrapperOrganization,
} from '../components/foundation'
import { organization } from '../preview'
import type { schemas } from '../types'

export function CryptoPaymentInstructions({
  email,
  organization,
  product_name,
  amount_display,
  url,
  expiry_minutes,
}: schemas['CryptoPaymentInstructionsProps']) {
  return (
    <WrapperOrganization
      organization={organization}
      preview={`Complete your ${product_name} payment`}
    >
      <Intro headline="Complete your payment">
        Your order of{' '}
        <Text as="span" weight="medium">
          {product_name}
        </Text>{' '}
        ({amount_display}) is waiting for payment. Use the link below to pay
        from another device, or to pick up where you left off.
      </Intro>
      <Button href={url}>Open payment page</Button>
      <Text variant="caption" align="center">
        The exact amount is locked for {expiry_minutes} minutes at a time
        because crypto prices move; the payment page refreshes it for you.
        Nothing is charged until you send the payment. Already paid? We confirm
        automatically and will email your receipt.
      </Text>
      <FooterCustomer organization={organization} email={email} />
    </WrapperOrganization>
  )
}

CryptoPaymentInstructions.PreviewProps = {
  email: 'john@example.com',
  organization,
  product_name: 'Pro Plan',
  amount_display: '$49.00',
  url: 'https://polar.sh/checkout/polar_c_123',
  expiry_minutes: 15,
}

export default CryptoPaymentInstructions

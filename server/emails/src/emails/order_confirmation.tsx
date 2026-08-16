import {
  Button,
  Divider,
  EmailLink,
  FooterCustomer,
  Intro,
  Text,
  WrapperOrganization,
} from '../components/foundation'
import Benefits from '../components/Benefits'
import OrderSummary from '../components/OrderSummary'
import { order, organization, product } from '../preview'
import type { schemas } from '../types'

export function OrderConfirmation({
  email,
  organization,
  product,
  order,
  url,
}: schemas['OrderConfirmationProps']) {
  return (
    <WrapperOrganization
      organization={organization}
      preview={`Your ${order.description} purchase`}
    >
      <Intro headline="Thank you for your purchase!">
        Thank you for purchasing{' '}
        <Text as="span" weight="medium">
          {order.description}
        </Text>
        . Your invoice is attached.
        {order.receipt_number && (
          <>
            {' '}
            You can find your receipt in the{' '}
            <EmailLink href={url}>Customer Portal</EmailLink>.
          </>
        )}
      </Intro>
      {product && (
        <>
          {product.benefits.length > 0 && (
            <Benefits benefits={product.benefits} />
          )}
          <Button href={url}>Access purchase</Button>
        </>
      )}
      <Divider />
      <OrderSummary order={order} />
      {order.crypto_payment && (
        <Text variant="caption" align="center">
          Paid with {order.crypto_payment.amount}{' '}
          {order.crypto_payment.currency}
          {order.crypto_payment.tx_hash && (
            <>
              {' · transaction '}
              {order.crypto_payment.explorer_url ? (
                <EmailLink href={order.crypto_payment.explorer_url}>
                  {order.crypto_payment.tx_hash.slice(0, 10)}…
                </EmailLink>
              ) : (
                `${order.crypto_payment.tx_hash.slice(0, 10)}…`
              )}
            </>
          )}
        </Text>
      )}
      <FooterCustomer organization={organization} email={email} />
    </WrapperOrganization>
  )
}

OrderConfirmation.PreviewProps = {
  email: 'john@example.com',
  organization,
  product,
  order,
  url: 'https://polar.sh/acme-inc/portal/orders/12345',
}

export default OrderConfirmation

from polar.customer.repository import CustomerRepository
from polar.exceptions import PolarError, PolarRequestValidationError
from polar.models import Customer as CustomerModel
from polar.postgres import AsyncSession

from ..schemas.customer import (
    CustomerPortalCustomerUpdate,
)


class CustomerError(PolarError): ...


class CustomerNotReady(CustomerError):
    def __init__(self, customer: CustomerModel) -> None:
        self.customer = customer
        super().__init__("Customer is not ready for this operation.", 403)


class CustomerService:
    async def update(
        self,
        session: AsyncSession,
        customer: CustomerModel,
        customer_update: CustomerPortalCustomerUpdate,
    ) -> CustomerModel:
        if customer_update.billing_name is not None:
            customer.billing_name = customer_update.billing_name

        if "billing_address" in customer_update.model_fields_set:
            if customer_update.billing_address is None:
                raise PolarRequestValidationError(
                    [
                        {
                            "type": "missing",
                            "loc": ("body", "billing_address"),
                            "msg": "Customer billing address cannot be reset to null once set.",
                            "input": customer_update.billing_address,
                        }
                    ]
                )
            else:
                customer.billing_address = customer_update.billing_address

        repository = CustomerRepository.from_session(session)
        customer = await repository.update(
            customer,
            update_dict=customer_update.model_dump(
                exclude_unset=True,
                exclude={"billing_name", "billing_address"},
            ),
        )
        return customer


customer = CustomerService()

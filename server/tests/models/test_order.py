import pytest

from polar.models import Customer, Product
from tests.fixtures.database import SaveFixture
from tests.fixtures.random_objects import create_order


@pytest.mark.asyncio
async def test_order_basic(
    save_fixture: SaveFixture,
    product: Product,
    customer: Customer,
) -> None:
    """Basic test to verify Order creation without tax fields."""
    order = await create_order(
        save_fixture,
        product=product,
        customer=customer,
        subtotal_amount=1000,
    )
    assert order.refunded_amount == 0
    assert order.subtotal_amount == 1000
    assert order.net_amount == 1000

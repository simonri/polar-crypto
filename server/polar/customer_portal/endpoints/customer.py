import json

import structlog
from fastapi import Depends, Request
from fastapi.responses import Response
from sse_starlette import EventSourceResponse

from polar.customer.service import customer as main_customer_service
from polar.eventstream.endpoints import subscribe
from polar.eventstream.service import Receivers
from polar.models import Customer
from polar.openapi import APITag
from polar.postgres import (
    AsyncReadSession,
    AsyncSession,
    get_db_read_session,
    get_db_session,
)
from polar.redis import Redis, get_redis
from polar.routing import APIRouter

from .. import auth
from ..schemas.customer import (
    CustomerPortalCustomer,
    CustomerPortalCustomerUpdate,
)
from ..service.customer import customer as customer_service
from ..utils import get_customer, get_customer_id

log = structlog.get_logger()

router = APIRouter(prefix="/customers", tags=["customers", APITag.public])


@router.get("/stream", include_in_schema=False)
async def stream(
    request: Request,
    auth_subject: auth.CustomerPortalUnionRead,
    session: AsyncSession = Depends(get_db_session),
    redis: Redis = Depends(get_redis),
) -> EventSourceResponse:
    await session.commit()
    receivers = Receivers(customer_id=get_customer_id(auth_subject))
    channels = receivers.get_channels()
    return EventSourceResponse(subscribe(redis, channels, request))


@router.get("/me", summary="Get Customer", response_model=CustomerPortalCustomer)
async def get(auth_subject: auth.CustomerPortalUnionRead) -> Customer:
    """Get authenticated customer."""
    return get_customer(auth_subject)


@router.get(
    "/me/export",
    summary="Export Customer Data",
    tags=[APITag.private],
    responses={
        200: {
            "content": {"application/json": {"schema": {"type": "object"}}},
            "description": "Customer data exported as a JSON file.",
        }
    },
)
async def export(
    auth_subject: auth.CustomerPortalUnionRead,
    session: AsyncReadSession = Depends(get_db_read_session),
) -> Response:
    """Export all data for the authenticated customer as a JSON file."""
    customer = get_customer(auth_subject)
    data = await main_customer_service.get_export(session, customer)
    filename = f"polar-customer-export-{customer.id}.json"
    return Response(
        content=json.dumps(data, indent=2),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.patch(
    "/me",
    summary="Update Customer",
    responses={
        200: {"description": "Customer updated."},
    },
    response_model=CustomerPortalCustomer,
)
async def update(
    customer_update: CustomerPortalCustomerUpdate,
    auth_subject: auth.CustomerPortalUnionBillingWrite,
    session: AsyncSession = Depends(get_db_session),
) -> Customer:
    """Update authenticated customer."""
    return await customer_service.update(
        session, get_customer(auth_subject), customer_update
    )

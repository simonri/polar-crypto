from polar.routing import APIRouter

from .customer import router as customer_router
from .customer_email_update import router as customer_email_update_router
from .customer_session import router as customer_session_router
from .member import router as member_router
from .order import router as order_router
from .organization import router as organization_router
from .subscription import router as subscription_router
from .wallet import router as wallet_router

router = APIRouter(prefix="/customer-portal", tags=["customer_portal"])

router.include_router(customer_router)
router.include_router(customer_email_update_router)
router.include_router(customer_session_router)
router.include_router(member_router)
router.include_router(order_router)
router.include_router(organization_router)
router.include_router(subscription_router)
router.include_router(wallet_router)

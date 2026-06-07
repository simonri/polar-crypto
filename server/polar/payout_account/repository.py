from uuid import UUID

from sqlalchemy import Select

from polar.auth.models import User
from polar.kit.repository import (
    RepositoryBase,
    RepositorySoftDeletionIDMixin,
    RepositorySoftDeletionMixin,
)
from polar.models import PayoutAccount


class PayoutAccountRepository(
    RepositorySoftDeletionIDMixin[PayoutAccount, UUID],
    RepositorySoftDeletionMixin[PayoutAccount],
    RepositoryBase[PayoutAccount],
):
    model = PayoutAccount

    def get_statement_by_user(self, user: User) -> Select[tuple[PayoutAccount]]:
        return self.get_base_statement().where(PayoutAccount.admin_id == user.id)

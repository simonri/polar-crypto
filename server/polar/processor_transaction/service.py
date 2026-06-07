from polar.postgres import AsyncSession


class ProcessorTransactionService:
    async def sync_stripe(self, session: AsyncSession) -> None:
        # Stripe balance transaction sync removed
        pass


processor_transaction = ProcessorTransactionService()

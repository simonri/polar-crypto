from typing import Literal

import typer
from sqlalchemy import select, update

from polar.models import Customer, Product
from scripts.helper import (
    configure_script_logging,
    limit_bindparam,
    run_batched_update,
    typer_async,
)

cli = typer.Typer()

configure_script_logging()

products_subquery = (
    select(Product.id)
    .where(
        Product.search_vector.is_(None),
    )
    .order_by(Product.id)
    .limit(limit_bindparam())
    .scalar_subquery()
)
products_update_statement = (
    update(Product).values(name=Product.name).where(Product.id.in_(products_subquery))
)

customers_subquery = (
    select(Customer.id)
    .where(
        Customer.search_vector.is_(None),
    )
    .order_by(Customer.id)
    .limit(limit_bindparam())
    .scalar_subquery()
)
customers_update_statement = (
    update(Customer)
    .values(name=Customer.name)
    .where(Customer.id.in_(customers_subquery))
)


@cli.command()
@typer_async
async def search_vectors_backfill(
    entity: Literal["product", "customer"] = typer.Argument(),
    batch_size: int = typer.Option(5000, help="Number of rows to process per batch"),
    sleep_seconds: float = typer.Option(0.1, help="Seconds to sleep between batches"),
) -> None:
    match entity:
        case "product":
            statement_to_run = products_update_statement
        case "customer":
            statement_to_run = customers_update_statement

    await run_batched_update(
        statement_to_run,
        batch_size=batch_size,
        sleep_seconds=sleep_seconds,
    )


if __name__ == "__main__":
    cli()

from __future__ import annotations

from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.factors.compute_common_factors import compute_and_store_common_factors
from croesus.prices.ingest_prices import ingest_daily_prices


def main() -> None:
    migrate()
    with get_connection() as conn:
        price_result = ingest_daily_prices(conn)
        factor_result = compute_and_store_common_factors(conn)
    print(
        "daily run complete: "
        f"{len(price_result.succeeded)} price downloads succeeded, "
        f"{len(price_result.failed)} failed, "
        f"{len(factor_result.computed)} assets with factors"
    )


if __name__ == "__main__":
    main()

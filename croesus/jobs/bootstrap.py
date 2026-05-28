from __future__ import annotations

from croesus.assets.seed_us_equities import seed_us_equities
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate


def main() -> None:
    migrate()
    with get_connection() as conn:
        seed_us_equities(conn)
    print("bootstrap complete: schema applied and seed assets inserted")


if __name__ == "__main__":
    main()

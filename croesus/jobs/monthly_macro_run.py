"""
Monthly macro job: CPI, PCE, PMI, GDP, unemployment, M2, wages.

Usage:
    python -m croesus.jobs.monthly_macro_run
"""
from __future__ import annotations

import logging
from datetime import date

from croesus.db.migrate import migrate
from croesus.macro._loader import load_raw, store_macro_state
from croesus.macro.data_sources.fred_source import (
    DAILY_SERIES,
    MONTHLY_SERIES,
    QUARTERLY_SERIES,
    WEEKLY_SERIES,
)
from croesus.macro.engine import compute_macro_state
from croesus.macro.report import save_report

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


def main() -> None:
    migrate()
    today = date.today()
    logger.info("Monthly macro run for %s", today)

    raw = load_raw(
        fred_series=DAILY_SERIES + WEEKLY_SERIES + MONTHLY_SERIES + QUARTERLY_SERIES,
        include_sentiment=True,
    )
    state = compute_macro_state(today, raw)

    store_macro_state(state)
    md_path, csv_path = save_report(state, raw_indicators=state.raw_indicators)

    logger.info(
        "MacroState: regime=%s positioning=%s amp=%.1f conf=%.2f",
        state.regime,
        state.positioning,
        state.amplifier_score,
        state.confirmation_score,
    )
    logger.info("Report written to %s", md_path)


if __name__ == "__main__":
    main()

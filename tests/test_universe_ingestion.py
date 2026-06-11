"""Sprint 008c: index-universe ingestion — idempotent, dedup, loud failures."""
from pathlib import Path

import pandas as pd
import pytest

from croesus.assets.ingest_universe import (
    UNIVERSE_SOURCE,
    ingest_universe,
    normalize_symbol,
)
from croesus.assets.repository import AssetRepository
from croesus.assets.seed_us_equities import seed_us_equities
from croesus.assets.universe_sources.base import UniverseConstituent
from croesus.assets.universe_sources.wikipedia import constituents_from_tables
from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.jobs.universe_refresh import (
    UniverseRefreshError,
    run_universe_refresh,
    summarize,
)


class FakeSource:
    def __init__(self, source_name: str, constituents, error: Exception | None = None):
        self.source_name = source_name
        self._constituents = constituents
        self._error = error

    def fetch_constituents(self):
        if self._error is not None:
            raise self._error
        return list(self._constituents)


def _sp500_like():
    return FakeSource(
        "fake_sp500",
        [
            UniverseConstituent("AAPL", "Apple Inc.", "Information Technology",
                                "Technology Hardware", index_name="sp500"),
            UniverseConstituent("BRK.B", "Berkshire Hathaway", "Financials",
                                "Multi-Sector Holdings", index_name="sp500"),
            UniverseConstituent("JNJ", "Johnson & Johnson", "Health Care",
                                "Pharmaceuticals", index_name="sp500"),
        ],
    )


def _nasdaq100_like():
    return FakeSource(
        "fake_nasdaq100",
        [
            UniverseConstituent("AAPL", "Apple Inc.", "Information Technology",
                                "Technology Hardware", index_name="nasdaq100"),
            UniverseConstituent("PDD", "PDD Holdings", "Consumer Discretionary",
                                "Broadline Retail", index_name="nasdaq100"),
        ],
    )


def _open(tmp_path: Path):
    db_path = tmp_path / "u.duckdb"
    migrate(db_path)
    return get_connection(db_path)


def test_registers_constituents_and_dedups_overlapping_symbols(tmp_path: Path) -> None:
    with _open(tmp_path) as conn:
        result = ingest_universe(conn, [_sp500_like(), _nasdaq100_like()])
        assets = AssetRepository(conn).list_active()

    assert result.added == 4  # AAPL counted once despite appearing in both indices
    assert result.fetched == {"fake_sp500": 3, "fake_nasdaq100": 2}

    by_symbol = {a.symbol: a for a in assets}
    aapl = by_symbol["AAPL"]
    assert aapl.asset_id == "US_EQ_AAPL"
    assert aapl.metadata["indices"] == ["nasdaq100", "sp500"]
    assert aapl.source == UNIVERSE_SOURCE
    assert aapl.asset_type == "equity"
    assert aapl.country == "US" and aapl.currency == "USD"

    # Share-class dots normalize to the yfinance dash form, id stays safe.
    brk = by_symbol["BRK-B"]
    assert brk.asset_id == "US_EQ_BRK_B"


def test_rerun_is_idempotent(tmp_path: Path) -> None:
    sources = [_sp500_like(), _nasdaq100_like()]
    with _open(tmp_path) as conn:
        first = ingest_universe(conn, sources)
        second = ingest_universe(conn, sources)
        count = conn.execute("SELECT COUNT(*) FROM assets").fetchone()[0]

    assert first.added == 4
    assert second.added == 0 and second.updated == 0
    assert second.unchanged == 4
    assert count == 4


def test_does_not_overwrite_manually_seeded_assets(tmp_path: Path) -> None:
    source = FakeSource(
        "fake_sp500",
        [UniverseConstituent("AAPL", "Apple Computer (wiki)", "Information Technology",
                             "Technology Hardware", index_name="sp500")],
    )
    with _open(tmp_path) as conn:
        seed_us_equities(conn)
        result = ingest_universe(conn, [source])
        aapl = next(
            a for a in AssetRepository(conn).list_active() if a.symbol == "AAPL"
        )

    assert result.added == 0 and result.updated == 1
    # Curated fields stay; only the index membership metadata was attached.
    assert aapl.name == "Apple Inc."
    assert aapl.sector == "Technology"
    assert aapl.source == "manual_seed"
    assert aapl.metadata["indices"] == ["sp500"]


def test_partial_source_failure_degrades_loudly(tmp_path: Path) -> None:
    broken = FakeSource("fake_nasdaq100", [], error=ValueError("page layout changed"))
    with _open(tmp_path) as conn:
        result = run_universe_refresh(conn, [_sp500_like(), broken])
        issues = conn.execute(
            "SELECT code, severity FROM data_quality_issues"
        ).fetchall()

    assert result.added == 3  # the healthy index still landed
    assert "fake_nasdaq100" in result.failed_sources
    assert ("UNIVERSE_SOURCE_FAILED", "warn") in issues
    assert "failed_sources=fake_nasdaq100" in summarize(result)


def test_all_sources_failing_raises(tmp_path: Path) -> None:
    broken = FakeSource("fake_sp500", [], error=OSError("network down"))
    with _open(tmp_path) as conn:
        with pytest.raises(UniverseRefreshError, match="all universe sources failed"):
            run_universe_refresh(conn, [broken])


def test_normalize_symbol_handles_share_classes_and_noise() -> None:
    assert normalize_symbol(" brk.b ") == "BRK-B"
    assert normalize_symbol("AAPL") == "AAPL"
    assert normalize_symbol("  ") == ""


def test_constituents_from_tables_finds_table_by_columns() -> None:
    decoy = pd.DataFrame({"Date": ["2024-01-01"], "Event": ["rebalance"]})
    sp500_style = pd.DataFrame(
        {
            "Symbol": ["AAPL", "BRK.B"],
            "Security": ["Apple Inc.", "Berkshire Hathaway"],
            "GICS Sector": ["Information Technology", "Financials"],
            "GICS Sub-Industry": ["Technology Hardware", "Multi-Sector Holdings"],
        }
    )
    rows = constituents_from_tables([decoy, sp500_style], index_name="sp500")
    assert [c.symbol for c in rows] == ["AAPL", "BRK.B"]
    assert rows[0].sector == "Information Technology"
    assert rows[0].index_name == "sp500"

    nasdaq_style = pd.DataFrame(
        {"Ticker": ["PDD"], "Company": ["PDD Holdings"]}
    )
    rows = constituents_from_tables([nasdaq_style], index_name="nasdaq100")
    assert rows == [
        UniverseConstituent("PDD", "PDD Holdings", None, None, index_name="nasdaq100")
    ]

    assert constituents_from_tables([decoy], index_name="sp500") == []

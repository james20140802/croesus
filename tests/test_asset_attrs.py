from croesus.db.connection import get_connection
from croesus.db.migrate import migrate
from croesus.portfolio.asset_attrs import load_asset_attrs


def _seed_asset(conn, asset_id, **kw):
    conn.execute(
        """INSERT INTO assets
           (asset_id, symbol, name, asset_type, country, exchange, currency,
            sector, industry, is_active, source, metadata)
           VALUES (?, ?, ?, ?, ?, 'NMS', ?, ?, ?, true, 'test', ?)""",
        [
            asset_id, kw.get("symbol", asset_id), kw.get("name", asset_id),
            kw.get("asset_type", "equity"), kw.get("country", "US"),
            kw.get("currency", "USD"), kw.get("sector", "Technology"),
            kw.get("industry", "Software"), kw.get("metadata", '{"theme_tags": ["ai"]}'),
        ],
    )


def test_load_asset_attrs_parses_theme_tags_and_skips_cash(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        _seed_asset(conn, "EQ1", sector="Technology", industry="Software")
        attrs = load_asset_attrs(conn, ["EQ1", "CASH_USD", "EQ1"])
    assert set(attrs) == {"EQ1"}
    assert attrs["EQ1"].sector == "Technology"
    assert attrs["EQ1"].asset_type == "equity"
    assert attrs["EQ1"].theme_tags == ["ai"]


def test_load_asset_attrs_empty_returns_empty(tmp_path):
    db = str(tmp_path / "t.duckdb")
    migrate(db)
    with get_connection(db) as conn:
        assert load_asset_attrs(conn, []) == {}
        assert load_asset_attrs(conn, ["CASH_USD"]) == {}

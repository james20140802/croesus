from __future__ import annotations

import duckdb

from croesus.news.models import (
    RELATION_QUERIED,
    RELATION_RELATED,
    NewsItem,
    RawNewsArticle,
    make_item_id,
)


class NewsRepository:
    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    def upsert_articles(
        self,
        source: str,
        articles: list[RawNewsArticle],
        *,
        symbol_to_asset: dict[str, str],
    ) -> int:
        """Upsert articles and their asset links. ``symbol_to_asset`` maps a
        ticker symbol to an asset_id; tickers not in the map (outside our
        universe) are not linked. Returns the number of article rows submitted.
        """
        if not articles:
            return 0
        item_rows = []
        link_rows = []
        for art in articles:
            item_id = make_item_id(source, art.external_id)
            item_rows.append(
                (
                    item_id, source, art.external_id, art.url, art.headline,
                    art.summary, None, art.published_at, art.source_name, art.category,
                )
            )
            for position, symbol in enumerate(art.tickers):
                asset_id = symbol_to_asset.get(symbol)
                if asset_id is None:
                    continue
                relation = RELATION_QUERIED if position == 0 else RELATION_RELATED
                link_rows.append((item_id, asset_id, relation))

        # The article rows and their asset links must land together — wrap both
        # writes in one transaction so a mid-write failure can't leave a
        # news_items row orphaned (no link → invisible to load_for_asset's JOIN).
        self.conn.execute("BEGIN TRANSACTION")
        try:
            self.conn.executemany(
                """
                INSERT INTO news_items (
                  item_id, source, external_id, url, headline, summary, body,
                  published_at, source_name, category
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT (item_id) DO UPDATE SET
                  url = excluded.url,
                  headline = excluded.headline,
                  summary = excluded.summary,
                  body = COALESCE(excluded.body, news_items.body),
                  published_at = excluded.published_at,
                  source_name = excluded.source_name,
                  category = excluded.category
                """,
                item_rows,
            )
            if link_rows:
                self.conn.executemany(
                    """
                    INSERT INTO news_item_assets (item_id, asset_id, relation)
                    VALUES (?, ?, ?)
                    -- Only ever promote (related -> queried), never downgrade: a
                    -- direct ticker query is a stronger signal than a co-mention.
                    ON CONFLICT (item_id, asset_id) DO UPDATE SET relation =
                      CASE WHEN news_item_assets.relation = 'queried'
                           THEN 'queried' ELSE excluded.relation END
                    """,
                    link_rows,
                )
            self.conn.execute("COMMIT")
        except Exception:
            self.conn.execute("ROLLBACK")
            raise
        return len(item_rows)

    def load_for_asset(self, asset_id: str, *, limit: int = 50) -> list[NewsItem]:
        """Most-recent-first articles linked to one asset (for C2 / the grader)."""
        rows = self.conn.execute(
            """
            SELECT i.item_id, i.source, i.external_id, i.url, i.headline, i.summary,
                   i.body, i.published_at, i.source_name, i.category
            FROM news_items i
            JOIN news_item_assets l ON l.item_id = i.item_id
            WHERE l.asset_id = ?
            ORDER BY i.published_at DESC NULLS LAST, i.item_id
            LIMIT ?
            """,
            [asset_id, limit],
        ).fetchall()
        return [
            NewsItem(
                item_id=r[0], source=r[1], external_id=r[2], url=r[3], headline=r[4],
                summary=r[5], body=r[6], published_at=r[7], source_name=r[8], category=r[9],
            )
            for r in rows
        ]

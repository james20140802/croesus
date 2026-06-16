"""
Job-run history and data-freshness state for the local scheduler (Sprint 006b).

This module turns "is today's data trustworthy?" into a queryable product state
rather than text printed after a command. Two pieces:

  - ``RunStatusRepository`` reads/writes ``job_runs`` and ``data_freshness``.
  - ``evaluate_freshness`` is a pure function mapping (now, latest data date,
    latest success time, staleness threshold) -> (status, reason).

Freshness is tied to recorded *successful job runs*, not just the presence of
data, so the dashboard can answer "did the pipeline actually refresh this?".
All timestamps are normalised to UTC; DuckDB ``TIMESTAMP`` columns are naive, so
values are stored as naive-UTC and re-tagged as UTC on read.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable

import duckdb

# ── Freshness status values (stable, part of the product contract) ───────────
STATUS_FRESH = "fresh"
STATUS_STALE = "stale"
STATUS_MISSING = "missing"

# ── Job-run status values ────────────────────────────────────────────────────
RUN_SUCCESS = "success"
RUN_FAILED = "failed"
RUN_SKIPPED = "skipped"


def _utc(dt: datetime | None) -> datetime | None:
    """Tag a naive datetime as UTC, or convert an aware one to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _naive_utc(dt: datetime | None) -> datetime | None:
    """Render a datetime as naive-UTC for storage in a DuckDB TIMESTAMP column."""
    aware = _utc(dt)
    return aware.replace(tzinfo=None) if aware is not None else None


def _scalar_date(conn: duckdb.DuckDBPyConnection, sql: str) -> date | None:
    row = conn.execute(sql).fetchone()
    value = row[0] if row else None
    if isinstance(value, datetime):
        return value.date()
    return value


def _latest_screening_date(conn: duckdb.DuckDBPyConnection) -> date | None:
    """Parse the as-of date out of the newest screening run id.

    Screening rows carry no date column; the run id is ``screening-YYYY-MM-DD-<hex>``
    (see ``run_screening``). Lexicographic MAX picks the newest because the date
    is ISO-formatted. Returns None if the id does not match the expected shape.
    """
    row = conn.execute("SELECT MAX(run_id) FROM screening_results").fetchone()
    run_id = row[0] if row else None
    if not run_id:
        return None
    parts = run_id.split("-")
    # ["screening", "YYYY", "MM", "DD", "<hex>"]
    if len(parts) >= 4:
        try:
            return date.fromisoformat("-".join(parts[1:4]))
        except ValueError:
            return None
    return None


@dataclass(frozen=True)
class DomainSpec:
    """How to derive freshness for one data domain."""

    domain: str
    job_name: str
    stale_after_hours: float
    data_date_fn: Callable[[duckdb.DuckDBPyConnection], date | None]


# Ordered registry of data domains. ``job_name`` is the job whose latest
# successful run feeds ``latest_success_at``; ``data_date_fn`` reads the newest
# data date from the relevant source table. Thresholds are deterministic and err
# generous enough to absorb weekends/holidays for daily market data.
DOMAIN_REGISTRY: tuple[DomainSpec, ...] = (
    DomainSpec(
        "prices", "daily_run", 48.0,
        lambda c: _scalar_date(c, "SELECT MAX(date) FROM prices_daily"),
    ),
    DomainSpec(
        "fx", "daily_run", 48.0,
        lambda c: _scalar_date(c, "SELECT MAX(date) FROM fx_rates"),
    ),
    DomainSpec(
        "macro_daily", "daily_macro_run", 36.0,
        lambda c: _scalar_date(c, "SELECT MAX(date) FROM macro_scores"),
    ),
    DomainSpec(
        "macro_weekly", "weekly_macro_run", 24.0 * 8,
        lambda c: _scalar_date(c, "SELECT MAX(date) FROM macro_scores"),
    ),
    DomainSpec(
        "macro_monthly", "monthly_macro_run", 24.0 * 40,
        lambda c: _scalar_date(c, "SELECT MAX(date) FROM macro_scores"),
    ),
    DomainSpec(
        "portfolio_snapshot", "portfolio_snapshot", 48.0,
        lambda c: _scalar_date(c, "SELECT MAX(as_of_date) FROM portfolio_snapshots"),
    ),
    DomainSpec(
        "screening", "screening_run", 24.0 * 8,
        _latest_screening_date,
    ),
    DomainSpec(
        "rebalance_report", "rebalance_check", 24.0 * 8,
        lambda c: _scalar_date(c, "SELECT MAX(date) FROM rebalance_runs"),
    ),
    # Financial statements change on a quarterly filing cadence; ~92 days keeps
    # the DCF refreshed once per reporting season without daily refetches.
    DomainSpec(
        "fundamentals", "quarterly_run", 24.0 * 92,
        lambda c: _scalar_date(c, "SELECT MAX(period_end) FROM fundamentals"),
    ),
    DomainSpec(
        "performance", "performance_check", 48.0,
        lambda c: _scalar_date(
            c, "SELECT MAX(as_of_date) FROM portfolio_performance_snapshots"
        ),
    ),
    # Index constituents drift slowly; a weekly refresh tracks additions and
    # removals closely enough. The assets table has no timestamp column, so the
    # data date is the last successful refresh recorded in job_runs.
    DomainSpec(
        "asset_universe", "universe_refresh", 24.0 * 8,
        lambda c: _scalar_date(
            c,
            "SELECT MAX(finished_at) FROM job_runs "
            "WHERE job_name = 'universe_refresh' AND status = 'success'",
        ),
    ),
    # SEC filings arrive irregularly (quarterly 10-K/10-Q plus event-driven
    # 8-Ks). A ~daily refresh threshold keeps new 8-Ks flowing into the event
    # funnel promptly; MAX(filed_date) lags over weekends/holidays, which simply
    # marks the domain due and triggers a (cheap, mostly no-op) refresh.
    DomainSpec(
        "disclosures", "disclosures_run", 48.0,
        lambda c: _scalar_date(c, "SELECT MAX(filed_date) FROM disclosures"),
    ),
)

DOMAINS_BY_NAME: dict[str, DomainSpec] = {spec.domain: spec for spec in DOMAIN_REGISTRY}


@dataclass(frozen=True)
class FreshnessState:
    domain: str
    status: str
    latest_data_date: date | None
    latest_success_at: datetime | None
    stale_after_hours: float
    reason: str

    @property
    def is_due(self) -> bool:
        """True when this domain should be refreshed (stale or never run)."""
        return self.status != STATUS_FRESH


def evaluate_freshness(
    now: datetime,
    latest_data_date: date | None,
    latest_success_at: datetime | None,
    stale_after_hours: float,
) -> tuple[str, str]:
    """Pure freshness classification. Returns (status, human-readable reason)."""
    if latest_success_at is None:
        if latest_data_date is None:
            return STATUS_MISSING, "no data and no successful run recorded"
        return STATUS_MISSING, "data present but no successful run recorded"

    now = _utc(now)
    success = _utc(latest_success_at)
    age_hours = (now - success).total_seconds() / 3600.0
    if age_hours <= stale_after_hours:
        return STATUS_FRESH, f"updated {age_hours:.1f}h ago"
    return (
        STATUS_STALE,
        f"last success {age_hours:.1f}h ago exceeds {stale_after_hours:.0f}h threshold",
    )


class RunStatusRepository:
    """Persistence for ``job_runs`` and ``data_freshness``."""

    def __init__(self, conn: duckdb.DuckDBPyConnection) -> None:
        self.conn = conn

    # ── job_runs ─────────────────────────────────────────────────────────────
    def record_job_run(
        self,
        *,
        run_id: str,
        job_name: str,
        started_at: datetime,
        finished_at: datetime,
        status: str,
        summary: str | None = None,
        error: str | None = None,
        metadata: dict | None = None,
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO job_runs
                (run_id, job_name, started_at, finished_at, status, summary, error, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                run_id,
                job_name,
                _naive_utc(started_at),
                _naive_utc(finished_at),
                status,
                summary,
                error,
                json.dumps(metadata) if metadata is not None else None,
            ],
        )

    def latest_success_at(self, job_name: str) -> datetime | None:
        row = self.conn.execute(
            """
            SELECT MAX(finished_at)
            FROM job_runs
            WHERE job_name = ? AND status = ?
            """,
            [job_name, RUN_SUCCESS],
        ).fetchone()
        return _utc(row[0]) if row and row[0] is not None else None

    def recent_job_runs(self, job_name: str | None = None, limit: int = 50) -> list[dict]:
        if job_name is not None:
            rows = self.conn.execute(
                """
                SELECT run_id, job_name, started_at, finished_at, status, summary, error
                FROM job_runs
                WHERE job_name = ?
                ORDER BY finished_at DESC NULLS LAST
                LIMIT ?
                """,
                [job_name, limit],
            ).fetchall()
        else:
            rows = self.conn.execute(
                """
                SELECT run_id, job_name, started_at, finished_at, status, summary, error
                FROM job_runs
                ORDER BY finished_at DESC NULLS LAST
                LIMIT ?
                """,
                [limit],
            ).fetchall()
        return [
            {
                "run_id": r[0],
                "job_name": r[1],
                "started_at": _utc(r[2]),
                "finished_at": _utc(r[3]),
                "status": r[4],
                "summary": r[5],
                "error": r[6],
            }
            for r in rows
        ]

    # ── data_freshness ───────────────────────────────────────────────────────
    def compute_freshness(self, now: datetime) -> list[FreshnessState]:
        """Derive (but do not persist) freshness for every registered domain."""
        states: list[FreshnessState] = []
        for spec in DOMAIN_REGISTRY:
            latest_data_date = spec.data_date_fn(self.conn)
            latest_success_at = self.latest_success_at(spec.job_name)
            status, reason = evaluate_freshness(
                now, latest_data_date, latest_success_at, spec.stale_after_hours
            )
            states.append(
                FreshnessState(
                    domain=spec.domain,
                    status=status,
                    latest_data_date=latest_data_date,
                    latest_success_at=latest_success_at,
                    stale_after_hours=spec.stale_after_hours,
                    reason=reason,
                )
            )
        return states

    def upsert_freshness(self, states: list[FreshnessState]) -> None:
        for s in states:
            self.conn.execute(
                """
                INSERT INTO data_freshness
                    (data_domain, latest_data_date, latest_success_at,
                     stale_after_hours, status, reason, metadata)
                VALUES (?, ?, ?, ?, ?, ?, NULL)
                ON CONFLICT (data_domain) DO UPDATE SET
                    latest_data_date = EXCLUDED.latest_data_date,
                    latest_success_at = EXCLUDED.latest_success_at,
                    stale_after_hours = EXCLUDED.stale_after_hours,
                    status = EXCLUDED.status,
                    reason = EXCLUDED.reason
                """,
                [
                    s.domain,
                    s.latest_data_date,
                    _naive_utc(s.latest_success_at),
                    s.stale_after_hours,
                    s.status,
                    s.reason,
                ],
            )

    def refresh_freshness(self, now: datetime) -> list[FreshnessState]:
        """Compute, persist, and return freshness for all domains."""
        states = self.compute_freshness(now)
        self.upsert_freshness(states)
        return states

    def get_freshness(self) -> list[FreshnessState]:
        """Read the persisted freshness table, ordered by the registry."""
        rows = self.conn.execute(
            """
            SELECT data_domain, latest_data_date, latest_success_at,
                   stale_after_hours, status, reason
            FROM data_freshness
            """
        ).fetchall()
        by_domain = {
            r[0]: FreshnessState(
                domain=r[0],
                status=r[4],
                latest_data_date=r[1].date() if isinstance(r[1], datetime) else r[1],
                latest_success_at=_utc(r[2]),
                stale_after_hours=r[3],
                reason=r[5],
            )
            for r in rows
        }
        # Preserve registry order; include only domains that have been persisted.
        return [by_domain[spec.domain] for spec in DOMAIN_REGISTRY if spec.domain in by_domain]
